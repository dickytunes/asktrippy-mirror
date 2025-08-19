# backend/crawler/pipeline.py
# Site-level crawl orchestrator for Voy8.
#
# Flow:
#   1) Fetch homepage (deadline-aware; allow_raw_html for link discovery)
#   2) Discover up to 3 same-site targets: /hours|opening, /menu|food|drinks, /contact, /about, /fees|tickets|visit
#   3) Fetch targets in parallel within remaining site budget
#   4) Quality-gate pages (visible text length), assign per-page TTL, and return a bundle for persistence
#
# Spec alignment:
# - ≤ 5s per-site wall clock (deadline respected end-to-end)
# - Max 3 target pages (same-site only, prioritized order)
# - Per-page constraints enforced by Downloader (timeouts, size caps, robots)
# - Quality gates: text/html, HTTP 200, visible text ≥ MIN_VISIBLE_CHARS
# - Traceability: redirect_chain, content_hash, reason codes from Section 3F
# - TTLs per page_type: hours 3d; menu/contact/fees 14d; homepage/about 30d
#
# Usage:
#   from backend.crawler.downloader import Downloader
#   from backend.crawler.pipeline import CrawlPipeline
#
#   pipeline = CrawlPipeline()
#   result = pipeline.crawl_site("https://example.com", deadline_ms=5000)
#   for p in result.pages:
#       print(p.page_type, p.url, p.http_status, p.reason, len(p.cleaned_text or ""))
#
# Persist `PageRecord.to_scraped_pages_row()` rows into `scraped_pages`.

from __future__ import annotations

import os
import time
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict

from .downloader import (
    Downloader,
    FetchedPage,
    REASON_OK,
    REASON_TIME_BUDGET,
    REASON_INVALID_MIME,
    REASON_NON_200_STATUS,
    REASON_SIZE_LIMIT_EXCEEDED,
    REASON_TIMEOUT,
    REASON_DNS_FAILURE,
    REASON_TLS_ERROR,
    REASON_OTHER_NETWORK,
    REASON_ROBOTS_DISALLOWED,
)
from .link_finder import LinkFinder, CandidateLink, TARGET_ORDER


# ----------------- Config (env-overridable) -----------------
DEFAULT_SITE_BUDGET_MS = int(os.getenv("CRAWL_BUDGET_MS", "5000"))  # ≤ 5s wall clock
MIN_VISIBLE_CHARS = int(os.getenv("CRAWL_MIN_VISIBLE_CHARS", "200"))

FRESH_HOURS_DAYS = int(os.getenv("FRESH_HOURS_DAYS", "3"))
FRESH_MENU_CONTACT_PRICE_DAYS = int(os.getenv("FRESH_MENU_CONTACT_PRICE_DAYS", "14"))
FRESH_DESC_FEATURES_DAYS = int(os.getenv("FRESH_DESC_FEATURES_DAYS", "30"))  # homepage/about bucket

# ----------------- Data models -----------------
@dataclass
class PageRecord:
    """Fields aligned with `scraped_pages` (plus a few transient fields for QA)."""
    fsq_place_id: Optional[str]  # may be None if not known at crawl time
    url: str
    page_type: str  # enum-ish: homepage, hours, menu, contact, about, fees, other
    fetched_at: datetime
    valid_until: Optional[datetime]
    http_status: int
    content_type: Optional[str]
    content_hash: Optional[str]
    cleaned_text: Optional[str]
    size_bytes: int
    source_method: str  # direct_url | search_api | heuristic
    redirect_chain: List[str] = field(default_factory=list)
    reason: str = REASON_OK  # Section 3F reason codes (+ "thin_content")
    duration_ms: int = 0
    first_byte_ms: int = 0

    def to_scraped_pages_row(self) -> Dict:
        """Map to DB row dict for `scraped_pages` insert."""
        return {
            "fsq_place_id": self.fsq_place_id,
            "url": self.url,
            "page_type": self.page_type,
            "fetched_at": self.fetched_at,
            "valid_until": self.valid_until,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "cleaned_text": self.cleaned_text,
            # raw_html not persisted here (optional column in spec); use downloader if you store it
            "source_method": self.source_method,
            "redirect_chain": json.dumps(self.redirect_chain or []),
            "reason": self.reason,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
            "first_byte_ms": self.first_byte_ms,
        }


@dataclass
class CrawlResult:
    base_url: str
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    pages: List[PageRecord]
    fetched_count: int
    aborted_count: int  # time_budget_exceeded or read timeouts
    errors_by_class: Dict[str, int]


# ----------------- Helpers -----------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_html(raw: Optional[bytes]) -> str:
    if not raw:
        return ""
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("latin1", errors="replace")


def _ttl_for_page_type(page_type: str) -> timedelta:
    # hours: 3d; menu/contact/fees: 14d; homepage/about: 30d
    if page_type == "hours":
        return timedelta(days=FRESH_HOURS_DAYS)
    if page_type in ("menu", "contact", "fees"):
        return timedelta(days=FRESH_MENU_CONTACT_PRICE_DAYS)
    # homepage/about/other -> 30d (desc/features bucket)
    return timedelta(days=FRESH_DESC_FEATURES_DAYS)


def _quality_gate(fp: FetchedPage) -> bool:
    """Return True if page passes minimal quality: 200+ visible chars, html, 200 status."""
    if fp.reason != REASON_OK:
        return False
    if fp.http_status != 200:
        return False
    if not fp.content_type or not (fp.content_type.split(";")[0].lower() in ("text/html", "application/xhtml+xml")):
        return False
    text = (fp.cleaned_text or "").strip()
    return len(text) >= MIN_VISIBLE_CHARS


def _mk_record(
    fp: FetchedPage,
    page_type: str,
    source_method: str,
    fsq_place_id: Optional[str],
    override_reason: Optional[str] = None,
) -> PageRecord:
    reason = override_reason or fp.reason
    return PageRecord(
        fsq_place_id=fsq_place_id,
        url=fp.final_url,
        page_type=page_type,
        fetched_at=fp.fetched_at,
        valid_until=_now() + _ttl_for_page_type(page_type) if reason == REASON_OK and fp.cleaned_text else None,
        http_status=fp.http_status,
        content_type=fp.content_type,
        content_hash=fp.content_hash,
        cleaned_text=fp.cleaned_text if reason == REASON_OK else None,
        size_bytes=fp.size_bytes,
        source_method=source_method,
        redirect_chain=fp.redirect_chain or [],
        reason=reason,
        duration_ms=fp.duration_ms,
        first_byte_ms=fp.first_byte_ms,
    )


# ----------------- Pipeline -----------------
class CrawlPipeline:
    def __init__(self, downloader: Optional[Downloader] = None, link_finder: Optional[LinkFinder] = None):
        self.downloader = downloader or Downloader()
        self.finder = link_finder or LinkFinder()

    def crawl_site(
        self,
        base_url: str,
        *,
        fsq_place_id: Optional[str] = None,
        deadline_ms: Optional[int] = None,
        max_targets: int = 3,
    ) -> CrawlResult:
        """
        Crawl a single site within a strict wall-clock deadline.
        Returns PageRecord entries for homepage and up to `max_targets` target pages.

        - Respects robots via Downloader
        - Aborts gracefully when the site budget is exceeded
        - Applies quality gating to set reasons (e.g., thin_content)
        """
        started = _now()
        start_perf = time.perf_counter()
        budget_ms = deadline_ms if deadline_ms is not None else DEFAULT_SITE_BUDGET_MS
        deadline_ts = start_perf + (budget_ms / 1000.0)

        pages: List[PageRecord] = []
        errors: Dict[str, int] = {}

        # 1) Fetch homepage (allow_raw_html so we can parse links)
        home_fp = self.downloader.fetch_url(base_url, deadline_ts=deadline_ts, allow_raw_html=True)

        # Quality gate for homepage
        if _quality_gate(home_fp):
            home_record = _mk_record(home_fp, "homepage", "direct_url", fsq_place_id)
            home_html = home_fp.raw_html  # only set because allow_raw_html=True
        else:
            # If it failed the gate, set reason accordingly
            reason = home_fp.reason
            if reason == REASON_OK and len((home_fp.cleaned_text or "")) < MIN_VISIBLE_CHARS:
                reason = "thin_content"
            home_record = _mk_record(home_fp, "homepage", "direct_url", fsq_place_id, override_reason=reason)
            home_html = home_fp.raw_html if reason == REASON_OK else None  # still try to use html if network was OK

        pages.append(home_record)

        # Abort early if robots/timeout/dns/etc.
        if home_record.reason in (
            REASON_ROBOTS_DISALLOWED,
            REASON_TIMEOUT,
            REASON_DNS_FAILURE,
            REASON_TLS_ERROR,
            REASON_OTHER_NETWORK,
            REASON_TIME_BUDGET,
        ):
            ended = _now()
            dur_ms = int((time.perf_counter() - start_perf) * 1000)
            errors[home_record.reason] = errors.get(home_record.reason, 0) + 1
            return CrawlResult(
                base_url=base_url,
                started_at=started,
                ended_at=ended,
                duration_ms=dur_ms,
                pages=pages,
                fetched_count=1,
                aborted_count=1 if home_record.reason in (REASON_TIME_BUDGET, REASON_TIMEOUT) else 0,
                errors_by_class=errors,
            )

        # 2) Discover up to `max_targets` same-site targets (hours > menu > contact > about > fees)
        targets: List[CandidateLink] = []
        if home_html:
            targets = self.finder.discover_targets(_decode_html(home_html), base_url, max_targets=max_targets)

        # If nothing found or budget too thin, return homepage only
        if not targets or time.perf_counter() >= deadline_ts:
            ended = _now()
            dur_ms = int((time.perf_counter() - start_perf) * 1000)
            return CrawlResult(
                base_url=base_url,
                started_at=started,
                ended_at=ended,
                duration_ms=dur_ms,
                pages=pages,
                fetched_count=len(pages),
                aborted_count=sum(1 for p in pages if p.reason in (REASON_TIME_BUDGET, REASON_TIMEOUT)),
                errors_by_class=_tally_errors(pages),
            )

        # 3) Fetch targets in parallel, still respecting the shared deadline
        futures = []
        with ThreadPoolExecutor(max_workers=min(len(targets), max_targets)) as ex:
            for cand in targets:
                futures.append((
                    cand,
                    ex.submit(self.downloader.fetch_url, cand.url, deadline_ts=deadline_ts, allow_raw_html=False)
                ))

            for cand, fut in futures:
                try:
                    fp: FetchedPage = fut.result(timeout=max(0.0, deadline_ts - time.perf_counter()))
                except Exception:
                    # Treat any executor/timeout as network timeout
                    fp = FetchedPage(
                        url=cand.url,
                        final_url=cand.url,
                        http_status=0,
                        content_type=None,
                        content_hash=None,
                        fetched_at=_now(),
                        duration_ms=0,
                        first_byte_ms=0,
                        size_bytes=0,
                        cleaned_text=None,
                        raw_html=None,
                        redirect_chain=[],
                        reason=REASON_TIMEOUT,
                    )

                # Quality gate and record creation
                if _quality_gate(fp):
                    rec = _mk_record(fp, cand.page_type, "heuristic", fsq_place_id)
                else:
                    reason = fp.reason
                    if reason == REASON_OK and len((fp.cleaned_text or "")) < MIN_VISIBLE_CHARS:
                        reason = "thin_content"
                    rec = _mk_record(fp, cand.page_type, "heuristic", fsq_place_id, override_reason=reason)

                pages.append(rec)

                # Stop early if we hit the deadline to avoid wasting cycles
                if time.perf_counter() >= deadline_ts:
                    break

        # 4) Summarize
        ended = _now()
        dur_ms = int((time.perf_counter() - start_perf) * 1000)
        return CrawlResult(
            base_url=base_url,
            started_at=started,
            ended_at=ended,
            duration_ms=dur_ms,
            pages=pages,
            fetched_count=sum(1 for p in pages if p.http_status == 200),
            aborted_count=sum(1 for p in pages if p.reason in (REASON_TIME_BUDGET, REASON_TIMEOUT)),
            errors_by_class=_tally_errors(pages),
        )


def _tally_errors(pages: List[PageRecord]) -> Dict[str, int]:
    tally: Dict[str, int] = {}
    for p in pages:
        if p.reason and p.reason != REASON_OK:
            tally[p.reason] = tally.get(p.reason, 0) + 1
        if p.http_status and p.http_status != 200 and p.reason == REASON_OK:
            tally[REASON_NON_200_STATUS] = tally.get(REASON_NON_200_STATUS, 0) + 1
        if p.content_type and not (p.content_type.split(";")[0].lower() in ("text/html", "application/xhtml+xml")):
            tally[REASON_INVALID_MIME] = tally.get(REASON_INVALID_MIME, 0) + 1
        if p.size_bytes and p.size_bytes > int(os.getenv("CRAWL_PAGE_SIZE_LIMIT_BYTES", "2000000")):
            tally[REASON_SIZE_LIMIT_EXCEEDED] = tally.get(REASON_SIZE_LIMIT_EXCEEDED, 0) + 1
    return tally
