"""Optional browser-based resolution for SkipLink.

The plain HTTP engine (`core.resolve`) handles the vast majority of
shorteners.  A handful of hardened networks (JS countdown timers, safelink
gates, "get link" buttons) only reveal the destination when JavaScript runs
in a real browser.  This module drives Playwright's Chromium to do exactly
that - run the page, auto-click skip/continue buttons and read the final URL.

Playwright is an *optional* dependency: install it with
`pip install skiplink[browser]`, then `playwright install chromium`.
"""

from __future__ import annotations

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - environment dependent
    sync_playwright = None

from .core import DEFAULT_USER_AGENT, detect_protection, protection_note

__all__ = ["resolve_with_browser", "browser_available", "BROWSER_REQUIRED"]

BROWSER_REQUIRED = (
    "Browser mode needs Playwright:  pip install skiplink[browser]  "
    "then  playwright install chromium"
)


def browser_available() -> bool:
    return sync_playwright is not None


def _click_candidate(page):
    """Best-effort click of a skip/continue/get-link element.

    Returns True if something was clicked, False otherwise.
    """
    selectors = (
        'a:has-text("Skip")',
        'a:has-text("Continue")',
        'a:has-text("Get Link")',
        'a:has-text("Get link")',
        'a:has-text("Verify")',
        'a:has-text("Human")',
        'button:has-text("Continue")',
        'button:has-text("Get Link")',
        'a[id*="skip" i], a[class*="skip" i]',
        'a[id*="continue" i], a[class*="continue" i]',
        'button[id*="skip" i], button[class*="skip" i]',
        'input[type="submit"], button[type="submit"]',
    )
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible():
                locator.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def resolve_with_browser(
    start_url,
    timeout=30,
    max_clicks=4,
    settle_seconds=4,
    headless=True,
):
    """Resolve a short URL inside a real headless Chromium browser.

    Returns the same dict shape as `core.resolve` (plus a browser chain).
    """
    if sync_playwright is None:
        raise RuntimeError(BROWSER_REQUIRED)

    seen = set()
    chain = []
    final = start_url

    def record(url):
        nonlocal final
        if url and url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            chain.append({"url": url, "status": None, "kind": "browser"})
            final = url

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            context = browser.new_context(user_agent=DEFAULT_USER_AGENT)
            context.on("page", lambda popup: _record_popup(popup, record))
            page = context.new_page()
            page.on("framenavigated", lambda frame: record(frame.url))

            try:
                page.goto(start_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception:
                pass
            record(page.url)

            for _ in range(max_clicks):
                if not _click_candidate(page):
                    break
                page.wait_for_timeout(800)
                record(page.url)

            for _ in range(max(1, settle_seconds) * 2):
                page.wait_for_timeout(500)
                record(page.url)

            try:
                page_content = page.content()
            except Exception:
                page_content = ""
            protection = detect_protection(page_content, final)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    return {
        "start": start_url,
        "final": final,
        "chain": chain,
        "status": None,
        "protection": protection,
        "note": protection_note(protection),
        "error": None,
    }


def _record_popup(popup, record):
    try:
        popup.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    record(popup.url)
    try:
        popup.close()
    except Exception:
        pass
