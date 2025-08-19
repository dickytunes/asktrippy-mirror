# backend/crawler/link_finder.py
# Same-site target page discovery for Voy8 crawler.
#
# Spec alignment:
# - Strict same-site rule (registrable domain / eTLD+1), http/https only
# - Targets: hours|opening, menu|food|drinks, contact, about, fees|tickets|visit
# - Up to 3 targets, prioritized in that exact order
# - No off-domain fetching (we only *discover* links here)
# - Dedup by normalized URL; strip common tracking params
# - Lightweight i18n: common EU-language synonyms
#
# Usage:
#   finder = LinkFinder()
#   links = finder.discover_targets(html_text, base_url, max_targets=3)
#   for c in links: print(c.page_type, c.url, c.confidence, c.reason)

from __future__ import annotations

import re
import typing as t
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

from bs4 import BeautifulSoup

# Optional, better eTLD+1. If unavailable, we use a conservative fallback.
try:
    import tldextract  # type: ignore
    _TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=None)
except Exception:  # pragma: no cover
    _TLD_EXTRACT = None


@dataclass(frozen=True)
class CandidateLink:
    url: str
    page_type: str  # one of: hours, menu, contact, about, fees
    confidence: float  # 0..1
    anchor_text: str
    reason: str  # brief: which signals matched (e.g., "url:menu, text:food")


TARGET_ORDER = ["hours", "menu", "contact", "about", "fees"]

# Lightweight multilingual signal lists (expand as needed)
KW = {
    "hours": [
        "hours", "opening", "open", "times", "today",  # en
        "heures", "horaires",                           # fr
        "horario", "abierto",                           # es
        "orari", "apertura",                            # it
        "öffnungszeiten", "geöffnet",                   # de
        "uur", "openingstijden",                        # nl
        "godziny", "otwarte",                           # pl
        "horário",                                      # pt
    ],
    "menu": [
        "menu", "food", "drink", "drinks", "lunch", "dinner",
        "menú", "carta",            # es
        "carte", "menu du jour",    # fr
        "speisekarte",              # de
        "menù", "cucina",           # it
        "menukaart",                # nl
        "jälist", "juomat",         # fi (rough)
    ],
    "contact": [
        "contact", "contact-us", "get-in-touch", "enquiries", "inquiries",
        "kontakt", "contatto", "contacto", "contattarci", "kontaktieren",
        "impressum",  # de legal/contact
    ],
    "about": [
        "about", "about-us", "our-story", "who-we-are",
        "a-propos", "über", "chi-siamo", "sobre", "sobre-nosotros",
        "om-oss", "over-ons",
    ],
    "fees": [
        "fees", "tickets", "pricing", "prices", "admission", "visit",
        "tarifs", "billets",  # fr
        "prezzi", "biglietti",  # it
        "precios", "entradas",  # es
        "preise", "tickets",    # de
    ],
}

NEG_KW = [
    "privacy", "terms", "cookies", "careers", "jobs", "press", "news",
    "login", "signin", "account", "admin", "wp-admin", "cart", "checkout",
    "partners", "media", "newsletter", "blog", "events", "gift-card",
]

ALLOWED_SCHEMES = {"http", "https"}


def registrable_domain(url: str) -> str:
    """Return eTLD+1 (registrable domain). Falls back to a heuristic if tldextract is absent."""
    p = urlparse(url)
    host = p.hostname or ""
    if _TLD_EXTRACT:
        ext = _TLD_EXTRACT(host)
        if ext.registered_domain:
            return ext.registered_domain.lower()
        return host.lower()
    # Heuristic fallback for common multi-part TLDs
    parts = host.lower().split(".")
    if len(parts) <= 2:
        return host.lower()
    multi_tlds = (
        "co.uk", "org.uk", "ac.uk",
        "com.au", "net.au", "org.au",
        "co.nz", "org.nz",
        "com.br", "com.mx", "com.tr",
    )
    last_two = ".".join(parts[-2:])
    last_three = ".".join(parts[-3:])
    if last_three in multi_tlds:
        return ".".join(parts[-4:])  # e.g., foo.bar.co.uk -> bar.co.uk (keep 3+1? conservative)
    return last_two


def is_same_site(base_url: str, target_url: str) -> bool:
    """True if target has same registrable domain as base and uses http/https."""
    bp, tp = urlparse(base_url), urlparse(target_url)
    if (tp.scheme or "http").lower() not in ALLOWED_SCHEMES:
        return False
    return registrable_domain(base_url) == registrable_domain(target_url)


def strip_tracking_params(url: str) -> str:
    """Remove common tracking params and fragments to normalize."""
    p = urlparse(url)
    q = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_eid", "mc_cid"))]
    clean = p._replace(query=urlencode(q, doseq=True), fragment="")
    return urlunparse(clean)


def _contains_any(text: str, toks: t.Sequence[str]) -> bool:
    tl = text.lower()
    return any(tok in tl for tok in toks)


def classify(page_url_path: str, anchor_text: str) -> tuple[str | None, float, str]:
    """
    Classify a link into one of the target types with a confidence score and reason.
    Signals:
      - URL path tokens
      - Anchor text tokens
      - Section weight (nav/header/footer)
    """
    url_l = page_url_path.lower()
    text_l = (anchor_text or "").lower()

    scores: dict[str, float] = {k: 0.0 for k in KW.keys()}
    reasons: dict[str, list[str]] = {k: [] for k in KW.keys()}

    # Negative signals early exit
    if _contains_any(url_l, NEG_KW) or _contains_any(text_l, NEG_KW):
        return None, 0.0, ""

    # URL path tokens
    for ttype, toks in KW.items():
        for tok in toks:
            if re.search(rf"[\W_/|-]{re.escape(tok)}[\W_/|-]", f"/{url_l}/"):
                scores[ttype] += 0.6
                reasons[ttype].append(f"url:{tok}")

    # Anchor text tokens (softer)
    for ttype, toks in KW.items():
        for tok in toks:
            if tok in text_l:
                scores[ttype] += 0.4
                reasons[ttype].append(f"text:{tok}")

    # Pick the best class
    best_type = None
    best_score = 0.0
    for ttype in TARGET_ORDER:
        if scores[ttype] > best_score:
            best_type = ttype
            best_score = scores[ttype]

    if not best_type or best_score <= 0.0:
        return None, 0.0, ""

    # Cap score to 1.0
    return best_type, min(1.0, best_score), ",".join(reasons[best_type][:4])


def _section_weight(a_tag) -> float:
    """Crude boost if the link sits in nav/header/footer."""
    weight = 0.0
    for parent in a_tag.parents:
        if not getattr(parent, "name", None):
            continue
        name = parent.name.lower()
        classes = " ".join((parent.get("class") or []))
        pid = parent.get("id") or ""
        blob = f"{name} {classes} {pid}".lower()
        if "nav" in name or "header" in name:
            weight += 0.15
        if "footer" in name:
            weight += 0.05
        if any(k in blob for k in ("menu", "main-nav", "site-nav", "top-bar", "masthead")):
            weight += 0.1
        # Limit walk for speed
        if name in ("body", "main") or len(blob) > 300:
            break
    return min(weight, 0.3)


class LinkFinder:
    def __init__(self):
        pass

    def discover_targets(self, html: str, base_url: str, max_targets: int = 3) -> list[CandidateLink]:
        """
        Parse HTML and return up to `max_targets` same-site links classified and prioritized as:
        hours > menu > contact > about > fees

        Returns the best (highest confidence) candidate per type in that order, capped to max_targets.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        base_parsed = urlparse(base_url)

        candidates_by_type: dict[str, list[tuple[float, CandidateLink]]] = {k: [] for k in TARGET_ORDER}

        for a in soup.find_all("a", href=True):
            href = a.get("href")
            if not href:
                continue

            # Normalize absolute URL
            abs_url = urljoin(base_url, href)
            # Scheme & same-site
            if (urlparse(abs_url).scheme or "").lower() not in ALLOWED_SCHEMES:
                continue
            if not is_same_site(base_url, abs_url):
                continue

            norm_url = strip_tracking_params(abs_url)
            # Basic mime hints: avoid obvious files
            if re.search(r"\.(pdf|docx?|xlsx?|zip|rar|7z)(\?|$)", norm_url, re.I):
                continue

            # Classify by URL path + anchor text
            page_type, score, reason = classify(urlparse(norm_url).path, a.get_text(strip=True) or "")
            if not page_type:
                continue

            # Section boost
            score = min(1.0, score + _section_weight(a))

            # Record
            cand = CandidateLink(
                url=norm_url,
                page_type=page_type,
                confidence=round(score, 3),
                anchor_text=a.get_text(strip=True) or "",
                reason=reason or "signals",
            )
            candidates_by_type[page_type].append((score, cand))

        # Pick best per type, prioritize by TARGET_ORDER, cap max_targets
        results: list[CandidateLink] = []
        for ttype in TARGET_ORDER:
            if not candidates_by_type[ttype]:
                continue
            # Sort by score desc, shorter URL first as tie-breaker (often /menu vs /menus/today)
            candidates_by_type[ttype].sort(key=lambda x: (-x[0], len(x[1].url)))
            best = candidates_by_type[ttype][0][1]
            results.append(best)
            if len(results) >= max_targets:
                break

        return results
