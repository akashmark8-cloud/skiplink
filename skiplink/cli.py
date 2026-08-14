"""Command line interface for skiplink."""

import argparse
import json
import sys
import webbrowser

from .clipboard import copy_text
from .core import is_known_shortener, known_shortener_count, resolve

try:
    from .browser import browser_available, resolve_with_browser
except ImportError:  # pragma: no cover - playwright optional
    browser_available = lambda: False
    resolve_with_browser = None

from . import __version__


def _format_chain(chain):
    lines = []
    for index, hop in enumerate(chain, 1):
        status = hop["status"] if hop["status"] is not None else "-"
        lines.append(
            "  %2d. [%-8s] %s  (HTTP %s)"
            % (index, hop["kind"], hop["url"], status)
        )
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="skiplink",
        description=(
            "SkipLink - skip the ads, land on the real link. "
            "One-click bypass for 'shorten-and-earn' (ad-driven) URL "
            "shorteners, without ever rendering an ad."
        ),
        epilog="Examples:\n"
        "  skiplink https://adf.ly/AbCdEf\n"
        "  skiplink -o https://shorte.st/x\n"
        "  skiplink -j url1 url2\n"
        "  skiplink -b https://ouo.io/xyz   # real-browser mode\n"
        "  skiplink -f links.txt\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls", nargs="*", help="short / ad links to resolve")
    parser.add_argument("-f", "--file", metavar="FILE", help="read one URL per line from FILE")
    parser.add_argument("-o", "--open", action="store_true", help="open the final link in your browser")
    parser.add_argument("-c", "--copy", action="store_true", help="copy the final link to the clipboard")
    parser.add_argument("-j", "--json", action="store_true", help="print results as JSON")
    parser.add_argument(
        "-b",
        "--browser",
        action="store_true",
        help="resolve inside a real (headless) browser via Playwright - "
        "handles JS countdowns, safelink gates and 'get link' buttons",
    )
    parser.add_argument("--clicks", type=int, default=4, help="max auto-clicks in browser mode (default: 4)")
    parser.add_argument("-t", "--timeout", type=int, default=15, help="request timeout in seconds (default: 15)")
    parser.add_argument("-m", "--max-hops", type=int, default=15, help="max redirect hops to follow (default: 15)")
    parser.add_argument("-V", "--version", action="version", version="skiplink %s" % __version__)
    parser.add_argument("--list", action="store_true", help="show how many shortener domains are bundled")
    return parser


def _read_urls_from_file(path):
    urls = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            url = raw.strip()
            if url and not url.startswith("#"):
                urls.append(url)
    return urls


def _normalize(url):
    if not (url.startswith("http://") or url.startswith("https://")):
        return "https://" + url
    return url


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print("Bundled known shortener domains: %d" % known_shortener_count())
        return 0

    urls = list(args.urls)
    if args.file:
        try:
            urls.extend(_read_urls_from_file(args.file))
        except OSError as exc:
            print("ERROR: cannot read %s: %s" % (args.file, exc))
            return 1

    if not urls:
        parser.print_help()
        return 2

    if args.browser and not browser_available():
        print("ERROR: browser mode needs Playwright. Install it with:")
        print("  pip install skiplink[browser]")
        print("  playwright install chromium")
        return 1

    results = []
    for url in urls:
        url = _normalize(url)
        if args.browser:
            result = resolve_with_browser(url, timeout=args.timeout, max_clicks=args.clicks)
        else:
            result = resolve(url, timeout=args.timeout, max_hops=args.max_hops)
        results.append(result)

        if not args.json:
            domain = result["start"].split("//", 1)[-1].split("/", 1)[0]
            known = " (known shortener)" if is_known_shortener(domain) else ""
            mode = " [browser mode]" if args.browser else ""
            print("\n[%s]%s%s" % (result["start"], known, mode))
            print(_format_chain(result["chain"]))
            if result["error"]:
                print("ERROR: %s" % result["error"])
            else:
                print("\nFINAL: %s" % result["final"])
            if result.get("note"):
                print("NOTE:  %s" % result["note"])

    if args.json:
        print(json.dumps(results, indent=2))

    if results and args.copy:
        final = results[-1]["final"]
        ok = copy_text(final)
        print("Copied final link to clipboard: %s" % ("yes" if ok else "no (install xclip/xsel)"))

    if results and args.open:
        webbrowser.open(results[-1]["final"])
        print("Opened final link in your browser.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
