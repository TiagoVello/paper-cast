# Privacy Policy

_Last updated: 12 September 2026_

paper-cast is a personal command-line tool, run by one person on their own
computer. It turns a research paper PDF into an audio overview and uploads it as
a **private** video to that person's own YouTube channel. There is no service,
no server, and no other users.

## What it accesses

- **One Google permission**: `https://www.googleapis.com/auth/youtube.upload` —
  permission to upload a video to the channel of the person who authorised it.
  paper-cast does not read anything from the Google account: not profile
  details, not email, not existing videos.
- **Files you point it at**: the PDF you name on the command line, and the audio
  and video it generates from that PDF.

## Where things are stored

Everything stays on the operator's own machine:

| What | Where |
|---|---|
| OAuth client secret | `~/.config/paper-cast/client_secret.json`, readable only by that user |
| Refresh token | `~/.config/paper-cast/youtube_token.json`, readable only by that user |
| PDFs, audio, video | wherever the operator put them |

The only data that leaves the machine is the video, uploaded to the operator's
own YouTube channel as private, and the PDF content sent to the audio generator
the operator has configured.

## What it does not do

- No analytics, telemetry, tracking, advertising, or profiling.
- No data sold, shared, or transferred to anyone.
- No collection of anything from anyone other than the person running it.
- No accounts, no sign-ups, no third-party recipients beyond the Google APIs the
  operator explicitly authorises.

## Removing your data

- Delete `~/.config/paper-cast/` to remove the stored credentials.
- Revoke the app's access at <https://myaccount.google.com/permissions>.
- Delete any uploaded videos in YouTube Studio.

## Contact

Open an issue at <https://github.com/TiagoVello/paper-cast/issues>.
