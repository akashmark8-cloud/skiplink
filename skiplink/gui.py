"""One-click Tkinter GUI for skiplink.

Paste a short / ad link, press Enter or click "Bypass", and get the final
destination URL instantly - no ads, no timer waiting. An optional
"Use browser" mode drives a real (headless) browser for JS-gated links.
"""

from __future__ import annotations

import threading
from urllib.parse import urlparse
import webbrowser

try:
    import tkinter as tk
    from tkinter import scrolledtext, ttk
except ImportError as _exc:  # pragma: no cover - depends on system Tk build
    tk = None
    _TK_IMPORT_ERROR = _exc
else:
    _TK_IMPORT_ERROR = None

from .core import is_known_shortener, resolve

try:
    from .browser import resolve_with_browser
except ImportError:  # pragma: no cover - playwright optional
    resolve_with_browser = None

_Base = tk.Tk if tk is not None else object

_TK_HINT = (
    "The GUI needs Tkinter, but it is not available on this system.\n"
    "Install it with your package manager, e.g.:\n"
    "  Debian/Ubuntu:  sudo apt install python3-tk\n"
    "  Fedora:         sudo dnf install python3-tkinter\n"
    "  Arch:           sudo pacman -S tk\n"
    "  macOS:          brew install python-tk\n"
    "You can still use the CLI:  python main.py \"<short-link>\""
)


class SkiplinkApp(_Base):
    def __init__(self):
        if tk is None:  # pragma: no cover
            raise SystemExit(_TK_HINT)
        super().__init__()
        self.title("SkipLink - skip the ads, land on the real link")
        self.geometry("820x600")
        self.minsize(560, 400)
        self.final_url = ""
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self):
        top = ttk.Frame(self, padding=(10, 10, 10, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Short / ad link:").pack(side="left")
        self.entry = ttk.Entry(top)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", lambda _e: self.bypass())
        self.entry.focus_set()

        btns = ttk.Frame(self, padding=(10, 6, 10, 6))
        btns.pack(fill="x")
        self.btn_bypass = ttk.Button(btns, text="Bypass", command=self.bypass)
        self.btn_bypass.pack(side="left")
        self.btn_paste = ttk.Button(btns, text="Paste from clipboard", command=self.paste)
        self.btn_paste.pack(side="left", padx=4)
        self.btn_open = ttk.Button(btns, text="Open final", command=self.open_final, state="disabled")
        self.btn_open.pack(side="left", padx=4)
        self.btn_copy = ttk.Button(btns, text="Copy final", command=self.copy_final, state="disabled")
        self.btn_copy.pack(side="left", padx=4)
        self.btn_clear = ttk.Button(btns, text="Clear", command=self.clear)
        self.btn_clear.pack(side="left", padx=4)

        self.browser_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            btns,
            text="Use browser (Playwright)",
            variable=self.browser_var,
        ).pack(side="right")

        self.out = scrolledtext.ScrolledText(self, state="disabled", wrap="word")
        self.out.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        self.status = ttk.Label(self, text="Paste a link and press Enter (or click Bypass).")
        self.status.pack(fill="x", padx=10, pady=(0, 8))

    # --- UI actions ---------------------------------------------------

    def _log(self, msg):
        self.out.configure(state="normal")
        self.out.insert("end", msg + "\n")
        self.out.see("end")
        self.out.configure(state="disabled")

    def paste(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if text:
            self.entry.delete(0, "end")
            self.entry.insert(0, text)
            self.status.config(text="Pasted. Press Enter or click Bypass.")

    def clear(self):
        self.entry.delete(0, "end")
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.configure(state="disabled")
        self.final_url = ""
        self.btn_open.config(state="disabled")
        self.btn_copy.config(state="disabled")
        self.status.config(text="Cleared.")

    def open_final(self):
        if self.final_url:
            webbrowser.open(self.final_url)

    def copy_final(self):
        if not self.final_url:
            return
        self.clipboard_clear()
        self.clipboard_append(self.final_url)
        self.status.config(text="Final link copied to clipboard.")

    # --- bypass logic ------------------------------------------------

    def bypass(self):
        url = self.entry.get().strip()
        if not url:
            try:
                url = self.clipboard_get().strip()
            except tk.TclError:
                url = ""
        if not url:
            self.status.config(text="Enter a link first (or have one on the clipboard).")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url
            self.entry.delete(0, "end")
            self.entry.insert(0, url)

        if self.browser_var.get() and resolve_with_browser is None:
            self.status.config(
                text="Browser mode needs Playwright: pip install skiplink[browser] && playwright install chromium"
            )
            return

        self.final_url = ""
        self.btn_open.config(state="disabled")
        self.btn_copy.config(state="disabled")
        self.btn_bypass.config(state="disabled")
        mode = "browser" if self.browser_var.get() else "http"
        self.status.config(text="Resolving (%s), please wait..." % mode)
        threading.Thread(target=self._work, args=(url,), daemon=True).start()

    def _work(self, url):
        try:
            if self.browser_var.get():
                result = resolve_with_browser(url)
            else:
                result = resolve(url)
        except Exception as exc:
            result = {"start": url, "final": "", "chain": [], "error": str(exc),
                      "protection": {}, "note": None}

        def done():
            self.btn_bypass.config(state="normal")
            self.final_url = result["final"] or ""
            self._log("Resolution chain:")
            for index, hop in enumerate(result["chain"], 1):
                domain = urlparse(hop["url"]).netloc.lower()
                known = "  <known shortener>" if is_known_shortener(domain) else ""
                status = hop["status"] if hop["status"] is not None else "-"
                self._log(
                    "  %2d. [%-8s] %s  (HTTP %s)%s"
                    % (index, hop["kind"], hop["url"], status, known)
                )
            if result.get("error"):
                self._log("\nERROR: %s" % result["error"])
                self.status.config(text="Failed: %s" % result["error"])
                return
            self._log("\nFINAL: %s" % self.final_url)
            if result.get("note"):
                self._log("\nNOTE: %s" % result["note"])
            if self.final_url and self.final_url != url:
                self.btn_open.config(state="normal")
                self.btn_copy.config(state="normal")
                self.status.config(text="Done - %d hop(s)." % len(result["chain"]))
            elif self.final_url:
                self.status.config(text="No further redirect found - the link is already final.")
            else:
                self.status.config(text="Could not resolve.")

        self.after(0, done)


def run():
    if tk is None:  # pragma: no cover
        raise SystemExit(_TK_HINT)
    SkiplinkApp().mainloop()


if __name__ == "__main__":
    run()
