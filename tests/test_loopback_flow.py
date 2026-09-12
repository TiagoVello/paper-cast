"""The consent flow's local half: the loopback server, driven without touching Google."""

import contextlib
import io
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import youtube_auth as ya  # noqa: E402


def visit(url: str) -> str:
    """Fetch like a browser does: a non-200 (the 404 on /favicon.ico) is not an exception."""
    try:
        with urllib.request.urlopen(url) as response:
            return response.read().decode()
    except urllib.error.HTTPError as err:
        return err.read().decode()


class LoopbackFlowTest(unittest.TestCase):
    """`webbrowser.open` stands in for the user: it plays Google's redirect back at us."""

    def run_flow(self, respond, exchange=None):
        captured = {}

        def fake_open(url):
            captured["url"] = url
            query = parse_qs(urlsplit(url).query)
            target = query["redirect_uri"][0]
            state = query["state"][0]
            threading.Thread(target=respond, args=(target, state), daemon=True).start()
            return True

        exchange = exchange or (lambda *a, **kw: {"refresh_token": "rt", "scope": ya.SCOPE})
        with mock.patch.object(ya.webbrowser, "open", fake_open):
            with mock.patch.object(ya, "exchange_code", exchange):
                with contextlib.redirect_stdout(io.StringIO()):
                    return ya.run_consent_flow("cid", "sec", timeout=10.0), captured

    def run_flow_expecting_error(self, respond):
        with self.assertRaises(ya.ConsentError):
            self.run_flow(respond)

    def test_passes_the_pkce_verifier_matching_the_challenge_it_advertised(self):
        seen = {}

        def exchange(client_id, client_secret, code, redirect_uri, code_verifier):
            seen["verifier"] = code_verifier
            return {"refresh_token": "rt", "scope": ya.SCOPE}

        _, captured = self.run_flow(
            lambda target, state: visit(f"{target}?code=4/abc&state={state}"), exchange
        )
        challenge = parse_qs(urlsplit(captured["url"]).query)["code_challenge"][0]
        self.assertEqual(ya.code_challenge(seen["verifier"]), challenge)

    def test_catches_the_redirect_and_exchanges_the_code(self):
        seen = {}

        def exchange(client_id, client_secret, code, redirect_uri, code_verifier):
            seen.update(code=code, redirect_uri=redirect_uri)
            return {"refresh_token": "rt", "scope": ya.SCOPE}

        grant, captured = self.run_flow(
            lambda target, state: visit(f"{target}?code=4/abc&state={state}"), exchange
        )
        self.assertEqual(grant["refresh_token"], "rt")
        self.assertEqual(seen["code"], "4/abc")
        self.assertEqual(seen["redirect_uri"], parse_qs(urlsplit(captured["url"]).query)["redirect_uri"][0])

    def test_redirects_to_a_loopback_address(self):
        _, captured = self.run_flow(lambda target, state: visit(f"{target}?code=4/abc&state={state}"))
        redirect = parse_qs(urlsplit(captured["url"]).query)["redirect_uri"][0]
        self.assertTrue(redirect.startswith("http://127.0.0.1:"), redirect)

    def test_surfaces_a_denied_consent(self):
        with self.assertRaises(ya.ConsentError) as caught:
            self.run_flow(lambda target, state: visit(f"{target}?error=access_denied&state={state}"))
        self.assertIn("access_denied", str(caught.exception))

    def test_ignores_the_browsers_favicon_request(self):
        def respond(target, state):
            visit(f"{target}favicon.ico")
            visit(f"{target}?code=4/abc&state={state}")

        grant, _ = self.run_flow(respond)
        self.assertEqual(grant["refresh_token"], "rt")

    def test_escapes_what_it_reflects_into_the_browser(self):
        """The error page renders the callback back at the browser, on the loopback origin."""
        pages = {}

        def respond(target, state):
            pages["html"] = visit(f"{target}?error=%3Cimg+src%3Dx+onerror%3Dalert(1)%3E&state={state}")
            visit(f"{target}?code=4/abc&state={state}")

        self.run_flow_expecting_error(respond)
        self.assertNotIn("<img src=x", pages["html"])
        self.assertIn("&lt;img", pages["html"])

    def test_ignores_a_callback_whose_state_does_not_match(self):
        """A stray or hostile request must not kill an authorisation that is still in flight."""
        def respond(target, state):
            visit(f"{target}?code=attacker-code&state=not-the-state")
            visit(f"{target}?code=4/abc&state={state}")

        grant, _ = self.run_flow(respond)
        self.assertEqual(grant["refresh_token"], "rt")

    def test_gives_up_when_no_callback_arrives(self):
        with mock.patch.object(ya.webbrowser, "open", lambda url: True):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ya.ConsentError):
                    ya.run_consent_flow("cid", "sec", timeout=0.5)

    def test_frees_the_port_afterwards(self):
        """A second run must not trip over the first one's listening socket."""
        for _ in range(2):
            grant, _ = self.run_flow(lambda target, state: visit(f"{target}?code=4/abc&state={state}"))
            self.assertEqual(grant["refresh_token"], "rt")


if __name__ == "__main__":
    unittest.main()
