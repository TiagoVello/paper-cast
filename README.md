# paper-cast

Drop a paper on your bar. Get back a private YouTube episode: two hosts talking
about it, steered at you.

paper-cast is an Omarchy plugin and a terminal command, in one repo. It takes a
Source — a PDF you drop, or one you name on the command line — sends it to
NotebookLM, muxes the audio overview over the paper's first page, and uploads the
Episode to your channel as private.

The point of it is the **Steering**: the instruction the hosts get before they
read. It is what stops them explaining what a transformer is to someone who has
implemented three.

And it's free. Well... until google decides to charge for NotebookLM. Enjoy until then :)

## Add the plugin

```bash
omarchy plugin add https://github.com/TiagoVello/paper-cast.git --enable
```

Then click the paper-cast icon on the bar and press **Run setup**.

Omarchy's plugin system clones the repo, checks its manifest, and runs nothing —
there is no hook it could run — so Setup is a command you run once, in a
terminal, and it opens one for you.

### Or from a terminal, with or without Omarchy

```bash
git clone https://github.com/TiagoVello/paper-cast.git ~/src/paper-cast
~/src/paper-cast/scripts/paper_cast.py setup
```

Those two lines are the whole of what a `curl … | bash` would do: every path
converges on the same `paper-cast setup`. Without Omarchy there is no bar icon,
and everything else works the same from the command line.

## Setup

```bash
paper-cast setup          # asks before each step
paper-cast setup --yes    # takes every step without asking
```

Six steps. Each one first checks whether it is already done and says so, so
running Setup again after a Ctrl-C, a failure or a year costs nothing and changes
nothing. It exits 0 when the machine can run a Job and 1 when something is still
outstanding, and names what.

| | Step | Already done when |
|---|---|---|
| 1 | `ffmpeg`, `poppler`, `uv` and `inotify-tools` from pacman; `nlm` from `uv tool install notebooklm-mcp-cli` | each program is on `PATH` |
| 2 | `nlm login`, once, in a visible browser | the stored NotebookLM session still refreshes |
| 3 | The YouTube credential — a ten-stage walk through the Google Cloud console | `client_secret.json` and `youtube_token.json` are both on disk |
| 4 | `~/.local/bin/paper-cast` | it is there and points at this checkout |
| 5 | Five starter Steering presets in your config, and which one is loaded | `~/.config/paper-cast/config.toml` exists |
| 6 | The bar widget | `omarchy plugin list` reports it enabled |

Of those, `inotify-tools` is the only one the pipeline never calls: it is how the
bar widget watches for a Job changing stage, so a headless machine running
paper-cast from a terminal can skip it. Everything else is the pipeline's.

Step 1 needs `sudo`, and steps 2 and 3 open a browser. Each says so before it
asks. Without a terminal to ask on, Setup refuses rather than guessing; that is
what `--yes` is for.

Step 3 is the long one — fifteen minutes of clicking, no existing Google Cloud
project assumed and no billing account needed. It remembers every answer, so
Ctrl-C and come back. `docs/youtube-oauth.md` is the same procedure written down.

The command Setup writes is four lines that `exec` the Python **inside this
checkout**. Nothing is copied out, and nothing is ever written back in, so
`omarchy plugin update` moves the command and the bar icon together and they can
never be from different commits.

## Using it

Drop one or more PDFs on the bar icon. That stages them and opens the panel; it
does not start anything, because choosing the Steering is the interesting part
and a drop should not skip it. Pick a Steering preset, press go, and the Job
joins the Queue. The icon changes colour while it runs.

The same thing without the bar:

```bash
paper-cast paper.pdf
paper-cast paper.pdf --title "Attention Is All You Need"
paper-cast paper.pdf --dry-run     # stop at the .mp4, before the upload
```

A Job takes about five minutes, nearly all of it NotebookLM generating. The
artifacts land in a Run directory named after the Episode, and a failed Job keeps
everything it made.

An arXiv paper needs no download of your own — paste the id or the link:

```bash
paper-cast queue add arxiv.org/abs/1706.03762
paper-cast queue add 2401.12345 cs.CL/0301001 --combine
paper-cast resolve arxiv.org/abs/1706.03762   # what a reference names, as JSON
```

Both id schemes, `/abs/` and `/pdf/` links with or without a version, and a
direct link to a `.pdf` anywhere. A DOI or a publisher's landing page is refused
rather than guessed at. A reference that names nothing fails when you add it, not
five minutes into the run. The PDF is fetched when the Job starts and lands in
the Run directory beside the Episode it produced, and arXiv's title is the one
used — most PDFs on arXiv carry no title of their own.

## Steering

Setup writes five presets — *ML researcher*, *Skim it*, *Critical read*, *Teach
me*, *Adjacent field* — into your own config file. They are a starting point, not
a fixed menu: the panel flips through them, adds and deletes them, and editing
the loaded text rewrites that preset in place.

```bash
paper-cast config get focus
paper-cast config set steering_preset 'Critical read'
paper-cast config set focus --stdin < my-steering.txt
```

Write as much as you like. The 500-character limit you may have met on
notebooklm.google.com is a cap on Google's own textarea; the path paper-cast uses
has no limit, and a 1,313-character Steering has been measured through it
byte for byte.

## Configuration

Everything about an Episode lives in `~/.config/paper-cast/config.toml`:
language, format, length, the Steering and its presets, where Run directories go,
and what to keep after an upload. `config.example.toml` is the documented shape.

```bash
paper-cast config list          # the whole file, defaults filled in
paper-cast config set length long
```

Unknown keys and invalid values are hard errors naming the key, because a
silently-ignored setting costs a full five-minute Job to discover.

## Removing it

Run this **first**, while the plugin is still there:

```bash
paper-cast uninstall
omarchy plugin remove io.github.tiagovello.paper-cast
```

That order matters. `paper-cast uninstall` is the CLI inside the plugin
checkout, and `omarchy plugin remove` deletes that checkout — afterwards there is
nothing left to run, and `~/.local/bin/paper-cast` is a wrapper pointing at a
directory that is gone.

`uninstall` takes the wrapper off your `PATH` and touches nothing else. It prints
what it is leaving: your config and the YouTube credential in
`~/.config/paper-cast/`, the Queue's memory in `~/.local/state/paper-cast/`, and
your Run directories. None of that comes back by re-running Setup, which is why
none of it is deleted for you. `nlm`, `ffmpeg`, `poppler` and `inotify-tools`
stay too — they are
system packages other things use.

## Requirements

Arch Linux, and Omarchy 4.0.0.alpha or later for the bar icon. A Google account
with a YouTube channel, and a desktop session on the same machine — the consent
flow opens a browser and catches the redirect on a loopback port, so there is no
headless path to the credential. Setup installs everything else.

The Python is standard library only: no virtualenv, no pip, nothing to keep
up to date but the checkout.

## Development

```bash
python3 -m unittest discover -s tests -q
```

`CONTEXT.md` is the vocabulary — Source, Job, Episode, Steering, Steering preset,
Queue, Run directory, Setup — and it is worth two minutes before reading the
code. `PRIVACY.md` is the policy the consent screen points at. Issues and the
decisions behind them live in GitHub issues.
