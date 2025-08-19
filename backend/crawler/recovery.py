# backend/crawler/recovery.py
# Website Recovery per Section 3D:
# - If venues.website is NULL, infer a canonical homepage from venue email domain or known socials.
# - Record all candidates in recovery_candidates with confidence + method.
# - If we choose one, write it to venues.website (HTTPS, no params, no trailing slash).
#
# Methods implemented (MVP, no external search API):
#   - email_domain  (e.g., info@my-restaurant.co.uk -> https://my-restaurant.co.uk)
#   - social_hint   (if a social URL obviously exposes a homepage in profile URL structure; conservative)
#
# Notes:
# - Never select socials themselves as the website.
# - Only http/https schemes; prefer https.
# - Strip tracking params and fragments.
# - Skip “link-in-bio” hosts (linktr.ee, bio.link, etc).

from __future__ import annotations

import os
import re
from typing import List, Tuple, Optional
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2.extras import RealDictCursor

SOCIAL_HOSTS = {
    "facebook.com", "m.facebook.com", "instagram.com", "x.com", "twitter.com",
    "tiktok.com", "linkedin.com", "youtube.com", "youtu.be", "pinterest.com"
}
LINK_HUBS = {"linktr.ee", "bio.link", "beacons.ai", "taplink.cc", "campsite.bio"}

def _get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)

def _canon_https(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return f"https://{host}"

def _clean_url(u: str) -> str:
    p = urlparse(u.strip())
    scheme = "https" if p.scheme in ("http", "https") else "https"
    netloc = (p.netloc or p.path).lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    # drop query/fragment, drop trailing slash
    clean = urlunparse((scheme, netloc, "", "", "", "")).rstrip("/")
    return clean

def _is_social(u: str) -> bool:
    host = (urlparse(u).netloc or "").lower()
    return any(host.endswith(h) for h in SOCIAL_HOSTS)

def _is_link_hub(u: str) -> bool:
    host = (urlparse(u).netloc or "").lower()
    return any(host.endswith(h) for h in LINK_HUBS)

def _email_domain_candidate(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    dom = email.split("@", 1)[1].strip().lower()
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", dom):
        return None
    return _canon_https(dom)

def _social_profile_home_hint(u: str) -> Optional[str]:
    """
    Very conservative: try to extract an obvious website from social profile URL query params/path if present.
    Most platforms don’t expose this in the URL; we avoid fetching social pages in MVP.
    So by default we return None unless the URL itself encodes a homepage (rare).
    """
    if not _is_social(u):
        return None
    # Example rare case: instagram.com/_u/<username>?u=https%3A%2F%2Fexample.com
    p = urlparse(u)
    if p.query and "http" in p.query:
        # pull the first http(s) substring
        m = re.search(r"(https?://[A-Za-z0-9.\-_/]+)", p.query)
        if m:
            return _clean_url(m.group(1))
    return None

def _insert_candidate(cur, fsq_place_id: str, url: str, confidence: float, method: str, is_chosen: bool):
    cur.execute(
        """
        INSERT INTO recovery_candidates (fsq_place_id, url, confidence, method, is_chosen)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (fsq_place_id, url, float(confidence), method, bool(is_chosen)),
    )

def propose_and_set_website(fsq_place_id: str) -> Optional[str]:
    """
    Propose site candidates and, if a good one exists, set venues.website.
    Returns the chosen website (or None).
    """
    with _get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Load venue
        cur.execute(
            "SELECT fsq_place_id, name, email, website FROM venues WHERE fsq_place_id=%s",
            (fsq_place_id,),
        )
        v = cur.fetchone()
        if not v:
            return None
        if v.get("website"):
            return v["website"]

        candidates: List[Tuple[str, float, str]] = []

        # 1) Email domain → homepage
        cand = _email_domain_candidate(v.get("email"))
        if cand and not (_is_social(cand) or _is_link_hub(cand)):
            candidates.append((cand, 0.9, "email_domain"))

        # 2) (Optional) We could scan enrichment.contact_details.social here once enrichment exists.
        #    For MVP recovery before the first crawl, we skip.

        # Persist candidates
        chosen_url: Optional[str] = None
        if candidates:
            # choose the highest confidence; ties by shortest hostname
            candidates.sort(key=lambda x: (-x[1], len(x[0])))
            chosen_url = candidates[0][0]

        for url, conf, method in candidates:
            _insert_candidate(cur, fsq_place_id, url, conf, method, is_chosen=(url == chosen_url))

        if chosen_url:
            cur.execute(
                "UPDATE venues SET website=%s WHERE fsq_place_id=%s",
                (chosen_url, fsq_place_id),
            )
        conn.commit()
        return chosen_url
