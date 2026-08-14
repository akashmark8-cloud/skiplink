"""Unit tests for skiplink.core using a local HTTP server."""

import http.server
import threading
import unittest

from skiplink import core


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        base = "http://localhost:%d" % self.server.server_port
        path = self.path.split("?")[0]

        if path == "/r1":
            self._send(302, "text/plain", b"", {"Location": "/r2"})
        elif path == "/r2":
            self._send(301, "text/plain", b"", {"Location": "/target?final=1"})
        elif path == "/target":
            self._send(200, "text/plain", b"destination reached")
        elif path == "/var":
            body = ('<html><script>var link = "%s/target";</script></html>' % base).encode()
            self._send(200, "text/html", body)
        elif path == "/meta":
            body = ('<html><head><meta http-equiv="refresh" content="0; url=%s/target"></head></html>' % base).encode()
            self._send(200, "text/html", body)
        elif path == "/atob":
            body = ('<html><script>location.href = atob("%s");</script></html>' % "aHR0cDovL2xvY2FsaG9zdDo0MDAwL3RhcmdldA==").encode()
            self._send(200, "text/html", body)
        elif path == "/skip":
            body = ('<html><a class="skip-btn" href="%s/target">Skip</a></html>' % base).encode()
            self._send(200, "text/html", body)
        elif path == "/go":
            self._send(302, "text/plain", b"", {"Location": "/target"})
        elif path == "/selfloop":
            body = ('<html><script>location.href = "%s/selfloop";</script></html>' % base).encode()
            self._send(200, "text/html", body)
        elif path == "/jsonapi":
            body = ('{"link": "%s/target"}').encode() if False else '{"destination": "%s/target"}' % base
            self._send(200, "application/json", body.encode())
        elif path == "/plain":
            self._send(200, "text/plain", ("%s/target" % base).encode())
        elif path == "/captcha":
            body = (
                '<html><div id="wpsafelink-landing" class="wpsafe-link">'
                '<script src="https://www.google.com/recaptcha/api.js"></script>'
                '<div class="g-recaptcha" data-sitekey="6LcOhTUp-AAAA"></div>'
                '<input type="hidden" name="vip1" value="2">'
                '<h3>Human verification required</h3></div></html>'
            ).encode()
            self._send(200, "text/html", body)
        elif path == "/money":
            filler = "<p>Filler paragraph. SEO keyword stuffing. " * 80 + "</p>"
            body = (
                '<html><head><script src="https://securepubads.g.doubleclick.net/tag/js/gpt.js"></script></head>'
                '<body><h1>Top 10 hospitals in India - NABH &amp; JCI accreditation</h1>'
                + filler +
                '<img src="https://i.imgur.com/abc.jpg"></body></html>'
            ).encode()
            self._send(200, "text/html", body)
        elif path == "/realblog":
            body = (
                '<html><h1>A real blog post</h1>'
                '<a href="https://example.org/article">read the story</a>'
                '<script src="https://securepubads.g.doubleclick.net/tag/js/gpt.js"></script>'
                '</html>'
            ).encode()
            self._send(200, "text/html", body)
        else:
            self._send(404, "text/plain", b"not found")


class _ServerMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://localhost:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class TestRedirectChain(_ServerMixin):
    def test_multihop_redirects(self):
        result = core.resolve("%s/r1" % self.base)
        self.assertEqual(result["final"], "%s/target?final=1" % self.base)
        kinds = [hop["kind"] for hop in result["chain"]]
        self.assertEqual(kinds, ["redirect", "redirect", "final"])

    def test_relative_location_resolution(self):
        result = core.resolve("%s/go" % self.base)
        self.assertEqual(result["final"], "%s/target" % self.base)

    def test_error_reported(self):
        result = core.resolve("%s/nope" % self.base)
        self.assertEqual(result["chain"][-1]["kind"], "final")
        self.assertIn("nope", result["final"])


class TestEmbeddedExtraction(_ServerMixin):
    def test_var_link(self):
        result = core.resolve("%s/var" % self.base)
        self.assertEqual(result["final"], "%s/target" % self.base)

    def test_meta_refresh(self):
        result = core.resolve("%s/meta" % self.base)
        self.assertEqual(result["final"], "%s/target" % self.base)

    def test_atob(self):
        result = core.resolve("%s/atob" % self.base)
        self.assertEqual(result["final"], "http://localhost:4000/target")

    def test_skip_anchor(self):
        result = core.resolve("%s/skip" % self.base)
        self.assertEqual(result["final"], "%s/target" % self.base)

    def test_json_api(self):
        result = core.resolve("%s/jsonapi" % self.base)
        self.assertEqual(result["final"], "%s/target" % self.base)

    def test_plain_text_url(self):
        result = core.resolve("%s/plain" % self.base)
        self.assertEqual(result["final"], "%s/target" % self.base)

    def test_self_loop_is_final(self):
        result = core.resolve("%s/selfloop" % self.base)
        self.assertEqual(result["final"], "%s/selfloop" % self.base)


class TestExtractionHelpers(unittest.TestCase):
    def test_extract_candidates_ordering(self):
        text = (
            '<a href="https://ads.example/tracker">x</a>'
            '<script>var link = "https://final.example/real";</script>'
        )
        cands = core.extract_candidates(text, "https://shorte.example/a")
        self.assertEqual(cands[0], "https://final.example/real")

    def test_base64_decode(self):
        self.assertEqual(core._b64decode("aHR0cHM6Ly9leGFtcGxlLmNvbS8="), "https://example.com/")

    def test_known_shortener(self):
        self.assertTrue(core.is_known_shortener("bit.ly"))
        self.assertTrue(core.is_known_shortener("adf.ly"))
        self.assertFalse(core.is_known_shortener("totally-not-a-shortener-xyz.com"))

    def test_known_count(self):
        self.assertGreater(core.known_shortener_count(), 1000)


class TestProtectionDetection(_ServerMixin):
    def test_captcha_gate(self):
        result = core.resolve("%s/captcha" % self.base)
        self.assertTrue(result["protection"]["captcha"])
        self.assertTrue(result["protection"]["safelink"])
        self.assertIsNotNone(result["note"])
        self.assertIn("Captcha", result["note"])

    def test_money_page(self):
        result = core.resolve("%s/money" % self.base)
        self.assertTrue(result["protection"]["money_page"])
        self.assertFalse(result["protection"]["captcha"])
        self.assertIsNotNone(result["note"])

    def test_real_page_not_flagged(self):
        protection = core.detect_protection("", "")
        self.assertEqual(protection, {"captcha": False, "safelink": False, "money_page": False})

    def test_real_blog_with_ads_not_money_page(self):
        result = core.resolve("%s/realblog" % self.base)
        self.assertFalse(result["protection"]["money_page"])

    def test_protection_note_none_when_clear(self):
        self.assertIsNone(core.protection_note({"captcha": False, "safelink": False, "money_page": False}))

    def test_plain_final_has_no_protection(self):
        result = core.resolve("%s/target" % self.base)
        self.assertFalse(any(result["protection"].values()))
        self.assertIsNone(result["note"])


if __name__ == "__main__":
    unittest.main()
