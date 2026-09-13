#!/usr/bin/env python3
"""Turn a paper into a private YouTube episode, in one command.

    paper-cast paper.pdf [--title "..."] [--dry-run]

Subcommands live in sibling `*_cli.py` modules and are discovered at startup;
the bare form above is `paper-cast cast`, spelled the way it was before there
were any others.

PDF in, NotebookLM audio overview out, muxed over page 1 of the paper and
uploaded private. The foreign programs — `nlm`, `pdfinfo`, `pdftoppm`, `ffmpeg`
— are shelled out to; the uploader is our own Python and is imported.

Stdlib only, per the standing decisions on the map.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import unicodedata
from pathlib import Path
from typing import Any

import youtube_auth

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "paper-cast"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# Every key has a default, so a fresh box works with no config file at all.
DEFAULTS: dict[str, Any] = {
    "language": "en",
    "format": "deep_dive",
    "length": "default",
    # The Steering, and the name of the preset it was loaded from. Loading a
    # preset copies its text into `focus`; editing the text there is what the
    # panel writes back into the preset (#11).
    "focus": "",
    "steering_preset": "",
    "presets": [],
    "output_dir": "~/Videos/paper-cast",
    "keep_artifacts": False,
    "keep_video": False,
}
REQUIRED_TOOLS = ("nlm", "pdfinfo", "pdftoppm", "ffmpeg")

# Constants, not config keys (#8): these are tuned by editing the script once.
POLL_INTERVAL = 15  # #2: audio create returns in 3s, generation runs ~4m30s.
GENERATION_TIMEOUT = 15 * 60  # ~3x the observed run, and the only failure detector we have.
DOWNLOAD_WINDOW = 120  # #2: `completed` 404s for ~35s before the media URL works.
DOWNLOAD_RETRY_INTERVAL = 5

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

SLUG_LIMIT = 80
FALLBACK_SLUG = "paper"

FORMATS = ("deep_dive", "brief", "critique", "debate")
LENGTHS = ("short", "default", "long")

# A Steering preset is a name and the Steering text, and nothing else until there
# is a reason (#11): no per-preset format, length or language.
PRESET_FIELDS = ("name", "text")


class ConfigError(Exception):
    """The config file on disk does not say something we can act on."""


# --- config -----------------------------------------------------------------


def _check_choice(key: str, value: Any, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ConfigError(f"{key} = {value!r} is not one of: {', '.join(allowed)}")
    return value


def _check_type(key: str, value: Any, wanted: type) -> Any:
    # bool is an int subclass, so a bare isinstance check would let `language = true` pass.
    if type(value) is not wanted:
        raise ConfigError(f"{key} must be {wanted.__name__}, not {type(value).__name__}")
    return value


def parse_presets(raw: Any) -> list[dict[str, str]]:
    """Validate `[[presets]]` as strictly as the flat keys, and name what is wrong.

    A preset that quietly came out wrong is worse than a missing one: the panel
    flips through these and sends whatever it finds, so a typo'd `txet` would
    mean an Episode generated with no Steering at all.

    There is no length check here on purpose (#11): the 500-character cap is a
    `maxlength` on Google's own textarea, not a server rule — a 1,313-character
    Steering round-tripped byte for byte through `nlm` — so a preset may run as
    long as it likes.
    """
    _check_type("presets", raw, list)
    presets: list[dict[str, str]] = []
    for index, entry in enumerate(raw):
        where = f"presets[{index}]"
        _check_type(where, entry, dict)
        unknown = sorted(set(entry) - set(PRESET_FIELDS))
        if unknown:
            raise ConfigError(
                f"unknown key {', '.join(repr(key) for key in unknown)} in {where}; "
                f"a Steering preset holds {', '.join(PRESET_FIELDS)}"
            )
        if "name" not in entry:
            raise ConfigError(f"{where} has no name, so nothing can ask for it by name")
        name = _check_type(f"{where}.name", entry["name"], str)
        if not name:
            raise ConfigError(f"{where}.name is empty, so nothing can ask for it by name")
        if any(name == seen["name"] for seen in presets):
            raise ConfigError(
                f"{where}.name = {name!r} is already taken by an earlier preset; "
                "steering_preset names one preset, so two cannot share a name"
            )
        # A preset with no text is a deliberate "no Steering", and legal. A typo'd
        # key is caught above, so an absent `text` can only have been meant.
        presets.append({"name": name, "text": _check_type(f"{where}.text", entry.get("text", ""), str)})
    return presets


def parse_config(text: str) -> dict[str, Any]:
    """Merge a config.toml over the defaults — optional, but strict.

    A silently-ignored `format = "deep-dive"` is the one config bug that costs a
    full five-minute generation cycle to discover, so an unknown key or an
    invalid value is a hard error naming the key.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"config is not valid TOML: {err}") from None

    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        raise ConfigError(
            f"unknown config key {', '.join(repr(key) for key in unknown)}; "
            f"paper-cast knows {', '.join(sorted(DEFAULTS))}"
        )

    config = dict(DEFAULTS) | raw
    for key in ("language", "focus", "steering_preset", "output_dir"):
        _check_type(key, config[key], str)
    _check_type("keep_artifacts", config["keep_artifacts"], bool)
    _check_type("keep_video", config["keep_video"], bool)
    _check_choice("format", _check_type("format", config["format"], str), FORMATS)
    _check_choice("length", _check_type("length", config["length"], str), LENGTHS)
    if not config["language"]:
        raise ConfigError("language must be a BCP-47 code such as 'en' or 'pt-BR', not empty")
    config["presets"] = parse_presets(config["presets"])
    names = [preset["name"] for preset in config["presets"]]
    if config["steering_preset"] and config["steering_preset"] not in names:
        # The panel and the CLI have to agree on which preset is loaded, and a
        # name that matches nothing would leave them disagreeing in silence.
        raise ConfigError(
            f"steering_preset = {config['steering_preset']!r} names no preset; "
            f"presets holds {', '.join(repr(name) for name in names) or 'none'}"
        )
    config["output_dir"] = Path(config["output_dir"]).expanduser()
    return config


def load_config(path: Path = CONFIG_FILE) -> dict[str, Any]:
    """Read the config file if it is there; a missing one is the default set."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return parse_config("")
    try:
        return parse_config(text)
    except ConfigError as err:
        raise ConfigError(f"{path}: {err}") from None


# --- naming -----------------------------------------------------------------


def slugify(title: str) -> str:
    """The run directory's name: ASCII, lowercase, hyphenated, 80 characters at most."""
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:SLUG_LIMIT].strip("-")


def run_slug(title: str, pdf: Path) -> str:
    """Name the run after the title, or the file it came from.

    Per #2 the filename fallback is the common path, not an edge case: the
    `pdfinfo` Title of *Attention Is All You Need* is empty.
    """
    return slugify(title) or slugify(pdf.stem) or FALLBACK_SLUG


def pdfinfo_title(output: str) -> str:
    """Pull the Title out of `pdfinfo`'s field listing. Often empty; that is fine."""
    for line in output.splitlines():
        if line.startswith("Title:"):
            return line[len("Title:") :].strip()
    return ""


def resolve_title(override: str | None, pdfinfo_output: str, pdf: Path) -> str:
    """--title, then the PDF's own metadata, then the filename it arrived as.

    Empty when none of the three say anything, which leaves the uploader's own
    `Untitled paper` fallback (#6) to fire instead of pre-empting it here.
    """
    for candidate in (override, pdfinfo_title(pdfinfo_output), pdf.stem):
        title = " ".join((candidate or "").split())
        if title:
            return title
    return ""


# --- command lines ----------------------------------------------------------


def cover_command(pdf: Path, prefix: Path) -> list[str]:
    """Render page 1 as a PNG. -singlefile is what drops pdftoppm's page suffix."""
    return ["pdftoppm", "-f", "1", "-l", "1", "-png", "-singlefile", str(pdf), str(prefix)]


def audio_create_command(notebook_id: str, config: dict[str, Any]) -> list[str]:
    """Kick off the audio overview. Returns in seconds; generation runs on for minutes."""
    command = [
        "nlm", "audio", "create", notebook_id,
        "--language", config["language"],
        "--format", config["format"],
        "--length", config["length"],
        "--confirm",
        "--json",
    ]
    if config["focus"]:
        command += ["--focus", config["focus"]]
    return command


def ffmpeg_command(cover: Path, audio: Path, out: Path) -> list[str]:
    """The mux, in the form #2 verified against a real downloaded overview.

    `-c:a copy` keeps the source's ~257 kbps AAC and handles its DASH branding;
    `-shortest` is mandatory, because `-loop 1` is an infinite video stream. The
    GOP is left to x264: forcing `-g 4` at 2 fps re-keyframes a dense page every
    two seconds and quadrupled the file (200 MB against 47 MB, measured).
    """
    return [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", "2", "-i", str(cover),
        "-i", str(audio),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-crf", "23",
        "-r", "2",
        "-vf", "scale=1280:-2,pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-c:a", "copy",
        "-shortest", "-movflags", "+faststart",
        str(out),
    ]


# --- generation -------------------------------------------------------------


def artifact_status(payload: list[dict[str, Any]], artifact_id: str) -> str:
    """Read one artifact's status out of `nlm studio status --json`.

    Anything that is not the artifact we are waiting on — or an artifact that has
    not surfaced yet — reads as `unknown`, which is also what an audio overview
    reports for the whole of its generation (#2). Only the timeout ends the wait.
    """
    for artifact in payload:
        if artifact_id in (artifact.get("artifact_id"), artifact.get("id")):
            return artifact.get("status") or STATUS_UNKNOWN
    return STATUS_UNKNOWN


# --- episode metadata -------------------------------------------------------


def episode_description(title: str, pdf: Path, config: dict[str, Any]) -> str:
    """What the video says about itself: the paper, the settings, and the disclosure.

    Only facts the pipeline actually holds. The filename goes in because per #2 it
    is frequently the only name the paper has; the local path does not, because
    nothing outside this machine can use it.
    """
    lines = [
        f'An AI-generated audio overview of "{title}".',
        "",
        f"Source: {pdf.name}",
        f"Settings: {config['format']}, {config['length']} length, {config['language']}",
    ]
    if config["focus"]:
        lines.append(f"Focus: {config['focus']}")
    lines += [
        "",
        "The narration is synthetic — generated by Google NotebookLM and assembled by",
        "paper-cast (https://github.com/TiagoVello/paper-cast). The paper is the source",
        "of truth; the overview can be wrong.",
    ]
    return "\n".join(lines)


# --- the run directory ------------------------------------------------------


class RunPaths:
    """One directory per paper, overwritten on a re-run: a re-run means 'do it again'."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.cover = directory / "cover.png"
        # pdftoppm takes a prefix and appends the extension itself.
        self.cover_prefix = directory / "cover"
        self.audio = directory / "episode.m4a"
        self.video = directory / "episode.mp4"


def cleanup_targets(run: RunPaths, config: dict[str, Any], uploaded: bool) -> list[Path]:
    """What a finished run sheds. Nothing, unless the episode actually went up.

    A failed or dry run keeps everything, whatever the config says: that is when
    the artifacts are the only copy of a five-minute generation cycle.
    """
    if not uploaded:
        return []
    targets = [] if config["keep_artifacts"] else [run.cover, run.audio]
    if not config["keep_video"]:
        targets.append(run.video)
    return targets


# --- foreign programs -------------------------------------------------------


class PipelineError(Exception):
    """A step of the pipeline did not do what the next step needs."""


# Everything a run can fail with. OSError covers urllib's URLError from the
# uploader (no network, DNS, TLS) and an output_dir that cannot be written to.
RUN_ERRORS = (
    ConfigError,
    PipelineError,
    youtube_auth.ConfigError,
    youtube_auth.ConsentError,
    youtube_auth.UploadError,
    OSError,
)


def missing_tools() -> list[str]:
    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def run_step(label: str, command: list[str]) -> str:
    """Run a foreign program, and never swallow what it said when it fails.

    When Google changes an endpoint under `nlm`, the subprocess's own stderr is
    the whole diagnosis (#7), so it is surfaced verbatim under the step's name.
    """
    # errors="replace": a PDF's metadata is arbitrary bytes, and a mojibake title
    # beats a UnicodeDecodeError five minutes into a run.
    result = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PipelineError(
            f"{label} failed: {command[0]} exited {result.returncode}"
            + (f"\n{detail}" if detail else "")
        )
    return result.stdout


def json_step(label: str, command: list[str]) -> Any:
    output = run_step(label, command)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        raise PipelineError(f"{label}: expected JSON, got:\n{output.strip()}") from None


def say(message: str) -> None:
    print(message, flush=True)


# --- the pipeline -----------------------------------------------------------


def wait_for_audio(notebook_id: str, artifact_id: str) -> None:
    """Poll until the overview is generated, or until we give up on it.

    `unknown` is the normal in-flight status and there is no terminal-failure
    signal (#2), so this timeout is the only failure detector the CLI gives us.
    """
    started = time.monotonic()
    while True:
        try:
            payload = json_step(
                "nlm studio status",
                ["nlm", "studio", "status", notebook_id, "--json", "--artifact-id", artifact_id],
            )
            status = artifact_status(payload, artifact_id)
        except PipelineError as err:
            # A poll that could not be read says nothing about the generation, which is
            # running server-side regardless. Surface it and keep waiting for the timeout.
            say(f"  {err}")
            status = STATUS_UNKNOWN
        elapsed = time.monotonic() - started
        if status == STATUS_COMPLETED:
            say(f"  generated in {elapsed:.0f}s")
            return
        if status == STATUS_FAILED:
            raise PipelineError(f"NotebookLM reports artifact {artifact_id} failed")
        if elapsed > GENERATION_TIMEOUT:
            raise PipelineError(
                f"audio overview still {status} after {elapsed / 60:.0f} min; giving up. "
                f"The notebook is at https://notebooklm.google.com/notebook/{notebook_id} "
                "if it finishes later."
            )
        say(f"  {status}, {elapsed:.0f}s elapsed")
        time.sleep(POLL_INTERVAL)


def download_audio(notebook_id: str, artifact_id: str, destination: Path) -> None:
    """Fetch the episode, retrying while the media URL is still 404ing (#2: ~35s)."""
    command = [
        "nlm", "download", "audio", notebook_id,
        "--id", artifact_id, "--output", str(destination), "--no-progress",
    ]
    deadline = time.monotonic() + DOWNLOAD_WINDOW
    while True:
        try:
            run_step("nlm download audio", command)
            break
        except PipelineError as err:
            if time.monotonic() >= deadline:
                raise
            say(f"  {err}\n  retrying")
            time.sleep(DOWNLOAD_RETRY_INTERVAL)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise PipelineError(f"nlm download audio wrote nothing to {destination}")


def generate_episode(pdf: Path, title: str, config: dict[str, Any], run: RunPaths) -> None:
    """Everything NotebookLM: notebook, source, overview, download."""
    notebook = json_step("nlm notebook create", ["nlm", "notebook", "create", title, "--json"])
    notebook_id = notebook.get("notebook_id")
    if not notebook_id:
        raise PipelineError(f"nlm notebook create returned no notebook id: {notebook}")
    say(f"  notebook {notebook_id}")

    say("Adding the PDF as a source")
    run_step("nlm source add", ["nlm", "source", "add", notebook_id, "--file", str(pdf), "--wait"])

    say(f"Generating a {config['format']} overview in {config['language']}")
    created = json_step("nlm audio create", audio_create_command(notebook_id, config))
    artifact_id = created.get("artifact_id")
    if not artifact_id:
        raise PipelineError(f"nlm audio create returned no artifact id: {created}")

    wait_for_audio(notebook_id, artifact_id)
    say("Downloading the episode")
    download_audio(notebook_id, artifact_id, run.audio)


def tidy_up(run: RunPaths, config: dict[str, Any]) -> None:
    """Shed what the config does not want kept, once the episode is safely up."""
    for leftover in cleanup_targets(run, config, uploaded=True):
        leftover.unlink(missing_ok=True)
    if not any(run.dir.iterdir()):
        # Nothing was kept, so leave no empty directory per paper behind either.
        run.dir.rmdir()


def run_pipeline(pdf: Path, title_override: str | None, config: dict[str, Any], dry_run: bool) -> int:
    if not pdf.is_file():
        raise PipelineError(f"no such file: {pdf}")
    missing = missing_tools()
    if missing:
        raise PipelineError(f"not on PATH: {', '.join(missing)}")

    # Both credentials are checked before the PDF is touched: failing here costs
    # seconds, failing after generation costs a five-minute cycle.
    say("Refreshing the NotebookLM session")
    try:
        run_step("nlm auth refresh", ["nlm", "auth", "refresh"])
    except PipelineError as err:
        raise PipelineError(f"{err}\n\nRun `nlm login` in a visible browser and sign in again.") from None
    if not dry_run:
        youtube_auth.check_credential()

    title = resolve_title(title_override, run_step("pdfinfo", ["pdfinfo", str(pdf)]), pdf)
    run = RunPaths(config["output_dir"] / run_slug(title, pdf))
    run.dir.mkdir(parents=True, exist_ok=True)
    say(f"{title}\n  {run.dir}")

    try:
        say("Rendering the cover from page 1")
        run_step("pdftoppm", cover_command(pdf, run.cover_prefix))
        generate_episode(pdf, title, config, run)

        say("Muxing the video")
        run_step("ffmpeg", ffmpeg_command(run.cover, run.audio, run.video))

        if dry_run:
            say(f"Dry run: stopping before the upload.\n  {run.video}")
            return 0

        say("Uploading, private")
        video = youtube_auth.upload_private(run.video, title, episode_description(title, pdf, config))
    except (*RUN_ERRORS, KeyboardInterrupt):
        # Nothing is deleted on failure; the retry is you, with the artifacts in hand.
        print(f"artifacts kept in {run.dir}", file=sys.stderr)
        raise

    # The link first: a tidy-up that fails must not bury where the episode went.
    video_id = video.get("id", "?")
    say(f"https://studio.youtube.com/video/{video_id}/edit")
    tidy_up(run, config)
    return 0


# --- entry point ------------------------------------------------------------

# Subcommands live in sibling `*_cli.py` modules, each exposing
# `register(subparsers)`: it adds its own parser and sets `handler`, a callable
# taking the parsed namespace and returning an exit code. Discovery is by glob
# rather than a list here, so growing the CLI is a new file and never an edit to
# this one.
SUBCOMMAND_SUFFIX = "_cli.py"


def subcommand_modules() -> list[Any]:
    """Import every sibling `*_cli.py`, in a stable order."""
    import importlib

    modules = []
    for path in sorted(Path(__file__).resolve().parent.glob("*" + SUBCOMMAND_SUFFIX)):
        modules.append(importlib.import_module(path.name[: -len(".py")]))
    return modules


def cast_command(args: argparse.Namespace) -> int:
    return run_pipeline(args.pdf, args.title, load_config(), args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-cast",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    cast = subparsers.add_parser("cast", help="turn a paper into an episode, now")
    cast.add_argument("pdf", type=Path, help="the paper to turn into an episode")
    cast.add_argument("--title", help="override the title from the PDF's metadata")
    cast.add_argument("--dry-run", action="store_true", help="stop after the .mp4, before the upload")
    cast.set_defaults(handler=cast_command)

    for module in subcommand_modules():
        module.register(subparsers)
    # What `with_default_command` needs, recorded where it is known rather than
    # dug back out of argparse's internals.
    parser.commands = set(subparsers.choices)
    return parser


def with_default_command(argv: list[str], commands: set[str]) -> list[str]:
    """`paper-cast paper.pdf` still means `paper-cast cast paper.pdf`.

    The bare form is the one that was documented before there were subcommands,
    and it is the one worth typing; anything that is not a known command and not
    a flag is a paper.
    """
    if argv and not argv[0].startswith("-") and argv[0] not in commands:
        return ["cast", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = with_default_command(list(sys.argv[1:] if argv is None else argv), parser.commands)
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except RUN_ERRORS as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
