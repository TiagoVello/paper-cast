#!/usr/bin/env python3
"""Bootstrap and exercise the YouTube credential paper-cast uploads with.

Stdlib only, per the standing decisions on the map. Three subcommands:

    authorize   run the loopback consent flow once, persist the refresh token 0600
    refresh     trade the refresh token for an access token, to prove it still works
    upload      upload a video as private, to prove the whole chain works

The console half of the bootstrap (GCP project, Desktop OAuth client, and
publishing the consent screen to production so the refresh token does not expire
after seven days) is manual; see docs/youtube-oauth.md.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.server
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "paper-cast"
DEFAULT_CLIENT_SECRET = CONFIG_DIR / "client_secret.json"
DEFAULT_TOKEN = CONFIG_DIR / "youtube_token.json"

# The resumable protocol wants every chunk but the last to be a multiple of 256 KiB.
CHUNK_SIZE = 8 * 1024 * 1024


class ConfigError(Exception):
    """The client secret on disk is not the thing we need."""


class ConsentError(Exception):
    """The consent flow came back with something other than a usable grant."""


class UploadError(Exception):
    """The upload session did not behave the way the resumable protocol says."""


class StateMismatch(ConsentError):
    """A loopback request that is not our callback: ignore it, keep waiting for the real one."""


# --- client secret ----------------------------------------------------------


def client_credentials(secret: dict[str, Any]) -> tuple[str, str]:
    """Pull (client_id, client_secret) out of a Desktop app client secret."""
    if "installed" not in secret:
        kind = ", ".join(secret) or "nothing"
        raise ConfigError(
            f"client secret holds {kind}; paper-cast needs an OAuth client of type "
            "Desktop app, whose secret is keyed 'installed'"
        )
    block = secret["installed"]
    missing = [field for field in ("client_id", "client_secret") if not block.get(field)]
    if missing:
        raise ConfigError(f"client secret is missing {', '.join(missing)}")
    return block["client_id"], block["client_secret"]


def read_client_secret(path: Path) -> tuple[str, str]:
    try:
        secret = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no client secret at {path}; see docs/youtube-oauth.md") from None
    except json.JSONDecodeError as err:
        raise ConfigError(f"client secret at {path} is not JSON: {err}") from None
    return client_credentials(secret)


# --- consent flow -----------------------------------------------------------


def new_code_verifier() -> str:
    """A PKCE verifier: RFC 7636 wants 43-128 unreserved characters."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """The S256 challenge: base64url of the verifier's SHA-256, padding stripped."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def build_auth_url(client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    """The consent URL, with the parameters that decide whether a refresh token comes back."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            # The code comes back over plaintext loopback HTTP, so PKCE binds it to
            # this process: anything that intercepts the code cannot redeem it.
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # offline is what asks for a refresh token at all; consent is what makes
            # Google hand one over again on a re-authorisation instead of staying silent.
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"{AUTH_ENDPOINT}?{query}"


def parse_callback(path: str, expected_state: str) -> str:
    """Read the authorisation code out of the loopback request Google redirects to."""
    params = parse_qs(urlsplit(path).query)
    state = params.get("state", [None])[0]
    if state != expected_state:
        # Deliberately does not name the expected state: this message is rendered
        # back to whoever made the request, and that may not be Google.
        raise StateMismatch(f"callback state {state!r} is not the one this flow issued")
    if "error" in params:
        raise ConsentError(f"consent failed: {params['error'][0]}")
    code = params.get("code", [None])[0]
    if not code:
        raise ConsentError(f"callback carried no authorisation code: {path}")
    return code


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot handler: records the outcome on the server and says so in the browser."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        if not urlsplit(self.path).query:
            # The browser also asks for /favicon.ico; that is not the redirect.
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            self.server.code = parse_callback(self.path, self.server.state)  # type: ignore[attr-defined]
            message = "paper-cast is authorised. You can close this tab."
        except StateMismatch as err:
            # Someone else's request, or a replayed old one. Do not end the flow over it.
            message = str(err)
        except ConsentError as err:
            self.server.error = err  # type: ignore[attr-defined]
            message = f"Authorisation failed: {err}"
        body = f"<!doctype html><meta charset=utf-8><p>{html.escape(message)}</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """Keep the handler quiet; the command reports its own progress."""


def run_consent_flow(client_id: str, client_secret: str, timeout: float = 300.0) -> dict[str, Any]:
    """Open the browser, catch the loopback redirect, and exchange the code for tokens."""
    state = secrets.token_urlsafe(24)
    verifier = new_code_verifier()
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.state = state  # type: ignore[attr-defined]
    server.code = None  # type: ignore[attr-defined]
    server.error = None  # type: ignore[attr-defined]
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"

    url = build_auth_url(client_id, redirect_uri, state, code_challenge(verifier))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(f"Opening the consent screen. If nothing opens, visit:\n\n  {url}\n")
        webbrowser.open(url)
        deadline = time.monotonic() + timeout
        while server.code is None and server.error is None:  # type: ignore[attr-defined]
            if time.monotonic() > deadline:
                raise ConsentError(f"no callback within {timeout:.0f}s")
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()

    if server.error is not None:  # type: ignore[attr-defined]
        raise server.error  # type: ignore[attr-defined]
    return exchange_code(client_id, client_secret, server.code, redirect_uri, verifier)  # type: ignore[attr-defined]


def exchange_code(
    client_id: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str
) -> dict[str, Any]:
    return _post_form(
        TOKEN_ENDPOINT,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict[str, Any]:
    return _post_form(
        TOKEN_ENDPOINT,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )


# --- token storage ----------------------------------------------------------


def token_record(grant: dict[str, Any]) -> dict[str, Any]:
    """What is worth keeping from a grant: the access token expires in an hour, the refresh token does not."""
    if not grant.get("refresh_token"):
        raise ConsentError(
            "grant came back without a refresh_token; re-run with a consent screen that "
            "has not already been authorised, or revoke the app's access and try again"
        )
    granted = grant.get("scope", "")
    if SCOPE not in granted.split():
        raise ConsentError(
            f"grant does not carry {SCOPE}; it came back with {granted or 'no scope at all'}. "
            "Re-run and leave the YouTube permission ticked at the consent screen."
        )
    return {
        "refresh_token": grant["refresh_token"],
        "scope": granted,
        "obtained_at": int(time.time()),
    }


def save_token(path: Path, record: dict[str, Any]) -> None:
    """Persist the token, readable only by its owner, in a directory that is too."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    # Open at 0600 rather than chmod afterwards: a refresh token should never exist,
    # even for an instant, in a file anyone else on the box can read.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
    os.chmod(path, 0o600)  # O_CREAT leaves an existing file's mode alone.


def load_token(path: Path) -> dict[str, Any]:
    try:
        token = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no token at {path}; run `youtube_auth.py authorize` first") from None
    except json.JSONDecodeError as err:
        raise ConfigError(f"token at {path} is not JSON: {err}") from None
    if not token.get("refresh_token"):
        raise ConfigError(f"token at {path} holds no refresh_token; re-run `youtube_auth.py authorize`")
    return token


# --- upload -----------------------------------------------------------------


def video_metadata(title: str, description: str) -> dict[str, Any]:
    """The videos.insert body. Private forever, and honest about being machine-made."""
    return {
        "snippet": {"title": title, "description": description},
        "status": {
            # Unverified projects force uploads private anyway; we ask for what we want.
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }


def content_range(offset: int, length: int, total: int) -> str:
    return f"bytes {offset}-{offset + length - 1}/{total}"


def parse_resume_offset(range_header: str | None) -> int:
    """Turn a 308's `Range: bytes=0-262143` into the next byte the server wants."""
    if not range_header:
        return 0
    try:
        return int(range_header.split("-")[-1]) + 1
    except ValueError:
        raise UploadError(f"cannot read resume position from Range: {range_header}") from None


def start_resumable_session(access_token: str, metadata: dict[str, Any], size: int, mime_type: str) -> str:
    url = f"{UPLOAD_ENDPOINT}?{urlencode({'uploadType': 'resumable', 'part': 'snippet,status'})}"
    request = urllib.request.Request(
        url,
        data=json.dumps(metadata).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": mime_type,
        },
    )
    with _opener().open(request) as response:
        session_url = response.headers.get("Location")
    if not session_url:
        raise UploadError("videos.insert accepted the metadata but returned no upload session URL")
    return session_url


def upload_file(session_url: str, path: Path, size: int, mime_type: str) -> dict[str, Any]:
    """PUT the file into the session, following the server's own idea of where it is."""
    offset = 0
    with path.open("rb") as handle:
        while offset < size:
            handle.seek(offset)
            chunk = handle.read(CHUNK_SIZE)
            request = urllib.request.Request(
                session_url,
                data=chunk,
                method="PUT",
                headers={
                    "Content-Range": content_range(offset, len(chunk), size),
                    # urllib would otherwise label the body form-encoded.
                    "Content-Type": mime_type,
                },
            )
            try:
                with _opener().open(request) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as err:
                if err.code != 308:
                    raise _http_error(err) from None
                stored = parse_resume_offset(err.headers.get("Range"))
                if stored <= offset:
                    raise UploadError(
                        f"upload session made no progress: still at byte {stored} "
                        f"after sending {len(chunk)} bytes from {offset}"
                    ) from None
                offset = stored
            print(f"  {offset}/{size} bytes", file=sys.stderr)
    raise UploadError("sent the whole file but the session never returned a video")


def upload_video(access_token: str, path: Path, title: str, description: str) -> dict[str, Any]:
    size = path.stat().st_size
    mime_type = mimetypes.guess_type(path.name)[0] or "video/*"
    session_url = start_resumable_session(access_token, video_metadata(title, description), size, mime_type)
    return upload_file(session_url, path, size, mime_type)


# --- HTTP -------------------------------------------------------------------


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A 308 from an upload session means 'resume here', not 'go there'."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirects)


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urlencode(fields).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with _opener().open(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        raise _http_error(err) from None


def _http_error(err: urllib.error.HTTPError) -> Exception:
    """Google puts the useful half of a failure in the body, so carry it along."""
    body = err.read().decode("utf-8", "replace").strip()
    detail = f"{err.code} {err.reason}"
    if body:
        detail = f"{detail}: {body}"
    if "invalid_grant" in body:
        detail += (
            "\n\ninvalid_grant usually means the refresh token expired, which happens after "
            "7 days while the consent screen is still in Testing. Publish it to production "
            "and re-run `authorize`."
        )
    return ConsentError(detail) if err.code in (400, 401) else UploadError(detail)


# --- commands ---------------------------------------------------------------


def cmd_authorize(args: argparse.Namespace) -> int:
    client_id, client_secret = read_client_secret(args.client_secret)
    record = token_record(run_consent_flow(client_id, client_secret))
    save_token(args.token, record)
    print(f"Refresh token saved to {args.token} (mode 600).")
    print(f"Scope granted: {record['scope']}")
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    client_id, client_secret = read_client_secret(args.client_secret)
    token = load_token(args.token)
    grant = refresh_access_token(client_id, client_secret, token["refresh_token"])
    print(f"Access token acquired, valid {grant.get('expires_in', '?')}s.")
    print(f"Scope: {grant.get('scope', '?')}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    client_id, client_secret = read_client_secret(args.client_secret)
    token = load_token(args.token)
    grant = refresh_access_token(client_id, client_secret, token["refresh_token"])
    if not grant.get("access_token"):
        raise ConsentError(f"token endpoint returned no access token: {grant}")
    video = upload_video(grant["access_token"], args.file, args.title, args.description)
    video_id = video.get("id", "?")
    print(f"Uploaded {video_id} as {video.get('status', {}).get('privacyStatus', '?')}.")
    print(f"https://studio.youtube.com/video/{video_id}/edit")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-secret", type=Path, default=DEFAULT_CLIENT_SECRET)
    parser.add_argument("--token", type=Path, default=DEFAULT_TOKEN)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("authorize", help="run the loopback consent flow and store the refresh token").set_defaults(
        func=cmd_authorize
    )
    sub.add_parser("refresh", help="check the stored refresh token still buys an access token").set_defaults(
        func=cmd_refresh
    )
    upload = sub.add_parser("upload", help="upload a video as private")
    upload.add_argument("file", type=Path)
    upload.add_argument("--title", default="paper-cast test upload")
    upload.add_argument("--description", default="Throwaway upload proving the paper-cast credential works.")
    upload.set_defaults(func=cmd_upload)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    # OSError covers urllib's URLError (no network, DNS, TLS) and a missing video file.
    except (ConfigError, ConsentError, UploadError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
