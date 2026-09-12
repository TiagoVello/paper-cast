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

## Tests

```bash
python3 -m unittest discover -s tests
```

Covers the parts that fail quietly: the consent URL's parameters, PKCE, the
loopback callback (state mismatch, a denied consent, and what it reflects back
into the browser), token file permissions, the `videos.insert` body, and the
resumable-upload offsets. Google itself is only exercised by the manual
bootstrap above.
