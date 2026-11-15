"""Real HTTPS trust checks for download and model-gateway clients."""
from __future__ import annotations

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from app.services import harness2_provider, local_stream, managed_agent_network as network, runtime_tls
from app.services.harness2_errors import StructuredFailure
import test_managed_agent_tls as fixtures


class RuntimeTLSTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.ca, self.leaf, self.key = fixtures.make_certificates(self.root / "trusted")
        self.other_ca, self.other_leaf, self.other_key = fixtures.make_certificates(self.root / "other")
        self.stack.enter_context(patch.dict(os.environ, {
            "SSL_CERT_FILE": "", "SSL_CERT_DIR": "", "HTTP_PROXY": "", "HTTPS_PROXY": "",
            "NO_PROXY": "", "no_proxy": ""}, clear=True))
        # Model a frozen OpenSSL whose compiled default paths are unavailable.
        # The real TLS handshakes below still verify issuer, hostname and dates.
        self.default_roots = self.stack.enter_context(
            patch.object(ssl.SSLContext, "load_default_certs", return_value=None))
        self.bundle = self.stack.enter_context(patch.object(runtime_tls.certifi, "where", return_value=str(self.ca)))
        self.requests = []

    def server(self, *, leaf=None, key=None, redirect=None, status=200):
        requests = self.requests

        class Origin(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append({"path": self.path, "headers": dict(self.headers)})
                self.send_response(302 if redirect else status)
                if redirect:
                    self.send_header("Location", redirect)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                payload = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": "verified fixture response"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}).encode()
                self.send_response(307 if redirect else status)
                if redirect:
                    self.send_header("Location", redirect)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        if leaf:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(leaf, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.stack.callback(close)
        return "%s://127.0.0.1:%d" % ("https" if leaf else "http", server.server_port)

    def download(self, url):
        with network.open_download(urllib.request.Request(url), timeout=2) as response:
            self.assertEqual(response.read(), b"ok")
            return response._soduwork_download_routes

    def gate(self, url):
        return local_stream._llm_gate_check({"needs_llm": True}, {"llm": {"base": url}})

    def provider(self, url):
        return harness2_provider.request(
            {"base": url, "model": "fixture-model", "key": "synthetic-fixture-key"},
            [{"role": "user", "content": "Synthetic TLS regression"}], [], timeout=2)

    def assert_rejected(self, url):
        before = len(self.requests)
        with self.assertRaises(urllib.error.URLError) as raised:
            self.download(url)
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)
        self.assertIn("TLS 证书校验失败", self.gate(url))
        with self.assertRaises(StructuredFailure) as provider_error:
            self.provider(url)
        self.assertEqual(provider_error.exception.code, "model_connection")
        self.assertEqual(len(self.requests), before)

    def test_harness2_model_request_uses_bundle_without_platform_roots_or_proxy(self):
        url = self.server(leaf=self.leaf, key=self.key)
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:1"}):
            message, usage = self.provider(url)
        self.assertEqual(message, {"role": "assistant", "content": "verified fixture response"})
        self.assertEqual(usage["input"], 2)
        self.assertEqual(usage["output"], 3)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["path"], "/chat/completions")
        self.assertEqual(self.requests[0]["body"]["model"], "fixture-model")
        self.assertEqual(self.requests[0]["headers"].get("Authorization"), "Bearer synthetic-fixture-key")
        self.assertNotIn("Proxy-Authorization", self.requests[0]["headers"])

    def test_harness2_model_request_keeps_redirects_disabled(self):
        destination = self.server(leaf=self.leaf, key=self.key)
        url = self.server(leaf=self.leaf, key=self.key, redirect=destination)
        with self.assertRaises(StructuredFailure) as raised:
            self.provider(url)
        self.assertEqual(raised.exception.code, "model_http")
        self.assertEqual(raised.exception.http_status, 307)
        self.assertEqual(len(self.requests), 1)

    def test_missing_platform_roots_reproduces_old_failure_and_both_clients_use_the_bundle(self):
        url = self.server(leaf=self.leaf, key=self.key)
        old_context = ssl.create_default_context()
        self.assertEqual(old_context.cert_store_stats()["x509_ca"], 0)
        old_opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=old_context))
        with self.assertRaises(urllib.error.URLError) as raised:
            old_opener.open(url, timeout=2)
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)
        self.assertEqual(self.requests, [])
        context = runtime_tls.client_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.cert_store_stats()["x509_ca"], 1)
        self.download(url)
        self.assertEqual(self.gate(url), "")
        self.assertEqual(len(self.requests), 2)

    def test_unset_and_empty_overrides_use_the_same_bundle_without_changing_environment(self):
        for unset in (False, True):
            with self.subTest(unset=unset), patch.dict(os.environ):
                if unset:
                    os.environ.pop("SSL_CERT_FILE", None)
                    os.environ.pop("SSL_CERT_DIR", None)
                before = dict(os.environ)
                self.assertEqual(runtime_tls.client_context().cert_store_stats()["x509_ca"], 1)
                self.assertEqual(dict(os.environ), before)

    def test_available_default_roots_remain_trusted_alongside_the_bundle(self):
        other_ca = str(self.other_ca)

        def platform_roots(context, purpose=ssl.Purpose.SERVER_AUTH):
            self.assertEqual(purpose, ssl.Purpose.SERVER_AUTH)
            context.load_verify_locations(cafile=other_ca)

        with patch.object(ssl.SSLContext, "load_default_certs", platform_roots):
            self.assertEqual(runtime_tls.client_context().cert_store_stats()["x509_ca"], 2)
            self.download(self.server(leaf=self.leaf, key=self.key))
            self.download(self.server(leaf=self.other_leaf, key=self.other_key))

    def test_explicit_ca_file_does_not_add_default_or_bundled_roots(self):
        with patch.dict(os.environ, {"SSL_CERT_FILE": str(self.other_ca)}):
            self.default_roots.side_effect = AssertionError("Explicit trust loaded platform roots")
            self.bundle.side_effect = AssertionError("Explicit trust loaded bundled roots")
            self.download(self.server(leaf=self.other_leaf, key=self.other_key))
            self.assert_rejected(self.server(leaf=self.leaf, key=self.key))

    def test_explicit_ca_directory_does_not_fall_back_to_bundled_roots(self):
        capath = self.root / "empty-hashed-ca-directory"
        capath.mkdir()
        with patch.dict(os.environ, {"SSL_CERT_DIR": str(capath)}):
            self.default_roots.side_effect = AssertionError("Explicit trust loaded platform roots")
            self.bundle.side_effect = AssertionError("Explicit trust loaded bundled roots")
            self.assert_rejected(self.server(leaf=self.leaf, key=self.key))

    def test_invalid_explicit_ca_configuration_fails_without_fallback_or_path_disclosure(self):
        malformed = self.root / "private-invalid-ca.pem"
        malformed.write_text("This is not a certificate.\n", encoding="utf-8")
        cases = (
            {"SSL_CERT_FILE": str(self.root / "private-missing-ca.pem")},
            {"SSL_CERT_FILE": str(self.root)},
            {"SSL_CERT_FILE": str(malformed)},
            {"SSL_CERT_FILE": " "},
            {"SSL_CERT_DIR": str(self.root / "private-missing-directory")},
            {"SSL_CERT_DIR": str(self.ca)},
            {"SSL_CERT_FILE": str(self.ca), "SSL_CERT_DIR": str(self.root / "missing")},
        )
        url = self.server(leaf=self.leaf, key=self.key)
        for variables in cases:
            with self.subTest(variables=tuple(variables)), patch.dict(os.environ, variables):
                self.bundle.reset_mock()
                with self.assertRaises((OSError, ValueError)):
                    self.download(url)
                self.bundle.assert_not_called()
                message = self.gate(url)
                self.assertIn("直连失败", message)
                self.assertNotIn(str(self.root), message)
                self.assertNotIn("private-", message)
                with self.assertRaises(StructuredFailure) as raised:
                    self.provider(url)
                self.assertEqual(raised.exception.code, "model_connection")
                self.assertNotIn(str(self.root), str(raised.exception))
        self.assertEqual(self.requests, [])

    def test_invalid_bundled_ca_fails_closed(self):
        self.bundle.return_value = str(self.root / "missing-bundle.pem")
        with self.assertRaises(OSError):
            runtime_tls.client_context()

    def test_unknown_issuer_is_rejected_by_both_clients(self):
        self.assert_rejected(self.server(leaf=self.other_leaf, key=self.other_key))

    def test_wrong_hostname_is_rejected_by_both_clients(self):
        ca, leaf, key = fixtures.make_certificates(self.root / "wrong-name", hostname="other.invalid")
        self.bundle.return_value = str(ca)
        self.assert_rejected(self.server(leaf=leaf, key=key))

    def test_expired_certificate_is_rejected_by_both_clients(self):
        ca, leaf, key = fixtures.make_certificates(self.root / "expired", expired=True)
        self.bundle.return_value = str(ca)
        self.assert_rejected(self.server(leaf=leaf, key=key))

    def test_plain_http_does_not_load_invalid_tls_configuration(self):
        with patch.dict(os.environ, {"SSL_CERT_FILE": str(self.root / "missing.pem")}):
            url = self.server()
            self.download(url)
            self.assertEqual(self.gate(url), "")
            self.assertEqual(self.provider(url)[0]["content"], "verified fixture response")
            self.bundle.assert_not_called()
            self.default_roots.assert_not_called()

    def test_http_redirects_to_https_use_the_bundle(self):
        destination = self.server(leaf=self.leaf, key=self.key)
        url = self.server(redirect=destination)
        self.download(url)
        self.assertEqual(self.gate(url), "")
        self.assertEqual(len(self.requests), 4)

    def test_http_redirects_to_https_reject_untrusted_certificates(self):
        destination = self.server(leaf=self.other_leaf, key=self.other_key)
        url = self.server(redirect=destination)
        with self.assertRaises(urllib.error.URLError) as raised:
            self.download(url)
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)
        self.assertIn("TLS 证书校验失败", self.gate(url))
        self.assertEqual(len(self.requests), 2)

    def test_tls_gateway_http_auth_error_still_proves_connectivity_without_proxy(self):
        url = self.server(leaf=self.leaf, key=self.key, status=401)
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:1"}):
            self.assertEqual(self.gate(url), "")
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("Authorization", self.requests[0]["headers"])
        self.assertNotIn("Proxy-Authorization", self.requests[0]["headers"])


if __name__ == "__main__":
    unittest.main()
