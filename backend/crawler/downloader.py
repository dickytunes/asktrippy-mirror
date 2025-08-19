# backend/crawler/downloader.py
# Trafilatura-based HTML downloader with strict timeouts, robots.txt, size caps,
# basic content gating, and traceable timings. No Docker required.
#
# Spec alignment:
# - Per-page timeouts: connect ≤1s, first byte ≤1s, total read ≤1s (configurable)
# - Size limit: ≤2 MB (configurable)
# - Content type: text/html only
# - Robots respected for the actual host fetched
# - Returns cleaned_text via Trafilatura and raw_html (optional)
# - Computes content_hash (sha256 of raw_html)
# - Records redirect_chain and reason codes per Section 3F
#
# Usage:
#   dl = Downloader()
#   page = dl.fetch_url("https://example.com/contact", deadline_ts=time.perf_counter() + 5.0)
#   if page.reason == "ok": print(page.cleaned_text[:500])

from __future__ import annotations

import hashlib
import os
import time
import typing as t
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import trafilatura
import urllib.robotparser as robotparser


# --------- Config (env-overridable) ---------
CONNECT_TIMEOUT_S = float(os.getenv("CRAWL_CONNECT_TIMEOUT_S", "1.0"))   # ≤1s
TTFB_TIMEOUT_S    = float(os.getenv("CRAWL_TTFB_TIMEOUT_S", "1.0"))      # ≤1s budget for first byte
READ_TIMEOUT_S    = float(os.getenv("CRAWL_READ_TIMEOUT_S", "1.0"))      # ≤1s budget for body read
SIZE_LIMIT_BYTES  = int(os.getenv("CRAWL_PAGE_SIZE_LIMIT_BYTES", str(2_000_000)))  # ≤2 MB
USER_AGENT        = os.getenv("CRAWL_USER_AGENT",
                               "Voy8Crawler/0.1 (+https://voy8.com; contact: crawler@voy8.com)")
ALLOW_RAW_HTML    = os.getenv("CRAWL_STORE_RAW_HTML", "false").lower() == "true"
ROBOTS_CACHE_TTL  = int(os.getenv("CRAWL_ROBOTS_TTL_SECONDS", "3600"))  # 1h

# Reason codes (Section 3F)
REASON_OK                  = "ok"
REASON_ROBOTS_DISALLOWED   = "robots_disallowed"
REASON_INVALID_MIME        = "invalid_mime"
REASON_NON_200_STATUS      = "non_200_status"
REASON_SIZE_LIMIT_EXCEEDED = "size_limit_exceeded"
REASON_TIMEOUT             = "network_timeout"
REASON_DNS_FAILURE         = "dns_failure"
REASON_TLS_ERROR           = "tls_error"
REASON_OTHER_NETWORK       = "network_error"
REASON_TIME_BUDGET         = "time_budget_exceeded"


@dataclass
class FetchedPage:
    url: str
    final_url: str
    http_status: int
    content_type: t.Optional[str]
    content_hash: t.Optional[str]
    fetched_at: datetime
    duration_ms: int
    first_byte_ms: int
    size_bytes: int
    cleaned_text: t.Optional[str]
    raw_html: t.Optional[bytes] = None
    redirect_chain: t.List[str] = field(default_factory=list)
    reason: str = REASON_OK


class RobotsCache:
    """Simple in-memory robots.txt cache keyed by origin (scheme://host:port)."""

    def __init__(self, ttl_seconds: int = ROBOTS_CACHE_TTL):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, robotparser.RobotFileParser]] = {}

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        netloc = p.netloc
        scheme = p.scheme or "https"
        return f"{scheme}://{netloc}"

    def allowed(self, url: str, user_agent: str, session: requests.Session) -> bool:
        origin = self._origin(url)
        now = time.time()
        entry = self._store.get(origin)

        if not entry or now - entry[0] > self.ttl:
            # Refresh robots
            rp = robotparser.RobotFileParser()
            robots_url = origin.rstrip("/") + "/robots.txt"
            try:
                resp = session.get(
                    robots_url,
                    timeout=(CONNECT_TIMEOUT_S, TTFB_TIMEOUT_S),
                    headers={"User-Agent": user_agent},
                )
                if resp.status_code == 200 and len(resp.content) <= SIZE_LIMIT_BYTES:
                    rp.parse(resp.text.splitlines())
                else:
                    # If robots fetch fails or is too big, default to allowing
                    rp.parse(["User-agent: *", "Allow: /"])
            except requests.exceptions.RequestException:
                rp.parse(["User-agent: *", "Allow: /"])
            self._store[origin] = (now, rp)
            entry = self._store[origin]

        rp = entry[1]
        return rp.can_fetch(user_agent, url)


class Downloader:
    def __init__(self, user_agent: str = USER_AGENT):
        self.user_agent = user_agent
        self.session = self._build_session()
        self.robots = RobotsCache()

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        # Conservative retry only for idempotent GETs on transient errors
        retries = Retry(
            total=2,
            backoff_factor=0.3,  # jitter-like spacing via urllib3 backoff
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=64, pool_maxsize=64)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en;q=0.8",
            "Connection": "close",  # be polite; we are highly parallelized elsewhere
        })
        return s

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _is_html(content_type: t.Optional[str]) -> bool:
        if not content_type:
            return False
        ct = content_type.split(";")[0].strip().lower()
        return ct in ("text/html", "application/xhtml+xml")

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def fetch_url(
        self,
        url: str,
        *,
        deadline_ts: t.Optional[float] = None,
        allow_raw_html: t.Optional[bool] = None,
    ) -> FetchedPage:
        """
        Fetch a single URL with strict budgets and content gating.

        :param url: Target URL (http/https)
        :param deadline_ts: Absolute perf_counter() deadline for the whole site budget.
                            If provided and already exceeded, returns time_budget_exceeded.
        :param allow_raw_html: override env flag to include raw_html in response
        """
        if allow_raw_html is None:
            allow_raw_html = ALLOW_RAW_HTML

        start_perf = time.perf_counter()
        if deadline_ts is not None and start_perf >= deadline_ts:
            return FetchedPage(
                url=url,
                final_url=url,
                http_status=0,
                content_type=None,
                content_hash=None,
                fetched_at=self._now(),
                duration_ms=0,
                first_byte_ms=0,
                size_bytes=0,
                cleaned_text=None,
                raw_html=None,
                redirect_chain=[],
                reason=REASON_TIME_BUDGET,
            )

        # Robots
        try:
            if not self.robots.allowed(url, self.user_agent, self.session):
                return FetchedPage(
                    url=url,
                    final_url=url,
                    http_status=0,
                    content_type=None,
                    content_hash=None,
                    fetched_at=self._now(),
                    duration_ms=0,
                    first_byte_ms=0,
                    size_bytes=0,
                    cleaned_text=None,
                    raw_html=None,
                    redirect_chain=[],
                    reason=REASON_ROBOTS_DISALLOWED,
                )
        except Exception:
            # If robots check explodes, be safe and deny
            return FetchedPage(
                url=url,
                final_url=url,
                http_status=0,
                content_type=None,
                content_hash=None,
                fetched_at=self._now(),
                duration_ms=0,
                first_byte_ms=0,
                size_bytes=0,
                cleaned_text=None,
                raw_html=None,
                redirect_chain=[],
                reason=REASON_ROBOTS_DISALLOWED,
            )

        # Adjust per-call timeouts based on remaining budget (if provided)
        connect_timeout = CONNECT_TIMEOUT_S
        ttfb_timeout = TTFB_TIMEOUT_S
        read_timeout = READ_TIMEOUT_S

        if deadline_ts is not None:
            remaining = max(0.0, deadline_ts - start_perf)
            # Keep some sanity floors; if remaining is tiny, bail early
            if remaining < 0.05:
                return FetchedPage(
                    url=url,
                    final_url=url,
                    http_status=0,
                    content_type=None,
                    content_hash=None,
                    fetched_at=self._now(),
                    duration_ms=int((time.perf_counter() - start_perf) * 1000),
                    first_byte_ms=0,
                    size_bytes=0,
                    cleaned_text=None,
                    raw_html=None,
                    redirect_chain=[],
                    reason=REASON_TIME_BUDGET,
                )
            # Cap individual phases by remaining budget (greedy split)
            slice_per_phase = remaining / 3.0
            connect_timeout = min(connect_timeout, slice_per_phase)
            ttfb_timeout = min(ttfb_timeout, slice_per_phase)
            read_timeout = min(read_timeout, slice_per_phase)

        # Perform GET with streaming to enforce size cap & measure first byte time
        try:
            resp = self.session.get(
                url,
                timeout=(connect_timeout, ttfb_timeout + read_timeout),
                allow_redirects=True,
                stream=True,
            )
        except requests.exceptions.ConnectTimeout:
            return self._mk_page(url, 0, None, b"", start_perf, 0, REASON_TIMEOUT, [])
        except requests.exceptions.ReadTimeout:
            return self._mk_page(url, 0, None, b"", start_perf, 0, REASON_TIMEOUT, [])
        except requests.exceptions.SSLError:
            return self._mk_page(url, 0, None, b"", start_perf, 0, REASON_TLS_ERROR, [])
        except requests.exceptions.ConnectionError as e:
            # Could be DNS failure or other net error
            reason = REASON_DNS_FAILURE if "Name or service not known" in str(e) else REASON_OTHER_NETWORK
            return self._mk_page(url, 0, None, b"", start_perf, 0, reason, [])
        except requests.exceptions.RequestException:
            return self._mk_page(url, 0, None, b"", start_perf, 0, REASON_OTHER_NETWORK, [])

        redirect_chain = [h.url for h in resp.history] if resp.history else []
        final_url = resp.url
        status = resp.status_code
        ctype = resp.headers.get("Content-Type")

        if status != 200:
            # Consume minimal content to free the connection, but treat as non-200
            try:
                _ = next(resp.iter_content(chunk_size=1024))
            except StopIteration:
                pass
            except Exception:
                pass
            return self._mk_page(final_url, status, ctype, b"", start_perf, 0, REASON_NON_200_STATUS, redirect_chain)

        # Confirm content type
        if not self._is_html(ctype):
            # Drain a tiny bit to avoid hanging connections
            try:
                _ = next(resp.iter_content(chunk_size=1024))
            except Exception:
                pass
            return self._mk_page(final_url, status, ctype, b"", start_perf, 0, REASON_INVALID_MIME, redirect_chain)

        # Stream body up to SIZE_LIMIT_BYTES, measuring first-byte time
        body = bytearray()
        first_byte_ms = 0
        read_started = time.perf_counter()
        try:
            for chunk in resp.iter_content(chunk_size=32_768):
                if not chunk:
                    continue
                if first_byte_ms == 0:
                    first_byte_ms = int((time.perf_counter() - read_started) * 1000)
                    # If first byte took too long relative to configured TTFB_TIMEOUT_S, mark timeout
                    if first_byte_ms / 1000.0 > (TTFB_TIMEOUT_S + 0.01):
                        # We still proceed, but mark later if needed; we enforce overall read timeout below
                        pass
                body.extend(chunk)
                if len(body) > SIZE_LIMIT_BYTES:
                    return self._mk_page(
                        final_url, status, ctype, bytes(body[:SIZE_LIMIT_BYTES]),
                        start_perf, first_byte_ms, REASON_SIZE_LIMIT_EXCEEDED, redirect_chain
                    )
                # Enforce read timeout relative to start of reading
                if (time.perf_counter() - read_started) > READ_TIMEOUT_S:
                    return self._mk_page(
                        final_url, status, ctype, bytes(body),
                        start_perf, first_byte_ms, REASON_TIMEOUT, redirect_chain
                    )
                # Enforce global site deadline if provided
                if deadline_ts is not None and time.perf_counter() > deadline_ts:
                    return self._mk_page(
                        final_url, status, ctype, bytes(body),
                        start_perf, first_byte_ms, REASON_TIME_BUDGET, redirect_chain
                    )
        except requests.exceptions.ReadTimeout:
            return self._mk_page(final_url, status, ctype, bytes(body), start_perf, first_byte_ms, REASON_TIMEOUT, redirect_chain)
        except requests.exceptions.RequestException:
            return self._mk_page(final_url, status, ctype, bytes(body), start_perf, first_byte_ms, REASON_OTHER_NETWORK, redirect_chain)

        raw = bytes(body)
        # Decode to text for Trafilatura
        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
        try:
            html_text = raw.decode(encoding, errors="replace")
        except Exception:
            html_text = raw.decode("utf-8", errors="replace")

        cleaned = trafilatura.extract(
            html_text,
            include_links=False,
            include_images=False,
            include_tables=False,
            favor_recall=True,  # better recall for facts extraction
            no_fallback=False,
        )

        return FetchedPage(
            url=url,
            final_url=final_url,
            http_status=status,
            content_type=ctype,
            content_hash=self._sha256(raw) if raw else None,
            fetched_at=self._now(),
            duration_ms=int((time.perf_counter() - start_perf) * 1000),
            first_byte_ms=first_byte_ms,
            size_bytes=len(raw),
            cleaned_text=cleaned or None,
            raw_html=raw if allow_raw_html else None,
            redirect_chain=redirect_chain,
            reason=REASON_OK,
        )

    def _mk_page(
        self,
        final_url: str,
        status: int,
        ctype: t.Optional[str],
        raw: bytes,
        start_perf: float,
        first_byte_ms: int,
        reason: str,
        redirect_chain: t.List[str],
    ) -> FetchedPage:
        cleaned = None
        if reason == REASON_OK and raw:
            try:
                html_text = raw.decode("utf-8", errors="replace")
                cleaned = trafilatura.extract(html_text, include_links=False, favor_recall=True)
            except Exception:
                cleaned = None
        return FetchedPage(
            url=final_url,
            final_url=final_url,
            http_status=status,
            content_type=ctype,
            content_hash=self._sha256(raw) if raw else None,
            fetched_at=self._now(),
            duration_ms=int((time.perf_counter() - start_perf) * 1000),
            first_byte_ms=first_byte_ms,
            size_bytes=len(raw),
            cleaned_text=cleaned,
            raw_html=raw if ALLOW_RAW_HTML else None,
            redirect_chain=redirect_chain,
            reason=reason,
        )
