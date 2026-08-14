"""Core resolution engine for SkipLink.

Follows HTTP redirect chains and inspects interstitial ("shorten-and-earn")
landing pages for the embedded destination URL, then returns the final
target.  No ads are ever rendered: only plain HTTP requests are made and
the embedded link is extracted and followed.

The engine also *detects* the walls it cannot climb - Google reCAPTCHA /
safelink verification pages and ad-farm "money pages" - and reports them
honestly instead of pretending a filler article is the destination.

Everything here uses only the Python standard library.
"""

from __future__ import annotations

import base64
import gzip
import html as html_mod
import json
import os
import re
import sys
import zlib
from http.cookiejar import CookieJar
from urllib.parse import urljoin, unquote, urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPError,
    HTTPRedirectHandler,
    Request,
    build_opener,
)

__all__ = [
    "resolve",
    "extract_candidates",
    "extract_destination",
    "is_known_shortener",
    "known_shortener_count",
    "detect_protection",
    "protection_note",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REDIRECT_CODES = {300, 301, 302, 303, 307, 308}

# Host fragments of ad/tracker CDNs that are never the real destination.
AD_KEYWORDS = (
    "doubleclick",
    "googleadservices",
    "googlesyndication",
    "adservice",
    "adnxs",
    "taboola",
    "outbrain",
    "popads",
    "adsterra",
    "propellerads",
    "mgid",
    "revcontent",
    "criteo",
    "pubmatic",
    "rubicon",
    "openx",
    "smartadserver",
    "adskeeper",
    "adcash",
    "adxpansion",
    "trafficjunky",
    "pixel.quantserve",
    "scorecardresearch",
)

RE_META = re.compile(
    r'<meta\b[^>]*?http-equiv\s*=\s*["\']?refresh["\']?[^>]*?content\s*=\s*["\']?([^"\'>]+)',
    re.I,
)
RE_LOCATION = re.compile(
    r'(?:location\.(?:href|replace|assign)|window\.location|document\.location)'
    r'\s*=\s*["\']([^"\']+)["\']',
    re.I,
)
RE_OPEN = re.compile(r'(?:window|top)\.open\s*\(\s*["\']([^"\']+)["\']', re.I)
RE_VAR = re.compile(
    r'var\s+(?:link|url|redirect|dest|destination|target|href|go|final)'
    r'\s*[:=]\s*["\']([^"\']+)["\']',
    re.I,
)
RE_VAR_LOOSE = re.compile(r'(?<!\w)(?:link|href|url)\s*=\s*["\']([^"\']+)["\']', re.I)
RE_ATOB = re.compile(r'atob\s*\(\s*["\']([^"\']+)["\']', re.I)
RE_UNESCAPE = re.compile(r'unescape\s*\(\s*["\']([^"\']+)["\']', re.I)
RE_DATA_ATTR = re.compile(
    r'data-(?:link|href|url|destination|final)\s*=\s*["\']([^"\']+)["\']', re.I
)
RE_ANCHOR = re.compile(r'<a\b[^>]*>', re.I)
RE_IFRAME = re.compile(r'<iframe\b[^>]+src\s*=\s*["\']([^"\']+)["\']', re.I)
RE_PLAIN_URL = re.compile(r'https?://[^\s"\'<>]+', re.I)

_SKIP_HINT = re.compile(r'(skip|continue|proceed|next|get link|btn|button|go)', re.I)

# Protection / wall detection -------------------------------------------
RE_RECAPTCHA = re.compile(r'(g-recaptcha|recaptcha/api\.js|data-sitekey|grecaptcha)', re.I)
RE_SAFELINK = re.compile(r'(wpsafelink-landing|wpsafe-|vip1|human[\s-]*verification|safelink)', re.I)
RE_AD_PRESENT = re.compile(r'(securepubads|adsbygoogle|pagead2|doubleclick\.net|gpt\.js)', re.I)
# Hosts that are framework/theme plumbing, never a real destination.
NEUTRAL_HOSTS = (
    "wpastra.com",
    "wordpress.org",
    "w.org",
    "schema.org",
    "w3.org",
    "gmpg.org",
    "gravatar.com",
    "wp.com",
)

# Path suffixes that are assets (images, scripts, styles), not content links.
_ASSET_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".mp4", ".webm",
)

_DATA_PATH = os.path.join(
    getattr(sys, "_MEIPASS", None) or os.path.dirname(__file__),
    "data",
    "shortener_domains.txt",
)
_KNOWN_DOMAINS = None


def _load_known_domains():
    """Lazily load the bundled shortener domain list (data/shortener_domains.txt)."""
    global _KNOWN_DOMAINS
    if _KNOWN_DOMAINS is None:
        names = set()
        try:
            with open(_DATA_PATH, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        names.add(line)
        except OSError:
            pass
        _KNOWN_DOMAINS = names
    return _KNOWN_DOMAINS


def known_shortener_count():
    return len(_load_known_domains())


def is_known_shortener(domain):
    """Return True if `domain` is one of the known shortener domains."""
    d = (domain or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d in _load_known_domains()


def _clean_url(value):
    """Decode HTML entities + common JS escapes and strip surrounding quotes."""
    value = html_mod.unescape(value or "")
    value = value.replace("\\/", "/")
    value = value.replace('\\"', '"')
    value = value.replace("\\\\", "\\")
    value = value.strip().strip('"').strip("'")
    return value


def _is_http(url):
    try:
        return urlparse(url).scheme in ("http", "https")
    except Exception:
        return False


def _is_ad(url):
    host = (urlparse(url).netloc or "").lower()
    return any(k in host for k in AD_KEYWORDS)


def _b64decode(value):
    try:
        s = value.strip().strip("'\"")
        pad = "=" * (-len(s) % 4)
        raw = base64.b64decode(s + pad, validate=False)
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None


def _maybe_decompress(body, content_encoding):
    enc = (content_encoding or "").lower()
    if not body:
        return body
    if "gzip" in enc:
        try:
            return gzip.decompress(body)
        except OSError:
            return body
    if "deflate" in enc:
        try:
            return zlib.decompress(body)
        except zlib.error:
            try:
                return zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error:
                return body
    return body


def _decode_body(body, content_type=""):
    if not body:
        return ""
    charset = ""
    if content_type:
        m = re.search(r'charset=["\']?([\w-]+)', content_type, re.I)
        if m:
            charset = m.group(1)
    for enc in (charset, "utf-8", "iso-8859-1"):
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


class _NoRedirect(HTTPRedirectHandler):
    """Intercept every redirect so we can inspect and follow each hop manually."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener():
    return build_opener(HTTPCookieProcessor(CookieJar()), _NoRedirect())


def _fetch(opener, url, timeout, headers):
    """Perform one request. Returns (status, headers, raw_body)."""
    req = Request(url, headers=headers)
    try:
        resp = opener.open(req, timeout=timeout)
        status, resp_headers = resp.status, resp.headers
        try:
            body = resp.read()
        finally:
            resp.close()
        return status, resp_headers, body
    except HTTPError as exc:
        status, exc_headers = exc.code, exc.headers
        try:
            body = exc.read()
        except Exception:
            body = b""
        finally:
            exc.close()
        return status, exc_headers, body


def extract_candidates(text, base_url=""):
    """Return http(s) candidate destination URLs found in `text`, best first."""
    cands = []
    seen = set()

    def add(raw, priority):
        raw = _clean_url(raw)
        if not raw:
            return
        merged = urljoin(base_url, raw) if base_url else raw
        if _is_http(merged) and merged not in seen:
            seen.add(merged)
            cands.append((priority, merged))

    for m in RE_META.finditer(text):
        mm = re.search(r'url\s*=\s*["\']?([^"\'>\s]+)', m.group(1), re.I)
        if mm:
            add(mm.group(1), 10)
    for m in RE_LOCATION.finditer(text):
        add(m.group(1), 9)
    for m in RE_OPEN.finditer(text):
        add(m.group(1), 8)
    for m in RE_VAR.finditer(text):
        add(m.group(1), 7)
    for m in RE_VAR_LOOSE.finditer(text):
        add(m.group(1), 6)
    for m in RE_DATA_ATTR.finditer(text):
        add(m.group(1), 5)
    for m in RE_ATOB.finditer(text):
        dec = _b64decode(m.group(1))
        if dec:
            add(dec, 8)
    for m in RE_UNESCAPE.finditer(text):
        dec = unquote(_clean_url(m.group(1)))
        add(dec, 8)
        dec = _b64decode(m.group(1))
        if dec:
            add(dec, 8)
    for m in RE_ANCHOR.finditer(text):
        tag = m.group(0)
        hm = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        if not hm:
            continue
        add(hm.group(1), 7 if _SKIP_HINT.search(tag) else 4)
    for m in RE_IFRAME.finditer(text):
        add(m.group(1), 3)
    if len(text) < 2000:
        for m in RE_PLAIN_URL.finditer(text):
            add(m.group(0), 2)

    cands.sort(key=lambda x: -x[0])
    return [u for _, u in cands]


def _from_json(text):
    try:
        data = json.loads(text)
    except Exception:
        return None
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and _is_http(value) and not _is_ad(value):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return found


def _pick_best(candidates, page_url):
    page_host = urlparse(page_url).netloc.lower()
    for url in candidates:
        if not _is_http(url) or _is_ad(url):
            continue
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host == page_host and parsed.path in ("", "/"):
            continue
        return url
    return None


def extract_destination(text, page_url="", content_type=""):
    """Extract the most likely destination URL from an interstitial page."""
    if not text:
        return None
    stripped = text.strip()
    if content_type.startswith("application/json") or stripped.startswith(("{", "[")):
        for url in _from_json(text) or []:
            if _is_http(url) and not _is_ad(url):
                return url
    for url in extract_candidates(text, page_url):
        if _is_http(url) and not _is_ad(url):
            return url
    return None


def _offsite_content_links(text, page_url):
    """Non-ad, non-plumbing, non-asset http links that point away from `page_url`."""
    page_host = urlparse(page_url).netloc.lower()
    out = []
    for url in extract_candidates(text, page_url):
        if not _is_http(url) or _is_ad(url):
            continue
        host = urlparse(url).netloc.lower()
        if host == page_host or host in NEUTRAL_HOSTS:
            continue
        if urlparse(url).path.lower().endswith(_ASSET_SUFFIXES):
            continue
        out.append(url)
    return out


def detect_protection(text, page_url=""):
    """Classify the walls on a page.

    Returns a dict with boolean flags:
      captcha     - Google reCAPTCHA / similar human-verification gate
      safelink    - wp-safelink style "click to continue" lock page
      money_page  - ad-farm filler article with no real off-site content
    """
    protection = {"captcha": False, "safelink": False, "money_page": False}
    if not text:
        return protection
    if RE_RECAPTCHA.search(text):
        protection["captcha"] = True
    if RE_SAFELINK.search(text):
        protection["safelink"] = True
    if len(text) > 3000 and RE_AD_PRESENT.search(text):
        if not _offsite_content_links(text, page_url):
            protection["money_page"] = True
    return protection


def protection_note(protection):
    """A short human-readable explanation of a protection dict (or None)."""
    if protection.get("captcha"):
        return (
            "Captcha-gated (Google reCAPTCHA / human verification). "
            "The final link is only released to a human in a real browser; "
            "no automated tool can bypass it."
        )
    if protection.get("money_page"):
        return (
            "Ad-farm money page. This is a filler article that exists only "
            "to show ads - there is no real destination behind it."
        )
    if protection.get("safelink"):
        return "Safelink / verification-gated page - further steps need a human click."
    return None


def _looks_like_interstitial(text, candidates):
    """Heuristic: is this a small page that is clearly a redirect stub?

    Only used for hosts that are NOT in the known-shortener list, to avoid
    digging into ordinary destination pages that happen to contain URLs.
    """
    if len(text) > 60000:
        return False
    if len(candidates) > 4:
        return False
    return bool(
        RE_META.search(text)
        or RE_LOCATION.search(text)
        or RE_VAR.search(text)
        or RE_ATOB.search(text)
        or RE_UNESCAPE.search(text)
        or RE_DATA_ATTR.search(text)
        or any(_SKIP_HINT.search(tag) for tag in RE_ANCHOR.findall(text))
    )


def resolve(start_url, timeout=15, max_hops=15, user_agent=None):
    """Resolve a (possibly multi-layer) short URL to its final destination.

    Returns a dict:
      {
        "start":      original URL,
        "final":      final destination URL,
        "chain":      [{"url", "status", "kind"}, ...] where kind is one of
                      "redirect", "embedded", "final" or "error",
        "status":     HTTP status of the final hop,
        "protection": {"captcha", "safelink", "money_page"} wall flags,
        "note":       human-readable explanation of a wall (or None),
        "error":      error message or None,
      }
    """
    opener = _build_opener()
    headers = {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    current = (start_url or "").strip()
    seen = set()
    chain = []
    result = {
        "start": start_url,
        "final": current,
        "chain": chain,
        "status": None,
        "protection": {"captcha": False, "safelink": False, "money_page": False},
        "note": None,
        "error": None,
    }

    for _ in range(max_hops):
        if current in seen:
            break
        seen.add(current)

        try:
            status, resp_headers, body = _fetch(opener, current, timeout, headers)
        except Exception as exc:
            result["error"] = "request failed: %s" % exc
            chain.append({"url": current, "status": None, "kind": "error"})
            break

        content_type = resp_headers.get("Content-Type", "") if resp_headers else ""
        body = _maybe_decompress(body, resp_headers.get("Content-Encoding", "") if resp_headers else "")
        location = resp_headers.get("Location") if resp_headers is not None else None

        if status in REDIRECT_CODES and location:
            nxt = urljoin(current, _clean_url(location))
            chain.append({"url": current, "status": status, "kind": "redirect"})
            current = nxt
            continue

        text = _decode_body(body, content_type)

        plain = text.strip().strip('"').strip("'")
        dest = None
        if _is_http(plain) and plain != current:
            dest = plain
        elif content_type.startswith("application/json") or text.lstrip().startswith(("{", "[")):
            for url in _from_json(text) or []:
                if _is_http(url) and not _is_ad(url):
                    dest = url
                    break

        if not dest:
            candidates = extract_candidates(text, current)
            host = urlparse(current).netloc.lower()
            if is_known_shortener(host) or _looks_like_interstitial(text, candidates):
                dest = _pick_best(candidates, current)

        if dest and dest != current:
            chain.append({"url": current, "status": status, "kind": "embedded"})
            current = dest
            continue

        # No further destination: classify whatever wall this page is.
        result["protection"] = detect_protection(text, current)
        result["note"] = protection_note(result["protection"])
        chain.append({"url": current, "status": status, "kind": "final"})
        break
    else:
        chain.append({"url": current, "status": None, "kind": "max-hops"})

    result["final"] = current
    result["status"] = chain[-1]["status"] if chain else None
    return result
