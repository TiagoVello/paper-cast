# YouTube OAuth bootstrap

One-time setup for the credential paper-cast uploads with. Half of it is console
work only a human can do; the other half is `scripts/youtube_auth.py`, which is
stdlib-only Python and needs nothing installed.

**Just run the wizard:**

```bash
scripts/bootstrap_youtube.sh
```

It walks all ten steps below — opening each console page, putting each value on
your clipboard, running the consent flow, and proving a private upload — and it
is resumable, so Ctrl-C and re-run costs nothing. The rest of this document is
what it does, for when you would rather do it by hand or need to check its work.

## 1. Console (manual)

No existing Google Cloud project is assumed, and no billing account is needed:
the YouTube Data API's free quota covers this and the console never asks for a
card. A first visit does ask you to accept the terms and pick a country.

1. **Create a GCP project** at <https://console.cloud.google.com/projectcreate>.
   Note the project id — it goes in the answer on the ticket.
2. **Enable the YouTube Data API v3**:
   <https://console.cloud.google.com/apis/library/youtube.googleapis.com>.
3. **Configure the consent screen** at
   <https://console.cloud.google.com/auth/overview> (Google Auth Platform), user
   type **External**. Declare the scope
   `https://www.googleapis.com/auth/youtube.upload` under *Data Access*. No test
   users are needed — step 4 publishes the app, which retires that list.
4. **Fill in Branding** at <https://console.cloud.google.com/auth/branding>.
   Production mode is refused without an app name, a user support email, a
   homepage URL **and** a privacy policy URL — the console says so only when you
   try to publish. Add the authorized domain *before* the URLs or it rejects
   them:

   | Field | Value |
   |---|---|
   | App name | `paper-cast` |
   | User support email | your own address (dropdown) |
   | Authorized domain | `github.com` |
   | Application home page | `https://github.com/TiagoVello/paper-cast` |
   | Privacy policy link | `https://github.com/TiagoVello/paper-cast/blob/main/PRIVACY.md` |
   | Terms of service | leave empty, not required |

   The policy is `PRIVACY.md` in this repo, so it has to be **pushed** before
   Google can fetch it.

5. **Publish the consent screen to production** — *Audience* → **Publish app** →
   confirm. The dialog mentions verification for sensitive scopes; confirm
   anyway. Do not skip this. A consent screen left in *Testing* issues refresh
   tokens that expire after **7 days**, so an unattended run would die weekly
   with `invalid_grant`. Publishing is one click and is **not** verification: no
   audit, no review. The app stays unverified (shows the "unverified app"
   warning at consent, capped at 100 lifetime users), which is fine for one user
   — and unverified projects force every upload to `private`, which is what we
   want anyway.
6. **Create an OAuth client** of type **Desktop app** under Credentials, download
   the JSON, and put it where the script looks:

   ```bash
   mkdir -p ~/.config/paper-cast
   mv ~/Downloads/client_secret_*.json ~/.config/paper-cast/client_secret.json
   chmod 600 ~/.config/paper-cast/client_secret.json
   ```

## 2. Consent flow

```bash
scripts/youtube_auth.py authorize
```

Opens a browser, catches Google's redirect on a loopback port, and writes the
refresh token to `~/.config/paper-cast/youtube_token.json` with mode `600`. The
request asks for `access_type=offline` and `prompt=consent`, which is what makes
a refresh token come back at all — and come back again on a re-authorisation.

At the consent screen, click through the "Google hasn't verified this app"
warning (Advanced → Go to …). That warning is the published-but-unverified
state, and is expected — it is the price of not doing a compliance audit, and it
is also why uploads are force-locked to private. Leave the YouTube permission **ticked** — Google shows it
as a checkbox, and unticking it still returns a refresh token, just one that
cannot upload. `authorize` refuses a grant without the upload scope rather than
letting that surface as a 403 days later.

Check the stored token still works at any time:

```bash
scripts/youtube_auth.py refresh
```

## 3. Prove a private upload

Make a throwaway clip and send it:

```bash
ffmpeg -f lavfi -i color=c=0x1d2021:s=1280x720 -f lavfi -i anullsrc=r=48000:cl=stereo \
  -t 5 -c:v libx264 -tune stillimage -pix_fmt yuv420p -r 2 -g 4 \
  -c:a aac -b:a 128k -ar 48000 -movflags +faststart /tmp/paper-cast-test.mp4

scripts/youtube_auth.py upload /tmp/paper-cast-test.mp4 --title "paper-cast test"
```

It prints the video id, the privacy status the API reports back, and a Studio
link. Delete the video in Studio afterwards.

## Files

| Path | Contents | Mode |
|---|---|---|
| `~/.config/paper-cast/client_secret.json` | Desktop OAuth client id and secret | 600 |
| `~/.config/paper-cast/youtube_token.json` | refresh token, granted scope, timestamp | 600 |

Neither file belongs in the repo. Both are per-machine.

## From the pipeline

`scripts/paper_cast.py` imports this file rather than shelling out to it — both live
in `scripts/`, and a `~/.local/bin/paper-cast` symlink still resolves `sys.path[0]`
to that directory, so a bare `import youtube_auth` works from the entry point.

```python
import youtube_auth

youtube_auth.check_credential()        # pre-flight, before ~5 minutes of generation
...
video = youtube_auth.upload_private(mp4, title, description)
```

`check_credential()` exists because the expensive half of a run comes first: an
expired or revoked token should cost two seconds, not a full NotebookLM cycle. It
throws its access token away — the upload refreshes for itself.

`upload_private()` is the pipeline's entire view of YouTube; it never handles a
token. Titles go through `sanitize_title()` on the way in, which strips the `<`
and `>` YouTube rejects and truncates at 100 characters — `pdfinfo` titles and
filename stems are arbitrary, and that 400 would otherwise land *after* the
episode was generated.

An upload that fails is not retried. The `.mp4` survives in the run directory
(artifacts are never deleted on failure), so the retry is the `upload` subcommand
above, by hand, on the file that is already there.

## Tests

```bash
python3 -m unittest discover -s tests
```

Covers the parts that fail quietly: the consent URL's parameters, PKCE, the
loopback callback (state mismatch, a denied consent, and what it reflects back
into the browser), token file permissions, the `videos.insert` body, and the
resumable-upload offsets. Google itself is only exercised by the manual
bootstrap above.

## How Setup runs it

`paper-cast setup` is the integration (#12). Step 3 of the wizard checks whether
`client_secret.json` and `youtube_token.json` are both on disk and, if they are
not, hands the terminal to `bash scripts/bootstrap_youtube.sh` after warning what
it is about to cost. Once the credential exists the step is skipped entirely, so
the ten stages below are a once-per-machine thing.

What that integration had to know, and what was changed to make it work:

**Run it as a subprocess, never `source` it.** It sets `set -euo pipefail` and
calls `exit 1` on its give-up paths (no project id, no client secret found,
consent failed). Sourced, each of those kills the installer instead.

**It needs a real TTY.** Every stage blocks on `read`. Piping an installer
straight into a shell (`curl … | bash`) hands the script's own text to `read`
and the wizard eats the rest of the installer. Either require an interactive
run, or redirect explicitly: `bash scripts/bootstrap_youtube.sh < /dev/tty`.

**It needs a desktop session on the same machine.** The consent flow opens a
browser and catches the redirect on a loopback port, so the browser and the
script must share a host. There is no headless or CI path — that is inherent to
the credential, not a limitation of this script.

**Exit status is only 0 or 1**, and `1` means "incomplete but resumable", not
"broken". Answers live in `~/.config/paper-cast/bootstrap.env`, so a re-run
picks up where it stopped. If the installer wants to branch on *why* it stopped,
give the give-up paths distinct exit codes first.

**Stage 9 uploads a real video**, against a 100/day `videos.insert` budget. It
now remembers that it has: a re-run that already has a `TEST_VIDEO_ID` in
`bootstrap.env` offers to skip the upload and runs the cheap health check
instead, which is what makes the stage safe to reach twice.

```bash
python3 scripts/youtube_auth.py refresh   # exit 0 == the credential still works
```

**Stage 4's `git push` offer is guarded.** Only the repo's own author can push
it, and a plugin checkout is a clone of somebody else's repo — so the offer
appears only when there is an `origin`, an upstream branch, and something to
push. Everyone else falls through to paper-cast's own published `PRIVACY.md`,
which is the right URL for a consent screen to carry anyway: the policy
describes what the program does with your data, and it is the same program.

**Requirements**: `python3`, `ffmpeg`, `curl`, GNU `stat` (`-c`), `awk`, `sed`,
`mktemp` — checked in a preflight before stage 1 now, rather than failing at
stage 9 after a quarter of an hour of console work. Optional: `git` (URL
derivation and the push offer), `wl-copy` (clipboard; silently prints the value
instead on X11 or over SSH), and `xdg-open`/`wslview`/`open` (the wizard warns
and prints the URL if none exist). `paper-cast setup` installs the required ones
in its step 1, which is why step 3 comes after it.

**It writes only to `~/.config/paper-cast/` and `$TMPDIR`** — never into the
repo. `ENV_FILE` is assigned inside the script, so exporting it from an
installer has no effect.

**The stage library above the `STAGES` marker is generated** by the `/wizard`
skill and is identical in every wizard; don't hand-edit it. If you add a stage,
update `TOTAL_STAGES` to match or the progress counter lies.

**The console URLs were verified on 2026-09-12.** Google reorganised this area
once already (the OAuth consent screen became Google Auth Platform); if a stage
opens a 404, the page moved and the step needs re-checking against the docs.
