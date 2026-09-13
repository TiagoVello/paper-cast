#!/usr/bin/env python3
"""`paper-cast queue …` and `paper-cast run-queue` — the Queue, its runner, and the
Job files the bar panel reads.

A Job is a JSON file in `~/.local/state/paper-cast/jobs/`, one per Job, and it is
the contract between the CLI and the panel (#13). XDG state rather than anywhere
under `~/.config/omarchy`, so the headless CLI owes the plugin nothing.

The shape, whole — the panel reads these fields and no others:

    version         1, the shape's own number; a reader that does not know it
                    should show the Job and touch nothing.
    id              "20260913T125959123456Z-a1b2c3": a UTC stamp to the
                    microsecond and a random tail, so sorting the ids sorts the
                    Queue into the order it runs in. Opaque otherwise — read the
                    timestamps, not the id.
    stage           one of paper_cast.STAGES: queued, generating, muxing,
                    uploading, done, failed. The three middle ones mean a runner
                    is working on it now.
    sources         the Sources, in order, each one exactly as `paper-cast
                    resolve` prints it (#17): [{"kind", "id", "title", "url",
                    "path"}]. `kind` is "pdf" for a paper on disk, "arxiv" for an
                    arXiv entry and "url" for a PDF on the web; the last two carry
                    `path` null until the runner has downloaded the paper into the
                    Run directory. `id` and `url` are null for a local paper, and
                    `title` is "" until something has named it.
    combine         true when these Sources make one Episode together (#18).
    title           the Episode's title, or "" to take it from the paper. The
                    pipeline writes back what it resolved, so a retry reuses it.
    steering        the Steering text, as it stood when the Job was queued.
    steering_preset the preset it was loaded from, or "" for a hand-written one.
    created_at      ISO-8601 UTC, "2026-09-13T12:59:59Z". Never changes.
    started_at      when a runner last picked it up, or null.
    finished_at     when it reached done or failed, or null.
    updated_at      when this file was last written. A poller compares this.
    error           the failure, whole and verbatim, or null.
    failed_stage    the stage it was in when it failed, or null. This is what a
                    retry resumes at.
    video_url       the YouTube Studio link once it is up, or null.
    run_dir         the Run directory, once the pipeline has named it, or null.
    log             the per-Job log, always set; the panel points at this one file.

Every write is a write-to-temp-then-rename, because the bar widget polls these
files and must never read half of one.

One Job at a time, serialized with `flock` on `queue.lock`: NotebookLM is a
remote session, not a pool. `queue add` writes the Job and spawns a detached
runner only if nobody holds the lock; `run-queue` drains and exits.

Stdlib only, per the standing decisions on the map.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import paper_cast as pc
from paper_cast import ConfigError, PipelineError
# What a Source may be named as, and how a named one becomes a file on disk — the
# arXiv API included — is all in `sources` (#17).
from sources import episode_directory, materialise, resolve

JOB_VERSION = 1
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
ID_STAMP = "%Y%m%dT%H%M%S"


# --- where state lives ------------------------------------------------------


def state_dir() -> Path:
    """`~/.local/state/paper-cast`, or wherever XDG_STATE_HOME points.

    Read from the environment on every call rather than frozen into a module
    constant, because the runner is a *separate process*: the environment is the
    only thing that crosses that boundary, so it has to be the one authority on
    where the Queue is.
    """
    root = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(root) / "paper-cast"


def jobs_dir() -> Path:
    return state_dir() / "jobs"


def lock_file() -> Path:
    return state_dir() / "queue.lock"


def job_file(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def log_file(job_id: str) -> Path:
    # Beside the Job file, named after it, so the panel can find one from the other.
    return jobs_dir() / f"{job_id}.log"


def now() -> str:
    return time.strftime(TIMESTAMP, time.gmtime())


def new_job_id() -> str:
    """A sortable id: the Queue's order is its ids' order, with no index to keep.

    Microseconds and not seconds, because three papers dropped on the icon at once
    are queued inside the same second, and an id that only sorted to the second
    would leave the panel drawing them in an order unrelated to the one they run
    in. The random tail is there to break a tie inside one microsecond, nothing more.
    """
    while True:
        moment = time.time()
        stamp = time.strftime(ID_STAMP, time.gmtime(moment))
        job_id = f"{stamp}{int(moment % 1 * 1_000_000):06d}Z-{secrets.token_hex(3)}"
        if not job_file(job_id).exists():
            return job_id


# --- reading and writing Job files ------------------------------------------


def write_job(job: dict[str, Any]) -> dict[str, Any]:
    """Write a Job file whole, and never let anything observe half of one.

    The bar widget polls this directory (#14), so the file is written beside
    itself and renamed over: a reader sees the old Job or the new one and never a
    truncated line of JSON. `.json.new` is deliberately not a `*.json` name, so a
    reader globbing the directory does not find the temporary either.
    """
    path = job_file(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)
    return job


def read_job(job_id: str) -> dict[str, Any]:
    path = job_file(job_id)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no such Job: {job_id}") from None
    except json.JSONDecodeError as err:
        raise ConfigError(f"{path}: not a Job file any more: {err}") from None


def all_jobs() -> list[dict[str, Any]]:
    """Every Job, oldest first — which is Queue order, because the ids sort.

    A file that will not parse is skipped rather than raised on: one Job somebody
    edited by hand must not take the whole list down with it, since this list is
    what the panel draws.
    """
    jobs = []
    for path in sorted(jobs_dir().glob("*.json")):
        try:
            jobs.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return jobs


def next_queued() -> dict[str, Any] | None:
    for job in all_jobs():
        if job.get("stage") == pc.STAGE_QUEUED:
            return job
    return None


def new_job(
    sources: list[dict[str, str]],
    *,
    combine: bool,
    title: str,
    steering: str,
    steering_preset: str,
) -> dict[str, Any]:
    """A Job as it goes onto the Queue: every field present, most of them empty."""
    job_id = new_job_id()
    return {
        "version": JOB_VERSION,
        "id": job_id,
        "stage": pc.STAGE_QUEUED,
        "sources": sources,
        "combine": combine,
        "title": title,
        "steering": steering,
        "steering_preset": steering_preset,
        "created_at": now(),
        "started_at": None,
        "finished_at": None,
        "updated_at": now(),
        "error": None,
        "failed_stage": None,
        "video_url": None,
        "run_dir": None,
        "log": str(log_file(job_id)),
    }


# --- one Job at a time ------------------------------------------------------


@contextlib.contextmanager
def queue_lock() -> Iterator[bool]:
    """Hold the one-Job-at-a-time lock, or report that somebody else has it.

    `flock` and not a pid file: the kernel releases it when the holder dies,
    however it dies, so a killed runner never leaves a Queue that will not start.
    """
    path = lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        # Closing the descriptor is what releases the lock.
        handle.close()


def spawn_runner() -> None:
    """Start a runner that outlives this command.

    `start_new_session` puts it in its own session, so it survives the panel's
    shell, a closed terminal and a Ctrl-C in the one it was launched from. Its
    three standard descriptors go to /dev/null because the Job's own output
    belongs in the Job's log, and nowhere else.
    """
    subprocess.Popen(
        [sys.executable, str(Path(pc.__file__).resolve()), "run-queue"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_runner_if_idle() -> bool:
    """Spawn a runner unless one is already draining. True when one was spawned.

    Taking the lock is the only way to learn whether anybody holds it, so it is
    taken and dropped again; the runner we spawn takes it for itself a moment
    later. If somebody else wins that moment, their drain picks this Job up.
    """
    with queue_lock() as idle:
        pass
    if idle:
        spawn_runner()
    return idle


# --- the runner -------------------------------------------------------------


def adopt_abandoned() -> None:
    """Fail any Job left mid-run by a runner that died, so the panel is not lying.

    Only ever called while holding the lock, which is what makes it safe: nothing
    else can be running a Job, so a Job that says `generating` is a Job whose
    runner is gone — killed, rebooted, out of battery. `failed_stage` keeps the
    stage it died in, so `queue retry` resumes there rather than from the top.

    It is not resumed automatically. A crash that repeats would otherwise repeat
    for ever, and the artifacts are all still on disk waiting for a Retry.
    """
    for job in all_jobs():
        if job.get("stage") in pc.RUNNING_STAGES:
            job.update(
                stage=pc.STAGE_FAILED,
                failed_stage=job["stage"],
                error="the runner did not survive this Job; its artifacts are still on disk",
                finished_at=now(),
                updated_at=now(),
            )
            write_job(job)


def resume_stage(job: dict[str, Any]) -> str:
    """Where a retry picks up: the stage it died in, as far as the disk backs that up.

    A failed upload must never re-run a five-minute generation (#13), so the
    recorded stage is believed — but only while the artifacts that stage consumes
    are still there. An `episode.mp4` deleted by hand demotes an `uploading` retry
    to `muxing`, and a missing cover or audio demotes that one to `generating`,
    rather than resuming into a Run directory with nothing in it.
    """
    wanted = job.get("failed_stage") or pc.STAGE_GENERATING
    if wanted not in (pc.STAGE_MUXING, pc.STAGE_UPLOADING) or not job.get("run_dir"):
        return pc.STAGE_GENERATING
    run = pc.RunPaths(Path(job["run_dir"]))
    if wanted == pc.STAGE_UPLOADING and has_content(run.video):
        return pc.STAGE_UPLOADING
    if has_content(run.cover) and has_content(run.audio):
        return pc.STAGE_MUXING
    return pc.STAGE_GENERATING


def has_content(path: Path) -> bool:
    # A zero-byte artifact is what a killed ffmpeg or a failed download leaves.
    return path.is_file() and path.stat().st_size > 0


def source_paths(sources: list[dict[str, Any]], run_dir: Path | None) -> list[Path]:
    """Every Source of a Job, as a local PDF path, in Job order.

    One failure here fails the whole Job before any of them reach NotebookLM
    (#13, #18): a combined Episode must never ship discussing fewer papers than
    it was asked to, so this is resolved once, up front, rather than lazily as
    the pipeline gets to each one.

    A Source of kind "pdf" is already a file on disk; an "arxiv" or "url" one has
    to be downloaded before it is a Path (#17), and this loop is the one place
    that happens. `run_dir` is where a download lands — `sources.episode_directory`
    names it, and it is None for a Job that has nothing to fetch.
    """
    paths = []
    for source in sources:
        # --- #17's one line: whatever this Source is, afterwards it is a file. ---
        materialise(source, run_dir)
        # `kind` is no longer the question: `materialise` has either put a paper
        # on disk or said why it could not, so a Source with no path here is a
        # local one whose file has gone.
        if not source.get("path"):
            raise PipelineError(f"this Job's Source is not a paper on disk: {source!r}")
        paths.append(Path(source["path"]))
    return paths


def cast_job(job: dict[str, Any], note: Any, resume_from: str) -> None:
    """Drive the pipeline for one Job. The Job's Steering is the one that reaches the hosts."""
    sources = job.get("sources") or []
    if not sources:
        raise PipelineError("this Job has no Sources")

    # The Job's own Steering wins over the config's: it was snapshotted when the
    # Job was queued, and a preset flipped through since must not re-steer a Job
    # that is already waiting in line.
    config = pc.load_config(pc.CONFIG_FILE) | {"focus": job["steering"]}
    # The config is read first because #17 needs `output_dir` to know where a
    # Source that is not on disk yet should be downloaded to: the Run directory
    # this Episode is about to claim, so the paper sits next to what it produced.
    run_dir = episode_directory(job, config)
    pdfs = source_paths(sources, run_dir)
    pc.run_pipeline(
        pdfs,
        job["title"] or None,
        config,
        dry_run=False,
        note=note,
        resume_from=resume_from,
        # Named before the fetch and handed over rather than worked out again:
        # a downloaded paper carries a `Title:` that `episode_directory` could
        # not have read, and a pipeline free to name the run off it would build
        # the Episode in a sibling of the directory holding the paper.
        run_dir=run_dir,
        # #17: arXiv named the paper and `pdfinfo` cannot — the PDF of *Attention
        # Is All You Need* carries no Title at all (#2) — so the first Source's
        # own title is handed over to outrank it.
        source_title=sources[0].get("title") or "",
    )


def run_job(job: dict[str, Any]) -> None:
    """Run one Job to an end, and record every step of it in the Job file.

    A failure is recorded rather than raised: this runs detached with nowhere to
    print, so an error that got out of here would be a Job frozen mid-stage and a
    user with nothing to read. Whatever went wrong lands in `error`, and the log
    has the rest. The one exception is a Ctrl-C, which is recorded and then passed
    on, because it was aimed at the runner and not at this Job.
    """
    state = dict(job)
    resume_from = resume_stage(state)

    def note(facts: dict[str, Any]) -> None:
        state.update(facts)
        state["updated_at"] = now()
        write_job(state)

    note({
        "stage": resume_from,
        "started_at": now(),
        "finished_at": None,
        "error": None,
        "failed_stage": None,
    })
    path = Path(state["log"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Appended to, never replaced: a retry's log belongs after the failure that
    # caused it, in the one file the panel points at. Line buffered, so a panel
    # tailing it sees the run as it happens rather than at the end.
    with path.open("a", buffering=1) as log:
        log.write(f"\n=== {now()} {state['id']} from {resume_from} ===\n")
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                cast_job(state, note, resume_from)
            except (KeyboardInterrupt, Exception) as err:
                named = isinstance(err, (*pc.RUN_ERRORS, KeyboardInterrupt)) and str(err)
                note({
                    "stage": pc.STAGE_FAILED,
                    "failed_stage": state["stage"],
                    # A failure the pipeline names reads as it was written; anything
                    # else carries its class, because a bare message out of a bug
                    # says nothing about where it came from.
                    "error": str(err) if named else f"{type(err).__name__}: {err}",
                    "finished_at": now(),
                })
                if isinstance(err, KeyboardInterrupt):
                    raise
            else:
                note({"stage": pc.STAGE_DONE, "finished_at": now()})


def drain() -> int:
    """Run queued Jobs one at a time until the Queue is empty, then exit."""
    while True:
        with queue_lock() as mine:
            if not mine:
                # Somebody else is draining, and they will reach whatever we would have.
                return 0
            adopt_abandoned()
            while (job := next_queued()) is not None:
                run_job(job)
        # The lock is free again only from here, so this is the first moment a
        # `queue add` could have seen it free. One that wrote its Job while we
        # still held it spawned no runner — and its file is on disk by now, since
        # it was written before the probe that found us busy. Looking once more
        # after letting go is what stops that Job waiting for ever. (#13)
        if next_queued() is None:
            return 0


# --- the subcommands --------------------------------------------------------


def read_stdin() -> str:
    """A value byte for byte, minus the one trailing newline a shell adds."""
    text = sys.stdin.read()
    return text[:-1] if text.endswith("\n") else text


def steering_for(args: argparse.Namespace) -> tuple[str, str]:
    """The Steering this Job is queued with, and the preset it came from.

    The config's `focus` is the loaded Steering (#11), so by default a Job takes
    whatever the panel or the shell has loaded and keeps its own copy of it. Text
    passed here instead is hand-written and belongs to no preset, exactly as
    `config set focus` leaves one.
    """
    if args.steering_stdin:
        return read_stdin(), ""
    if args.steering is not None:
        return args.steering, ""
    config = pc.load_config(pc.CONFIG_FILE)
    return config["focus"], config["steering_preset"]


def add_command(args: argparse.Namespace) -> int:
    """Queue one Job per Source, or one Job for all of them when they combine.

    A Job makes exactly one Episode (CONTEXT.md), so three papers dropped with the
    combine toggle off are three Jobs, and with it on they are one. The panel
    hands over the staged list and the toggle, and does not have to decide which.
    """
    if args.steering is not None and args.steering_stdin:
        raise ConfigError("queue add takes --steering, or --steering-stdin, and not both")
    # Every reference is read, and an arXiv one looked up, before a single Job is
    # written: a path that is not there and an id that names nothing both fail
    # here, while the user is still looking at what they typed (#17).
    sources = [resolve(raw) for raw in args.sources]
    steering, preset = steering_for(args)
    groups = [sources] if args.combine else [[source] for source in sources]
    if args.title and len(groups) > 1:
        raise ConfigError(
            f"--title names one Episode, and these {len(groups)} Sources would be "
            f"{len(groups)} separate Jobs; add --combine to make them one"
        )
    for group in groups:
        job = write_job(new_job(
            group,
            combine=args.combine,
            title=args.title or "",
            steering=steering,
            steering_preset=preset,
        ))
        print(job["id"])
    start_runner_if_idle()
    return 0


def retry_command(args: argparse.Namespace) -> int:
    """Put a failed Job back on the Queue, to resume where it stopped.

    Nothing is cleaned up and nothing is copied: the Job keeps its Run directory
    and its `failed_stage`, and the runner reads both to decide how much of the
    work is already done.
    """
    job = read_job(args.id)
    if job.get("stage") not in (pc.STAGE_FAILED, pc.STAGE_DONE):
        raise ConfigError(f"{args.id} is {job.get('stage')}, so there is nothing to retry")
    job["stage"] = pc.STAGE_QUEUED
    job["updated_at"] = now()
    write_job(job)
    print(f"{args.id} queued, resuming at {resume_stage(job)}")
    start_runner_if_idle()
    return 0


def clear_command(args: argparse.Namespace) -> int:
    """Drop the Jobs that have finished, and their logs. Nothing running is touched."""
    removed = 0
    for job in all_jobs():
        if job.get("stage") in (pc.STAGE_DONE, pc.STAGE_FAILED):
            job_file(job["id"]).unlink(missing_ok=True)
            log_file(job["id"]).unlink(missing_ok=True)
            removed += 1
    print(f"cleared {removed} finished {'Job' if removed == 1 else 'Jobs'}")
    return 0


def describe(job: dict[str, Any]) -> str:
    """One Job on one line, for a terminal."""
    where = job.get("video_url") or job.get("error") or job.get("run_dir") or ""
    what = ", ".join(Path(source.get("path") or source.get("title") or "?").name
                     for source in job.get("sources") or [])
    stage = str(job.get("stage"))
    return f"{job.get('id')}  {stage:<10}  {job.get('title') or what}  {where}".rstrip()


def list_command(args: argparse.Namespace) -> int:
    jobs = all_jobs()
    if args.as_json:
        # The Job files themselves, whole and in Queue order: the panel reads these
        # files directly too, and must never see two different shapes of the same Job.
        print(json.dumps(jobs, ensure_ascii=False, indent=2))
        return 0
    for job in jobs:
        print(describe(job))
    return 0


def show_command(args: argparse.Namespace) -> int:
    job = read_job(args.id)
    if args.as_json:
        print(json.dumps(job, ensure_ascii=False, indent=2))
        return 0
    for key, value in job.items():
        # A string goes out as it stands, so a Steering and an error message read as
        # prose; everything else goes out as JSON, so `null` and `false` are not
        # Python's spelling of them in a file the panel reads as JSON.
        print(f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}")
    return 0


def run_queue_command(_args: argparse.Namespace) -> int:
    return drain()


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("queue", help="queue Jobs, and see how they are going")
    actions = parser.add_subparsers(dest="action", metavar="<action>")

    def usage(_args: argparse.Namespace) -> int:
        parser.print_help()
        return 2

    parser.set_defaults(handler=usage)

    adder = actions.add_parser("add", help="queue one or more Sources")
    adder.add_argument(
        "sources",
        nargs="+",
        metavar="<source>",
        help="the papers to cast: PDFs, arXiv ids or URLs, or direct links to a .pdf",
    )
    adder.add_argument(
        "--combine",
        action="store_true",
        help="one Episode discussing all of them, instead of one Episode each",
    )
    adder.add_argument("--title", help="the Episode's title; taken from the paper when absent")
    adder.add_argument("--steering", help="the Steering, instead of the loaded one")
    adder.add_argument(
        "--steering-stdin",
        action="store_true",
        help="read the Steering from stdin instead, for text an argv would mangle",
    )
    adder.set_defaults(handler=add_command)

    listing = actions.add_parser("list", help="every Job, in the order they run")
    listing.add_argument("--json", action="store_true", dest="as_json", help="print the Job files")
    listing.set_defaults(handler=list_command)

    shower = actions.add_parser("show", help="one Job, whole")
    shower.add_argument("id", help="the Job id")
    shower.add_argument("--json", action="store_true", dest="as_json", help="print the Job file")
    shower.set_defaults(handler=show_command)

    retrier = actions.add_parser("retry", help="run a failed Job again, from where it stopped")
    retrier.add_argument("id", help="the Job id")
    retrier.set_defaults(handler=retry_command)

    clearer = actions.add_parser("clear", help="forget the Jobs that have finished")
    clearer.set_defaults(handler=clear_command)

    # Top-level rather than under `queue`: it is the Queue's runner, not something
    # done to the Queue, and it is what `queue add` spawns.
    runner = subparsers.add_parser("run-queue", help="run queued Jobs until there are none")
    runner.set_defaults(handler=run_queue_command)
