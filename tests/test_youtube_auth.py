"""Unit tests for the pure seams of scripts/youtube_auth.py.

Everything that touches the network is left to the manual bootstrap run; what is
tested here is the part that is easy to get quietly wrong: the consent URL, the
loopback callback, token file permissions, the videos.insert body, and the
resumable-upload offset bookkeeping.
"""

import base64
import contextlib
import email.message
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import youtube_auth as ya  # noqa: E402


class BuildAuthUrlTest(unittest.TestCase):
    def query(self, **kwargs):
        url = ya.build_auth_url(
            client_id=kwargs.pop("client_id", "cid.apps.googleusercontent.com"),
            redirect_uri=kwargs.pop("redirect_uri", "http://127.0.0.1:8080/"),
            state=kwargs.pop("state", "st4te"),
            code_challenge=kwargs.pop("code_challenge", "chall3nge"),
            **kwargs,
        )
        return url, parse_qs(urlsplit(url).query)

    def test_points_at_googles_auth_endpoint(self):
        url, _ = self.query()
        self.assertTrue(url.startswith(ya.AUTH_ENDPOINT + "?"), url)

    def test_requests_offline_access_so_a_refresh_token_comes_back(self):
        _, q = self.query()
        self.assertEqual(q["access_type"], ["offline"])

    def test_forces_the_consent_prompt_so_a_reauth_still_yields_a_refresh_token(self):
        _, q = self.query()
        self.assertEqual(q["prompt"], ["consent"])

    def test_asks_only_for_the_upload_scope(self):
        _, q = self.query()
        self.assertEqual(q["scope"], [ya.SCOPE])

    def test_carries_a_pkce_challenge(self):
        _, q = self.query()
        self.assertEqual(q["code_challenge"], ["chall3nge"])
        self.assertEqual(q["code_challenge_method"], ["S256"])

    def test_carries_the_client_redirect_state_and_code_response_type(self):
        _, q = self.query()
        self.assertEqual(q["client_id"], ["cid.apps.googleusercontent.com"])
        self.assertEqual(q["redirect_uri"], ["http://127.0.0.1:8080/"])
        self.assertEqual(q["state"], ["st4te"])
        self.assertEqual(q["response_type"], ["code"])


class PkceTest(unittest.TestCase):
    def test_challenge_is_the_base64url_sha256_of_the_verifier_without_padding(self):
        verifier = "a" * 64
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
        self.assertEqual(ya.code_challenge(verifier), expected.decode())

    def test_challenge_carries_no_padding(self):
        self.assertNotIn("=", ya.code_challenge(ya.new_code_verifier()))

    def test_verifier_is_long_enough_to_be_worth_having(self):
        # RFC 7636 wants 43-128 characters.
        self.assertGreaterEqual(len(ya.new_code_verifier()), 43)


class ParseCallbackTest(unittest.TestCase):
    def test_returns_the_code_when_the_state_matches(self):
        code = ya.parse_callback("/?code=4/abc&state=st4te", expected_state="st4te")
        self.assertEqual(code, "4/abc")

    def test_rejects_a_mismatched_state(self):
        with self.assertRaises(ya.ConsentError):
            ya.parse_callback("/?code=4/abc&state=other", expected_state="st4te")

    def test_does_not_repeat_the_expected_state_back_to_whoever_asked(self):
        """The error reaches a browser page; echoing the state would hand it to an attacker."""
        with self.assertRaises(ya.ConsentError) as caught:
            ya.parse_callback("/?code=4/abc&state=other", expected_state="s3cret-state")
        self.assertNotIn("s3cret-state", str(caught.exception))

    def test_rejects_a_missing_state_even_when_a_code_is_present(self):
        with self.assertRaises(ya.ConsentError):
            ya.parse_callback("/?code=4/abc", expected_state="st4te")

    def test_surfaces_the_error_google_sent_back(self):
        with self.assertRaises(ya.ConsentError) as caught:
            ya.parse_callback("/?error=access_denied&state=st4te", expected_state="st4te")
        self.assertIn("access_denied", str(caught.exception))

    def test_rejects_a_callback_with_neither_code_nor_error(self):
        with self.assertRaises(ya.ConsentError):
            ya.parse_callback("/favicon.ico", expected_state="st4te")


class ClientSecretTest(unittest.TestCase):
    def test_reads_the_desktop_app_installed_shape(self):
        creds = ya.client_credentials({"installed": {"client_id": "cid", "client_secret": "sec"}})
        self.assertEqual(creds, ("cid", "sec"))

    def test_rejects_a_web_application_client(self):
        with self.assertRaises(ya.ConfigError) as caught:
            ya.client_credentials({"web": {"client_id": "cid", "client_secret": "sec"}})
        self.assertIn("Desktop app", str(caught.exception))

    def test_rejects_a_secret_missing_its_fields(self):
        with self.assertRaises(ya.ConfigError):
            ya.client_credentials({"installed": {"client_id": "cid"}})


class SaveTokenTest(unittest.TestCase):
    def test_writes_the_token_file_readable_only_by_its_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "youtube_token.json"
            ya.save_token(path, {"refresh_token": "rt"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_creates_the_parent_directory_private_to_its_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "youtube_token.json"
            ya.save_token(path, {"refresh_token": "rt"})
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_round_trips_the_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "youtube_token.json"
            ya.save_token(path, {"refresh_token": "rt", "scope": ya.SCOPE})
            self.assertEqual(ya.load_token(path)["refresh_token"], "rt")

    def test_tightens_permissions_on_an_existing_world_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "youtube_token.json"
            path.write_text("{}")
            os.chmod(path, 0o644)
            ya.save_token(path, {"refresh_token": "rt"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_keeps_only_the_fields_worth_persisting(self):
        token = ya.token_record({
            "refresh_token": "rt",
            "access_token": "at",
            "scope": ya.SCOPE,
            "token_type": "Bearer",
            "expires_in": 3599,
        })
        self.assertEqual(set(token), {"refresh_token", "scope", "obtained_at"})

    def test_refuses_a_grant_that_did_not_include_the_upload_scope(self):
        """Google renders per-scope checkboxes; an unticked one still yields a refresh token."""
        with self.assertRaises(ya.ConsentError) as caught:
            ya.token_record({
                "refresh_token": "rt",
                "scope": "https://www.googleapis.com/auth/userinfo.email",
            })
        self.assertIn("youtube.upload", str(caught.exception))

    def test_refuses_a_grant_that_reports_no_scope_at_all(self):
        with self.assertRaises(ya.ConsentError):
            ya.token_record({"refresh_token": "rt"})

    def test_accepts_a_grant_whose_scope_list_includes_the_upload_scope(self):
        record = ya.token_record({"refresh_token": "rt", "scope": f"openid {ya.SCOPE}"})
        self.assertEqual(record["refresh_token"], "rt")

    def test_refuses_a_grant_that_came_back_without_a_refresh_token(self):
        with self.assertRaises(ya.ConsentError) as caught:
            ya.token_record({"access_token": "at", "scope": ya.SCOPE})
        self.assertIn("refresh_token", str(caught.exception))


class UploadLoopTest(unittest.TestCase):
    """The chunk loop trusts the server's Range header, so it has to distrust a useless one."""

    def upload_against(self, responses, mime_type="video/mp4"):
        calls = iter(responses)

        class FakeOpener:
            def open(self, request):
                return next(calls)(request)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"x" * 1024)
            with mock.patch.object(ya, "_opener", FakeOpener):
                return ya.upload_file("https://upload.example/session", path, 1024, mime_type)

    def test_gives_up_when_a_308_never_advances(self):
        def stalled(request):
            raise urllib.error.HTTPError(
                request.full_url, 308, "Resume Incomplete", email.message.Message(), None
            )

        with self.assertRaises(ya.UploadError) as caught:
            self.upload_against([stalled, stalled])
        self.assertIn("no progress", str(caught.exception).lower())

    def test_sends_the_media_type_on_the_chunk_itself(self):
        """urllib defaults an un-typed body to form-encoded, which is not a video."""
        seen = {}

        def done(request):
            seen["type"] = request.get_header("Content-type")
            return contextlib.closing(io.BytesIO(b'{"id": "vid123"}'))

        self.upload_against([done], mime_type="video/mp4")
        self.assertEqual(seen["type"], "video/mp4")

    def test_returns_the_video_the_session_finishes_with(self):
        def done(request):
            return contextlib.closing(io.BytesIO(b'{"id": "vid123"}'))

        self.assertEqual(self.upload_against([done])["id"], "vid123")


class LoadTokenTest(unittest.TestCase):
    def test_reports_a_corrupt_token_file_as_a_config_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "youtube_token.json"
            path.write_text("{not json")
            with self.assertRaises(ya.ConfigError):
                ya.load_token(path)

    def test_reports_a_token_file_with_no_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "youtube_token.json"
            path.write_text('{"scope": "x"}')
            with self.assertRaises(ya.ConfigError):
                ya.load_token(path)

    def test_reports_a_missing_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ya.ConfigError):
                ya.load_token(Path(tmp) / "absent.json")


class MainTest(unittest.TestCase):
    def test_reports_a_missing_video_file_instead_of_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "client_secret.json"
            secret.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
            token = Path(tmp) / "youtube_token.json"
            token.write_text(json.dumps({"refresh_token": "rt", "scope": ya.SCOPE}))
            with mock.patch.object(ya, "refresh_access_token", lambda *a: {"access_token": "at"}):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    code = ya.main([
                        "--client-secret", str(secret), "--token", str(token),
                        "upload", str(Path(tmp) / "absent.mp4"),
                    ])
            self.assertEqual(code, 1)
            self.assertIn("error:", stderr.getvalue())


class VideoMetadataTest(unittest.TestCase):
    def test_uploads_are_private(self):
        body = ya.video_metadata(title="t", description="d")
        self.assertEqual(body["status"]["privacyStatus"], "private")

    def test_declares_synthetic_media(self):
        body = ya.video_metadata(title="t", description="d")
        self.assertTrue(body["status"]["containsSyntheticMedia"])

    def test_carries_title_and_description(self):
        body = ya.video_metadata(title="A paper", description="Body")
        self.assertEqual(body["snippet"]["title"], "A paper")
        self.assertEqual(body["snippet"]["description"], "Body")

    def test_serialises_to_json(self):
        json.dumps(ya.video_metadata(title="t", description="d"))


class ResumeOffsetTest(unittest.TestCase):
    def test_reads_the_next_byte_from_a_range_header(self):
        self.assertEqual(ya.parse_resume_offset("bytes=0-262143"), 262144)

    def test_treats_a_missing_range_header_as_nothing_stored(self):
        self.assertEqual(ya.parse_resume_offset(None), 0)

    def test_rejects_a_range_header_it_cannot_read(self):
        with self.assertRaises(ya.UploadError):
            ya.parse_resume_offset("bytes=weird")


class ContentRangeTest(unittest.TestCase):
    def test_formats_an_inclusive_range_over_the_total_size(self):
        self.assertEqual(ya.content_range(0, 262144, 1000000), "bytes 0-262143/1000000")

    def test_formats_a_final_short_chunk(self):
        self.assertEqual(ya.content_range(900000, 100000, 1000000), "bytes 900000-999999/1000000")


if __name__ == "__main__":
    unittest.main()
