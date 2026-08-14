"""skiplink - one-click URL shortener bypass / link unwrapper.

Resolves 'shorten-and-earn' (ad-driven) short links to their final
destination without loading any ads - and honestly reports the walls it
cannot climb (reCAPTCHA, safelink gates, ad-farm money pages).
"""

__version__ = "1.1.0"

from .core import (
    detect_protection,
    extract_candidates,
    extract_destination,
    is_known_shortener,
    known_shortener_count,
    protection_note,
    resolve,
)

try:
    from .browser import browser_available, resolve_with_browser
except ImportError:  # pragma: no cover - playwright optional
    browser_available = lambda: False  # type: ignore[assignment]
    resolve_with_browser = None  # type: ignore[assignment]

__all__ = [
    "resolve",
    "resolve_with_browser",
    "browser_available",
    "detect_protection",
    "protection_note",
    "extract_candidates",
    "extract_destination",
    "is_known_shortener",
    "known_shortener_count",
    "__version__",
]
