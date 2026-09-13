#!/usr/bin/env python3
"""`paper-cast setup` — the wizard — and `paper-cast uninstall`.

Omarchy 4.0.0.alpha clones a plugin's repo, validates `manifest.json`, and runs
nothing: no install hook, no dependency declaration, no post-install script
(#10). Setup is therefore a command the user runs — from the panel's Run setup
button, from the README, or from a `curl | bash` that converges here.

Six steps. Each is a detector first and an action second, so a second run says
"already done" six times and changes nothing; that is the acceptance criterion
on #12. Nothing is detected by remembering that we did it — there is no stamp
file — because the thing to know is whether the machine is ready now, not
whether Setup once ran on it.

Interactive by default, `--yes` for scripted callers, and a hard failure rather
than a guess when there is no TTY: `omarchy-plugin-add` is the template, `gum`
included. `sudo` and a browser login are announced before they happen; running
`pacman` at all is not something an Arch-only plugin should be coy about.

Nothing here writes inside the plugin checkout. `omarchy plugin update` is
`git merge --ff-only` and refuses a dirty tree (#10), so a Setup that dropped a
file in the repo would break updates for good.

Stdlib only, per the standing decisions on the map.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import youtube_auth
from config_cli import STARTER_PRESETS, loaded_text, save_config, write_fresh_config
from paper_cast import CONFIG_DIR, CONFIG_FILE, DEFAULTS, ConfigError, load_config

PLUGIN_ID = "io.github.tiagovello.paper-cast"

# The checkout this file is in — the plugin directory itself, once Omarchy has
# cloned it. Resolved, so the wrapper records where the code really is even when
# reached through a symlink.
CHECKOUT = Path(__file__).resolve().parent.parent
ENTRY_POINT = CHECKOUT / "scripts" / "paper_cast.py"
BOOTSTRAP_YOUTUBE = CHECKOUT / "scripts" / "bootstrap_youtube.sh"

# Where a user-installed command goes, and where `uv tool install` puts one too,
# so step 1 and step 4 land in the same place and one PATH warning covers both.
BIN_DIR = Path.home() / ".local" / "bin"
WRAPPER = BIN_DIR / "paper-cast"

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "paper-cast"

# The two halves of the YouTube credential, named here because step 3 checks for
# them and `uninstall` says where they are being left.
CLIENT_SECRET = youtube_auth.DEFAULT_CLIENT_SECRET
TOKEN_FILE = youtube_auth.DEFAULT_TOKEN

# The pacman half of step 1: a package, and the programs that prove it is there.
# Detection is by binary on PATH rather than by asking pacman, because what the
# pipeline needs is the binary — `paper_cast.missing_tools` checks the same way,
# and a poppler installed from source would otherwise be reinstalled every run.
PACMAN_PACKAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ffmpeg", ("ffmpeg",)),
    ("poppler", ("pdfinfo", "pdftoppm")),
    # uv is not a paper-cast dependency; it is how `nlm` is distributed, below.
    ("uv", ("uv",)),
    # The bar widget's, not the pipeline's: #14's QueueState.qml shells out to
    # `inotifywait` to watch the state directory, and it is not in a base Arch
    # install. Without it the icon loads and its colour never changes, which is
    # the entire state channel. Deliberately not in `paper_cast.REQUIRED_TOOLS`
    # — that tuple is the pipeline's preflight, and a headless box should not be
    # refused a Job over a package the pipeline never invokes.
    ("inotify-tools", ("inotifywait",)),
)

# `nlm` ships as a uv tool. Not pip, not pacman, not the AUR: `uv tool install`
# is the only documented channel, and it puts the binary in ~/.local/bin.
NLM_PACKAGE = "notebooklm-mcp-cli"

# What a step can report. "satisfied" and "done" both mean the machine is ready;
# the distinction is only there so a second run can say which it was.
SATISFIED = "satisfied"
DONE = "done"
OUTSTANDING = "outstanding"
NOT_APPLICABLE = "n/a"


class SetupError(Exception):
    """Setup cannot go on: no TTY and no --yes, or a step failed outright."""


# --- talking to the user ----------------------------------------------------


def say(message: str = "") -> None:
    print(message, flush=True)


# Bold when a terminal is watching, nothing at all when the output is a log.
BOLD, RESET = ("\033[1m", "\033[0m") if sys.stdout.isatty() else ("", "")


def heading(index: int, total: int, title: str) -> None:
    say(f"\n{BOLD}\u25b8 {index}/{total} \u00b7 {title}{RESET}")


def note(message: str) -> None:
    say(f"  {message}")


def warn(message: str) -> None:
    print(f"  ⚠ {message}", file=sys.stderr, flush=True)


def announce(lines: list[str]) -> None:
    """Say exactly what is about to happen, before the confirm that allows it.

    The house rule from `omarchy-plugin-add`: a step that wants `sudo` or opens a
    browser prints a plain block naming what it will do first, so the password
    prompt or the Google consent screen is never the first news of it.
    """
    for line in lines:
        print(f"  {line}", file=sys.stderr, flush=True)


class Prompt:
    """Confirm before each group, and never guess — `omarchy-plugin-add`'s rules.

    `gum` is Omarchy's, not ours: the panel launches Setup through
    `omarchy-launch-floating-terminal-with-presentation`, which sources
    `omarchy-restart-gum` and so hands us a themed `gum` for free. A machine
    reached by `curl | bash` may have no `gum` at all, which is why there is a
    plain-stdin path underneath rather than a hard dependency.
    """

    def __init__(
        self,
        assume_yes: bool = False,
        interactive: bool | None = None,
        gum: str | None | Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.assume_yes = assume_yes
        self.interactive = is_interactive() if interactive is None else interactive
        self.gum = gum("gum") if callable(gum) else gum

    def confirm(self, question: str) -> bool:
        """Yes, no, or a refusal to invent an answer."""
        if self.assume_yes:
            return True
        if not self.interactive:
            # The one thing a wizard must not do unattended. #12: hard-fail
            # rather than guess when there is no TTY.
            raise SetupError(
                f"no terminal to ask {question!r} on; pass --yes to take every step without asking"
            )
        if self.gum:
            return subprocess.run([self.gum, "confirm", question]).returncode == 0
        return input(f"  ? {question} [y/N] ").strip().lower().startswith("y")

    def choose(self, header: str, options: list[str], selected: str) -> str:
        """One of `options`. `selected` is what a caller who cannot be asked gets."""
        if self.assume_yes or not self.interactive or not self.gum:
            return selected
        chosen = subprocess.run(
            [self.gum, "choose", f"--header={header}", "--selected", selected],
            input="\n".join(options),
            capture_output=True,
            text=True,
        )
        # A cancelled gum exits non-zero, and cancelling means "leave it as it is".
        if chosen.returncode != 0:
            return selected
        return chosen.stdout.strip() or selected


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def run_visible(command: list[str]) -> int:
    """Run a foreign program with its output left on the terminal.

    Unlike the pipeline's `run_step`, nothing is captured: `pacman` asks
    questions, `uv tool install` draws progress, and the user is sitting there.
    """
    note(f"$ {shlex.join(command)}")
    return subprocess.run(command).returncode


def run_quiet(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, errors="replace")


# --- step 1: prerequisites --------------------------------------------------


def missing_packages(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """The pacman packages whose programs are not on PATH."""
    return [
        package
        for package, programs in PACMAN_PACKAGES
        if any(which(program) is None for program in programs)
    ]


def pacman_command(packages: list[str], assume_yes: bool = False) -> list[str]:
    """`--needed` so an already-installed package is not reinstalled, and `sudo`
    because that is what installing a package costs; `--noconfirm` only for a
    caller that has already said yes to everything."""
    return ["sudo", "pacman", "-S", "--needed", *(["--noconfirm"] if assume_yes else []), *packages]


def uv_tool_install_command() -> list[str]:
    return ["uv", "tool", "install", NLM_PACKAGE]


def step_prerequisites(prompt: Prompt) -> str:
    packages = missing_packages(shutil.which)
    nlm_missing = shutil.which("nlm") is None
    if not packages and not nlm_missing:
        note("ffmpeg, poppler, uv, inotify-tools and nlm are all on PATH.")
        return SATISFIED

    if packages:
        announce(
            [
                f"About to install with pacman, which needs sudo: {', '.join(packages)}.",
                "paper-cast is an Arch/Omarchy plugin, so these come from the system's",
                "own package manager. Your password is pacman's to ask for, not ours.",
            ]
        )
        if prompt.confirm(f"Install {', '.join(packages)} with pacman?"):
            if run_visible(pacman_command(packages, prompt.assume_yes)) != 0:
                raise SetupError("pacman did not install the prerequisites; see its output above")
        else:
            warn(f"skipped: {', '.join(packages)}. A Job cannot run without them.")
            return OUTSTANDING

    if nlm_missing:
        if shutil.which("uv") is None:
            warn("uv is not on PATH, so nlm cannot be installed.")
            return OUTSTANDING
        announce(
            [
                f"About to run `uv tool install {NLM_PACKAGE}`, which installs `nlm`",
                f"into {BIN_DIR}. No sudo. That package is how the NotebookLM CLI is",
                "distributed — it is on neither PyPI-as-nlm nor in the Arch repos.",
            ]
        )
        if not prompt.confirm(f"Install nlm with `uv tool install {NLM_PACKAGE}`?"):
            warn("skipped: nlm. It is the program that talks to NotebookLM.")
            return OUTSTANDING
        if run_visible(uv_tool_install_command()) != 0:
            raise SetupError(f"`uv tool install {NLM_PACKAGE}` failed; see its output above")
        ensure_bin_on_path()
    return DONE


def ensure_bin_on_path() -> None:
    """Make a just-installed `nlm` findable for the rest of this run.

    `uv tool install` puts it in ~/.local/bin, and a shell that started before
    that directory was on PATH does not gain it by us writing a file there — so
    step 2 would report the `nlm` step 1 had just installed as missing. This
    fixes only this process; step 4 is what tells the user to fix their shell.
    """
    if not on_path(BIN_DIR):
        os.environ["PATH"] = f"{BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}"


# --- step 2: the NotebookLM session -----------------------------------------


def notebooklm_ready(run: Callable[[list[str]], Any] = run_quiet) -> bool:
    """Whether the stored NotebookLM session still works.

    `nlm auth refresh` is the same probe the pipeline makes before it touches a
    PDF (#7), so Setup and a run agree on what "logged in" means. There is no
    session file to stat: only the server can say.
    """
    return run(["nlm", "auth", "refresh"]).returncode == 0


def step_notebooklm(prompt: Prompt) -> str:
    if shutil.which("nlm") is None:
        warn("nlm is not installed, so there is no session to log in to. Step 1 first.")
        return OUTSTANDING
    if notebooklm_ready(run_quiet):
        note("The NotebookLM session is live.")
        return SATISFIED

    announce(
        [
            "About to run `nlm login`, which opens a visible browser window and asks",
            "you to sign in to the Google account whose NotebookLM will generate the",
            "episodes. The session is stored by nlm, not by paper-cast. Once only.",
        ]
    )
    if not prompt.confirm("Log in to NotebookLM in a browser now?"):
        warn("skipped: the NotebookLM session. Run `nlm login` before the first Job.")
        return OUTSTANDING
    if run_visible(["nlm", "login"]) != 0 or not notebooklm_ready(run_quiet):
        raise SetupError("`nlm login` did not leave a working session; try it again by hand")
    return DONE


# --- step 3: the YouTube credential -----------------------------------------


def youtube_ready(client_secret: Path = CLIENT_SECRET, token: Path = TOKEN_FILE) -> bool:
    """Both halves of the credential on disk.

    Deliberately not `youtube_auth.check_credential()`: that spends a network
    round trip refreshing the token, and a detector that a Job is about to run
    anyway should not. A token that has been revoked since is the pipeline's own
    pre-flight to catch, with a better error than Setup could give.
    """
    return client_secret.is_file() and token.is_file()


def step_youtube(prompt: Prompt) -> str:
    if youtube_ready(CLIENT_SECRET, TOKEN_FILE):
        note(f"The YouTube credential is in {CONFIG_DIR}.")
        return SATISFIED
    if not BOOTSTRAP_YOUTUBE.is_file():
        raise SetupError(f"{BOOTSTRAP_YOUTUBE} is missing from the checkout")

    announce(
        [
            "About to run the YouTube bootstrap: a ten-stage wizard that opens the",
            "Google Cloud console page by page, creates a project and an OAuth client",
            "from nothing, and finishes with one browser consent and one throwaway",
            "private upload to prove the credential works.",
            "",
            "It is the longest step by far — fifteen minutes of clicking — and the",
            "only one that needs a Google account. Ctrl-C is safe: it remembers every",
            "answer, and this step is skipped entirely once the credential exists.",
        ]
    )
    if not prompt.confirm("Run the YouTube bootstrap now?"):
        warn(f"skipped: the YouTube credential. Run {BOOTSTRAP_YOUTUBE} when you have time.")
        return OUTSTANDING
    # Run, never source, and let it own the terminal: every stage blocks on
    # `read`, and it needs the real TTY (docs/youtube-oauth.md).
    if run_visible(["bash", str(BOOTSTRAP_YOUTUBE)]) != 0 or not youtube_ready(CLIENT_SECRET, TOKEN_FILE):
        warn("The YouTube bootstrap stopped early. Re-run setup; it picks up where it left off.")
        return OUTSTANDING
    return DONE


# --- step 4: the wrapper ----------------------------------------------------

# Stamped into the wrapper so `uninstall` can tell a file it wrote from a
# `paper-cast` somebody else put on PATH, and refuse to delete the latter.
WRAPPER_MARKER = "# Written by `paper-cast setup`"


def wrapper_text(entry_point: Path = ENTRY_POINT) -> str:
    """The wrapper, which execs the Python *inside the checkout*.

    Not a copy and not an installed package: the CLI runs where `omarchy plugin
    update` will update it, so the command and the QML move together and can
    never be from different commits (#10). A symlink would do the same job, but a
    script can be read to find out where it points and can carry the marker.
    """
    return f"""#!/usr/bin/env bash
{WRAPPER_MARKER} (#12). Remove it with `paper-cast uninstall`.
#
# Execs the CLI where it lies inside the plugin checkout, so that `git pull`
# moves this command and the bar widget together.
exec python3 {shlex.quote(str(entry_point))} "$@"
"""


def wrapper_is_current(path: Path = WRAPPER, entry_point: Path = ENTRY_POINT) -> bool:
    """True when the wrapper on disk is ours and points at this checkout.

    A wrapper left by an older checkout — the plugin cloned somewhere else, then
    moved — reads as not current and is rewritten, which is the whole reason this
    compares contents instead of just calling `path.exists()`.
    """
    try:
        return path.read_text() == wrapper_text(entry_point)
    except (OSError, UnicodeDecodeError):
        # Absent, unreadable, a directory, or not text: none of them are ours.
        return False


def is_ours(path: Path = WRAPPER) -> bool:
    """Whether we wrote this file, whatever checkout it points at."""
    try:
        return WRAPPER_MARKER in path.read_text()
    except (OSError, UnicodeDecodeError):
        return False


def on_path(directory: Path, path_value: str | None = None) -> bool:
    """Whether a shell starting now would find a command in `directory`."""
    entries = (os.environ.get("PATH", "") if path_value is None else path_value).split(os.pathsep)
    # Compared expanded, because a PATH written as `~/.local/bin` in a shell rc
    # does reach us literally when the shell did not expand it.
    return any(entry and Path(entry).expanduser() == directory for entry in entries)


def step_wrapper(prompt: Prompt) -> str:
    if wrapper_is_current(WRAPPER, ENTRY_POINT):
        note(f"{WRAPPER} already runs {ENTRY_POINT}.")
        return check_bin_on_path(SATISFIED)

    if WRAPPER.exists() and not is_ours(WRAPPER):
        warn(f"{WRAPPER} exists and was not written by paper-cast.")
        if not prompt.confirm(f"Overwrite {WRAPPER}?"):
            warn("skipped: the wrapper. The panel runs `paper-cast`, so it needs one on PATH.")
            return OUTSTANDING

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    WRAPPER.write_text(wrapper_text(ENTRY_POINT))
    WRAPPER.chmod(0o755)
    note(f"Wrote {WRAPPER} → {ENTRY_POINT}")
    return check_bin_on_path(DONE)


def check_bin_on_path(status: str) -> str:
    """Warn, but do not edit anyone's shell rc: that is not Setup's file to write."""
    if not on_path(BIN_DIR):
        warn(f"{BIN_DIR} is not on your PATH, so `paper-cast` will not be found.")
        note("Add this to ~/.bashrc or ~/.zshrc, then open a new terminal:")
        note('    export PATH="$HOME/.local/bin:$PATH"')
    return status


# --- step 5: Steering -------------------------------------------------------


def preset_names() -> list[str]:
    return [preset["name"] for preset in STARTER_PRESETS]


def preset_text(name: str) -> str:
    for preset in STARTER_PRESETS:
        if preset["name"] == name:
            return preset["text"]
    return ""


def load_preset(name: str, path: Path = CONFIG_FILE) -> None:
    """Make `name` the loaded Steering, the way `config set steering_preset` does."""
    config = load_config(path)
    config["steering_preset"] = name
    config["focus"] = loaded_text(config)
    save_config(config, path)


def step_steering(prompt: Prompt) -> str:
    """The one step that is about the product rather than the plumbing.

    Steering is why paper-cast exists — it is what stops the hosts explaining
    AdamW to someone who already knows — so this step does not just write a file
    and move on. It puts the five starter presets in the user's own config, shows
    them the one that will be used, and says how to change it.
    """
    if not write_fresh_config(CONFIG_FILE):
        note(f"{CONFIG_FILE} already exists, and Setup never rewrites a config it did not write.")
        note("The Steering in it is yours.")
        return SATISFIED

    note(f"Wrote {CONFIG_FILE} with {len(STARTER_PRESETS)} starter Steering presets.")
    say()
    note("Steering is the instruction the hosts are given before they read the")
    note("paper: who they are talking to and what to concentrate on. It is the")
    note("difference between twenty minutes of background you already know and")
    note("ten minutes on the ablations. These five are a starting point — the")
    note("panel edits them, and editing one rewrites that preset in place.")
    say()
    for preset in STARTER_PRESETS:
        note(f"  {preset['name']}")

    say()
    chosen = prompt.choose(
        "Which Steering should be loaded to begin with?", preset_names(), preset_names()[0]
    )
    load_preset(chosen, CONFIG_FILE)
    say()
    note(f"Loaded: {chosen}")
    for line in wrap_quote(preset_text(chosen)):
        note(f"  {line}")
    say()
    note("Change it any time from the panel, or:")
    note("    paper-cast config set steering_preset 'Critical read'")
    note("    paper-cast config set focus --stdin < my-steering.txt")
    return DONE


def wrap_quote(text: str, width: int = 72) -> list[str]:
    """The Steering, readable in a terminal. `textwrap` would do, but this keeps
    the module's import list to what the rest of it already needs."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


# --- step 6: the plugin -----------------------------------------------------


def plugin_state(listing: str, plugin_id: str = PLUGIN_ID) -> str:
    """`enabled`, `disabled` or `absent`, from `omarchy-plugin-list --json`."""
    try:
        plugins = json.loads(listing)
    except json.JSONDecodeError:
        return "absent"
    for plugin in plugins:
        if plugin.get("id") == plugin_id:
            return "enabled" if plugin.get("enabled") else "disabled"
    return "absent"


def step_plugin(prompt: Prompt) -> str:
    """Offer to enable the bar widget. Never required: the CLI is the whole
    product without it, which is why this step cannot make Setup exit non-zero.
    """
    if shutil.which("omarchy-plugin-list") is None:
        note("No Omarchy on this machine, so there is no bar widget to enable.")
        note("`paper-cast cast paper.pdf` works exactly the same from a terminal.")
        return NOT_APPLICABLE

    state = plugin_state(run_quiet(["omarchy-plugin-list", "--json"]).stdout)
    if state == "enabled":
        note(f"The {PLUGIN_ID} bar widget is enabled.")
        return SATISFIED
    if state == "absent":
        note("Omarchy has not been given this plugin yet. To put the icon on the bar:")
        note("    omarchy plugin add https://github.com/TiagoVello/paper-cast.git --enable")
        return NOT_APPLICABLE

    if not prompt.confirm("Put the paper-cast icon on the bar now?"):
        note(f"Left disabled. `omarchy plugin enable {PLUGIN_ID}` whenever you like.")
        return NOT_APPLICABLE
    if run_visible(["omarchy", "plugin", "enable", PLUGIN_ID]) != 0:
        warn("omarchy could not enable the plugin; see its output above.")
        return NOT_APPLICABLE
    return DONE


# --- the wizard -------------------------------------------------------------

# Order matters: nlm has to exist before there is a session to log in to, and the
# config has to exist before the panel that reads it is put on the bar.
STEPS: tuple[tuple[str, Callable[[Prompt], str], bool], ...] = (
    ("Prerequisites", step_prerequisites, True),
    ("The NotebookLM session", step_notebooklm, True),
    ("The YouTube credential", step_youtube, True),
    ("The paper-cast command", step_wrapper, True),
    ("Steering", step_steering, True),
    ("The bar widget", step_plugin, False),
)


def setup_command(args: argparse.Namespace) -> int:
    """Run every step, and answer one question with the exit code: can a Job run?

    0 means yes — every step a Job depends on is satisfied. 1 means something is
    still outstanding, which is what the panel and a scripted caller need to be
    able to tell without reading the prose above it.
    """
    prompt = Prompt(assume_yes=args.assume_yes)
    if not prompt.assume_yes and not prompt.interactive:
        raise SetupError("setup asks questions and there is no terminal to ask on; pass --yes")

    say("paper-cast setup — six steps, each skipped when it is already done.")
    note(f"This checkout: {CHECKOUT}")

    outstanding: list[str] = []
    for index, (title, step, required) in enumerate(STEPS, start=1):
        heading(index, len(STEPS), title)
        status = step(prompt)
        if status == OUTSTANDING and required:
            outstanding.append(title)

    say()
    if outstanding:
        warn("Not ready yet. Still to do: " + ", ".join(outstanding))
        note("Re-run `paper-cast setup`; everything already done is skipped.")
        return 1
    say("Ready. Drop a paper on the bar icon, or:")
    note("    paper-cast cast paper.pdf")
    return 0


# --- uninstall --------------------------------------------------------------


def output_dir() -> Path:
    """Where Run directories land — from the config, or the default if it will not
    parse. A broken config must not stop `uninstall` from naming what it leaves."""
    try:
        return load_config(CONFIG_FILE)["output_dir"]
    except (ConfigError, OSError):
        return Path(DEFAULTS["output_dir"]).expanduser()


def leftovers() -> list[tuple[Path, str]]:
    """What uninstall deliberately does not delete, and what each thing is.

    None of it is reproducible: a Steering somebody wrote, a credential that cost
    fifteen minutes of console work, and Episodes. Only the existing ones are
    listed — naming paths that were never there reads like a threat.
    """
    candidates = [
        (CONFIG_DIR, "your config, the YouTube credential and the bootstrap answers"),
        (STATE_DIR, "the Queue and what it knows about past Jobs"),
        (output_dir(), "the Run directories — covers, audio and any kept video"),
    ]
    return [(path, what) for path, what in candidates if path.exists()]


def uninstall_command(args: argparse.Namespace) -> int:
    """Take the wrapper off PATH, and say plainly what is being left behind.

    Run this *before* `omarchy plugin remove` (#12): removing the plugin takes
    the checkout with it, and this command lives in that checkout.
    """
    prompt = Prompt(assume_yes=args.assume_yes)

    if not WRAPPER.exists():
        note(f"No {WRAPPER} to remove.")
    elif not is_ours(WRAPPER):
        warn(f"{WRAPPER} was not written by paper-cast setup, so it is being left alone.")
    else:
        if not prompt.confirm(f"Remove {WRAPPER}?"):
            note("Left in place. Nothing was changed.")
            return 0
        WRAPPER.unlink()
        note(f"Removed {WRAPPER}")

    say()
    remaining = leftovers()
    if remaining:
        say("Left behind, on purpose — none of it comes back by running setup again:")
        for path, what in remaining:
            note(f"  {path}")
            note(f"      {what}")
    else:
        say("Nothing of yours is on this machine to leave behind.")
    say()
    say("Also untouched, because they are not paper-cast's to take away:")
    note(f"  nlm         — `uv tool uninstall {NLM_PACKAGE}`, and it holds your")
    note("                NotebookLM session")
    note("  ffmpeg, poppler, uv, inotify-tools — system packages other things use")
    note("  the notebooks in NotebookLM, and the Episodes on your YouTube channel")

    if shutil.which("omarchy-plugin-list") is not None:
        say()
        say("The plugin itself is still there. To take the icon off the bar:")
        note(f"    omarchy plugin remove {PLUGIN_ID}")
    return 0


# --- the subcommands --------------------------------------------------------


def guarded(handler: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    """Turn a SetupError into the message-and-1 that every other command gives.

    `paper_cast.main` catches the pipeline's own errors by type; rather than
    widening that tuple for a command that is not the pipeline, Setup catches its
    own here.
    """

    def run(args: argparse.Namespace) -> int:
        try:
            return handler(args)
        except SetupError as err:
            print(f"error: {err}", file=sys.stderr)
            return 1

    return run


def register(subparsers: Any) -> None:
    setup = subparsers.add_parser("setup", help="bring this machine to the point where a Job can run")
    setup.add_argument(
        "--yes",
        "-y",
        action="store_true",
        dest="assume_yes",
        help="take every step without asking; the path for scripts and for the panel",
    )
    setup.set_defaults(handler=guarded(setup_command))

    uninstall = subparsers.add_parser(
        "uninstall", help="remove the paper-cast command, and say what is left behind"
    )
    uninstall.add_argument(
        "--yes", "-y", action="store_true", dest="assume_yes", help="do not ask before removing"
    )
    uninstall.set_defaults(handler=guarded(uninstall_command))
