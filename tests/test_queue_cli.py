"""Tests for scripts/queue_cli.py — the Queue, its runner, and the Job files.

The Job file is the contract the panel, the arXiv box and the combined Episode
are all written against (#13), so the shape is asserted field by field here.

The hard parts are tested for real rather than mocked into agreement: the lock is
a real `flock` contended by real processes, a killed runner is really killed with
SIGKILL, and the atomic write is hammered from a thread that reads while it runs.
What is stubbed is the pipeline itself — nothing here shells out to `nlm`,
`ffmpeg` or YouTube, and the only files written are under a temporary
XDG_STATE_HOME.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import paper_cast as pc  # noqa: E402
import queue_cli as q  # noqa: E402
import youtube_auth as ya  # noqa: E402

SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")

# A real runner process with a stubbed pipeline: everything about the Queue is the
# real thing — the flock, the Job files, the drain — and only the five minutes in
# NotebookLM is not.
CHILD = """\
import sys
sys.path.insert(0, {scripts!r})
import queue_cli as q
{code}
raise SystemExit(q.drain())
"""


def stub_pipeline(body):
    """Child source replacing the pipeline with `body`, which has `job` in scope."""
    indented = "\n".join("    " + line for line in body.strip("\n").splitlines())
    return f"def cast_job(job, note, resume_from):\n{indented}\nq.cast_job = cast_job"


# A Job wedged into the one instant `queue add` cannot spawn a runner for: the
# drain's scan comes up empty, and the Job is written by that very scan, so it
# lands after the last look taken under the lock and before the lock is let go.
LATE_ARRIVAL = """
q.cast_job = lambda job, note, resume_from: None
scan, arrived = q.next_queued, []


def next_queued():
    job = scan()
    if job is None and not arrived:
        arrived.append(q.write_job(q.new_job(
            [{"kind": "pdf", "path": "/late.pdf", "title": ""}],
            combine=False, title="late", steering="", steering_preset="")))
        return None
    return job


q.next_queued = next_queued
"""


class QueueTestCase(unittest.TestCase):
    """A Queue in a temporary directory, and a config file that is not the user's."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)
        config = mock.patch.object(pc, "CONFIG_FILE", self.root / "config.toml")
        config.start()
        self.addCleanup(config.stop)

    def paper(self, name="attention.pdf"):
        path = self.root / name
        path.write_bytes(b"%PDF-1.4\n")
        return path

    def add(self, *argv, stdin=None, spawn=None):
        """`paper-cast queue add …`, without ever really spawning a runner."""
        with mock.patch.object(q, "spawn_runner", spawn or mock.Mock()):
            with mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
                return self.run_cli("queue", "add", *argv)

    def run_cli(self, *argv):
        """The command, its exit code and its stdout; stderr lands in `self.said`."""
        out, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errors):
            status = pc.main(list(argv))
        self.said = errors.getvalue()
        return status, out.getvalue()

    def child(self, code, name="runner.py"):
        """A real `run-queue` process running `code` before it drains."""
        path = self.root / name
        path.write_text(CHILD.format(scripts=SCRIPTS, code=code))
        return self.spawned([sys.executable, str(path)])

    def spawned(self, command, **kwargs):
        """A child process this test will not leave behind, whatever it does."""
        child = subprocess.Popen(
            command, env=os.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs
        )
        self.addCleanup(self.reap, child)
        return child

    def reap(self, child):
        child.kill()
        child.wait(timeout=15)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()

    def complains(self, child):
        return child.stderr.read().decode()

    def wait_until(self, predicate, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        self.fail("timed out waiting for the Queue to get there")

    def queued(self, sources=1, **kwargs):
        """A Job on the Queue, straight through the writer rather than the CLI."""
        fields = {"combine": False, "title": "", "steering": "", "steering_preset": ""} | kwargs
        papers = [{"kind": "pdf", "path": str(self.paper(f"p{n}.pdf")), "title": ""}
                  for n in range(sources)]
        return q.write_job(q.new_job(papers, **fields))


class JobShapeTest(QueueTestCase):
    """The fields #14, #15, #17 and #18 read. Adding one is fine; renaming one is not."""

    FIELDS = (
        "version", "id", "stage", "sources", "combine", "title", "steering",
        "steering_preset", "created_at", "started_at", "finished_at", "updated_at",
        "error", "failed_stage", "video_url", "run_dir", "log",
    )

    def test_a_fresh_job_holds_every_documented_field(self):
        job = self.queued()
        self.assertEqual(tuple(job), self.FIELDS)

    def test_a_fresh_job_is_queued_with_nothing_run_and_nothing_wrong(self):
        job = self.queued()
        self.assertEqual(job["version"], q.JOB_VERSION)
        self.assertEqual(job["stage"], pc.STAGE_QUEUED)
        for empty in ("started_at", "finished_at", "error", "failed_stage", "video_url", "run_dir"):
            self.assertIsNone(job[empty], empty)

    def test_the_log_is_named_before_anything_has_written_to_it(self):
        # The panel offers "open the log" on a Job that has not started yet, so the
        # path is part of the Job from the first write.
        job = self.queued()
        self.assertEqual(job["log"], str(q.jobs_dir() / f"{job['id']}.log"))

    def test_a_source_is_a_kind_a_path_and_a_title(self):
        # #17 adds a kind of its own to this, and must not have to reshape it.
        paper = self.paper()
        self.add(str(paper))
        self.assertEqual(
            q.all_jobs()[0]["sources"],
            [{"kind": "pdf", "path": str(paper.resolve()), "title": ""}],
        )

    def test_the_timestamps_are_iso_8601_utc(self):
        self.assertRegex(self.queued()["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_the_job_file_is_json_the_panel_can_read_without_this_module(self):
        job = self.queued()
        self.assertEqual(json.loads(q.job_file(job["id"]).read_text()), job)


class JobIdTest(QueueTestCase):
    def test_ids_sort_into_the_order_the_jobs_were_queued_in(self):
        # Three papers dropped together are queued inside one second, and the panel
        # draws them in id order.
        ids = [q.new_job_id() for _ in range(200)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))

    def test_an_id_carries_no_dot_so_a_reader_cannot_mistake_one_for_a_suffix(self):
        self.assertNotIn(".", q.new_job_id())


class AtomicWriteTest(QueueTestCase):
    """The bar widget polls these files; half of one must never be observable."""

    def test_a_reader_hammering_the_file_never_sees_a_partial_one(self):
        job = self.queued()
        path = q.job_file(job["id"])
        seen, stop = [], threading.Event()

        def read():
            while not stop.is_set():
                try:
                    seen.append(json.loads(path.read_text()))
                except FileNotFoundError:
                    continue  # Never observed with a rename, but not a corrupt read either.
                except json.JSONDecodeError as err:
                    seen.append(err)
                    return

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for index in range(400):
                job["error"] = "a long error message, repeated: " + "x " * index
                q.write_job(job)
        finally:
            stop.set()
            reader.join(10)
        self.assertFalse([item for item in seen if isinstance(item, Exception)])
        self.assertTrue(seen, "the reader never got a look in")

    def test_the_temporary_is_not_a_json_file_a_reader_would_glob(self):
        self.queued()
        self.assertEqual(list(q.jobs_dir().glob("*.json.new")), [])
        self.assertEqual(len(q.all_jobs()), 1)


class AddTest(QueueTestCase):
    def test_one_paper_is_one_job(self):
        status, printed = self.add(str(self.paper()))
        self.assertEqual(status, 0)
        self.assertEqual(printed.split(), [q.all_jobs()[0]["id"]])

    def test_three_papers_without_combine_are_three_jobs_of_one_source_each(self):
        # A Job makes exactly one Episode (CONTEXT.md), so three papers and no
        # combine toggle is three Episodes and therefore three Jobs.
        self.add(*[str(self.paper(f"{n}.pdf")) for n in range(3)])
        jobs = q.all_jobs()
        self.assertEqual([len(job["sources"]) for job in jobs], [1, 1, 1])
        self.assertEqual([job["combine"] for job in jobs], [False, False, False])

    def test_three_papers_with_combine_are_one_job_of_three_sources(self):
        self.add(*[str(self.paper(f"{n}.pdf")) for n in range(3)], "--combine", "--title", "Three")
        job, = q.all_jobs()
        self.assertEqual(len(job["sources"]), 3)
        self.assertIs(job["combine"], True)
        self.assertEqual(job["title"], "Three")

    def test_a_title_over_several_separate_jobs_is_refused_rather_than_shared(self):
        status, _ = self.add(str(self.paper("a.pdf")), str(self.paper("b.pdf")), "--title", "One")
        self.assertEqual(status, 1)
        self.assertIn("--combine", self.said)
        self.assertEqual(q.all_jobs(), [])

    def test_a_paper_that_is_not_there_fails_now_and_queues_nothing(self):
        # #17's rule, and it applies to a path too: validate at stage time, in the
        # panel, not fifteen minutes later in the runner.
        status, _ = self.add(str(self.paper()), str(self.root / "ghost.pdf"))
        self.assertEqual(status, 1)
        self.assertIn("ghost.pdf", self.said)
        self.assertEqual(q.all_jobs(), [])

    def test_a_relative_path_is_stored_absolute_because_a_job_outlives_a_shell(self):
        paper = self.paper()
        here = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, here)
        self.add(paper.name)
        self.assertEqual(q.all_jobs()[0]["sources"][0]["path"], str(paper.resolve()))

    def test_the_job_keeps_the_steering_that_was_loaded_when_it_was_queued(self):
        pc.CONFIG_FILE.write_text('steering_preset = "Skim it"\n'
                                  'focus = "Five minutes."\n'
                                  '[[presets]]\nname = "Skim it"\ntext = "Five minutes."\n')
        self.add(str(self.paper()))
        job, = q.all_jobs()
        self.assertEqual(job["steering"], "Five minutes.")
        self.assertEqual(job["steering_preset"], "Skim it")

    def test_a_steering_given_here_belongs_to_no_preset(self):
        pc.CONFIG_FILE.write_text('focus = "Loaded."\n')
        self.add(str(self.paper()), "--steering", "Hand written.")
        job, = q.all_jobs()
        self.assertEqual(job["steering"], "Hand written.")
        self.assertEqual(job["steering_preset"], "")

    def test_a_steering_arrives_whole_through_stdin_when_argv_would_mangle_it(self):
        steering = 'Read it like a reviewer.\nAnd define "attention" for nobody.\t'
        self.add(str(self.paper()), "--steering-stdin", stdin=steering + "\n")
        self.assertEqual(q.all_jobs()[0]["steering"], steering)

    def test_a_steering_is_taken_one_way_or_the_other_and_not_both(self):
        self.assertEqual(self.add(str(self.paper()), "--steering", "x", "--steering-stdin")[0], 1)
        self.assertEqual(q.all_jobs(), [])

    def test_a_later_preset_change_does_not_re_steer_a_job_already_waiting(self):
        pc.CONFIG_FILE.write_text('focus = "As queued."\n')
        self.add(str(self.paper()))
        pc.CONFIG_FILE.write_text('focus = "Changed since."\n')
        self.assertEqual(q.all_jobs()[0]["steering"], "As queued.")

    def test_adding_spawns_a_runner_when_nobody_is_draining(self):
        spawn = mock.Mock()
        self.add(str(self.paper()), spawn=spawn)
        self.assertEqual(spawn.call_count, 1)


class LockTest(QueueTestCase):
    """One Job at a time, against a real second process holding a real flock."""

    def hold_the_lock(self):
        """A child process that takes the lock and waits to be told to let go."""
        path = self.root / "holder.py"
        path.write_text(
            f"import sys\nsys.path.insert(0, {SCRIPTS!r})\n"
            "import queue_cli as q\n"
            "with q.queue_lock() as mine:\n"
            "    print(int(mine), flush=True)\n"
            "    sys.stdin.readline()\n"
        )
        holder = self.spawned([sys.executable, str(path)], stdin=subprocess.PIPE, text=True)
        self.assertEqual(holder.stdout.readline().strip(), "1")
        return holder

    def test_a_second_process_cannot_take_a_lock_this_one_holds(self):
        self.hold_the_lock()
        with q.queue_lock() as mine:
            self.assertIs(mine, False)

    def test_the_lock_is_free_again_once_the_holder_lets_go(self):
        holder = self.hold_the_lock()
        holder.communicate("go\n", timeout=15)
        with q.queue_lock() as mine:
            self.assertIs(mine, True)

    def test_adding_while_somebody_is_draining_spawns_nothing(self):
        # The runner already draining will reach this Job; a second one would be a
        # second NotebookLM session, which is the thing the lock exists to prevent.
        self.hold_the_lock()
        spawn = mock.Mock()
        self.add(str(self.paper()), spawn=spawn)
        self.assertEqual(spawn.call_count, 0)
        self.assertEqual(len(q.all_jobs()), 1)

    def test_a_runner_that_cannot_take_the_lock_exits_rather_than_queueing_up(self):
        self.hold_the_lock()
        self.queued()
        self.assertEqual(q.drain(), 0)
        self.assertEqual(q.all_jobs()[0]["stage"], pc.STAGE_QUEUED)


class DrainTest(QueueTestCase):
    """Two real runners, four real Jobs, one real lock."""

    def test_two_runners_racing_never_have_two_jobs_in_flight_at_once(self):
        markers = self.root / "markers"
        for _ in range(4):
            self.queued()
        body = f"""
import os, time
with open({str(markers)!r}, "a") as handle:
    handle.write("start " + job["id"] + "\\n")
time.sleep(0.25)
with open({str(markers)!r}, "a") as handle:
    handle.write("end " + job["id"] + "\\n")
"""
        runners = [self.child(stub_pipeline(body), f"runner{n}.py") for n in range(2)]
        for runner in runners:
            self.assertEqual(runner.wait(timeout=60), 0, self.complains(runner))

        lines = markers.read_text().split("\n")[:-1]
        self.assertEqual(len(lines), 8, lines)
        # Strictly start/end/start/end: an overlap would put two starts in a row.
        for index, line in enumerate(lines):
            self.assertTrue(line.startswith("start" if index % 2 == 0 else "end"), lines)
        self.assertEqual({job["stage"] for job in q.all_jobs()}, {pc.STAGE_DONE})

    def test_a_job_queued_in_the_instant_a_runner_was_finishing_is_not_stranded(self):
        """The window the second look after releasing the lock exists to close.

        A `queue add` that writes its Job after the runner's last scan but before
        the runner lets the lock go finds the lock busy, spawns nothing, and would
        wait for ever on a runner that only scanned while holding it. Forced here
        by wedging the Job in at exactly that point: the scan that came up empty is
        the one that writes it.
        """
        runner = self.child(LATE_ARRIVAL)
        self.assertEqual(runner.wait(timeout=60), 0, self.complains(runner))
        jobs = q.all_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["stage"], pc.STAGE_DONE)


class KilledRunnerTest(QueueTestCase):
    """SIGKILL, for real, in the middle of a Job."""

    def setUp(self):
        super().setUp()
        self.job = self.queued()
        self.runner = self.child(stub_pipeline("import time\ntime.sleep(600)\n"))
        self.wait_until(lambda: q.read_job(self.job["id"])["stage"] == pc.STAGE_GENERATING)
        self.runner.kill()
        self.runner.wait(timeout=15)

    def test_the_job_file_still_says_what_stage_it_died_in(self):
        job = q.read_job(self.job["id"])
        self.assertEqual(job["stage"], pc.STAGE_GENERATING)
        self.assertIsNotNone(job["started_at"])

    def test_the_lock_died_with_it_so_the_queue_starts_again(self):
        with q.queue_lock() as mine:
            self.assertIs(mine, True)

    def test_the_next_drain_fails_it_rather_than_leaving_the_panel_lying(self):
        with mock.patch.object(q, "cast_job"):
            q.drain()
        job = q.read_job(self.job["id"])
        self.assertEqual(job["stage"], pc.STAGE_FAILED)
        self.assertEqual(job["failed_stage"], pc.STAGE_GENERATING)
        self.assertIn("runner", job["error"])

    def test_it_is_not_resumed_on_its_own_because_a_crash_that_repeats_repeats(self):
        ran = mock.Mock()
        with mock.patch.object(q, "cast_job", ran):
            q.drain()
        self.assertEqual(ran.call_count, 0)


class ResumeStageTest(QueueTestCase):
    """Where a retry picks up, and what on disk has to back that up."""

    def given_run(self, *artifacts):
        run = pc.RunPaths(self.root / "run")
        run.dir.mkdir(exist_ok=True)
        for artifact in artifacts:
            getattr(run, artifact).write_bytes(b"not empty")
        return run

    def job_that_failed(self, stage, run=None):
        return {"failed_stage": stage, "run_dir": str(run.dir) if run else None}

    def test_a_failed_upload_with_its_video_still_there_resumes_at_the_upload(self):
        run = self.given_run("cover", "audio", "video")
        self.assertEqual(q.resume_stage(self.job_that_failed(pc.STAGE_UPLOADING, run)), pc.STAGE_UPLOADING)

    def test_a_failed_mux_resumes_at_the_mux(self):
        run = self.given_run("cover", "audio")
        self.assertEqual(q.resume_stage(self.job_that_failed(pc.STAGE_MUXING, run)), pc.STAGE_MUXING)

    def test_a_failed_upload_whose_video_has_gone_falls_back_to_the_mux(self):
        run = self.given_run("cover", "audio")
        self.assertEqual(q.resume_stage(self.job_that_failed(pc.STAGE_UPLOADING, run)), pc.STAGE_MUXING)

    def test_a_failed_mux_with_no_audio_left_starts_over(self):
        run = self.given_run("cover")
        self.assertEqual(q.resume_stage(self.job_that_failed(pc.STAGE_MUXING, run)), pc.STAGE_GENERATING)

    def test_a_zero_byte_artifact_is_not_an_artifact(self):
        # What a killed ffmpeg or a failed download leaves behind.
        run = self.given_run("cover", "audio")
        run.video.write_bytes(b"")
        self.assertEqual(q.resume_stage(self.job_that_failed(pc.STAGE_UPLOADING, run)), pc.STAGE_MUXING)

    def test_a_failed_generation_starts_over(self):
        run = self.given_run()
        self.assertEqual(q.resume_stage(self.job_that_failed(pc.STAGE_GENERATING, run)), pc.STAGE_GENERATING)

    def test_a_job_that_never_reached_a_run_directory_starts_over(self):
        self.assertEqual(q.resume_stage(self.job_that_failed(pc.STAGE_UPLOADING)), pc.STAGE_GENERATING)

    def test_a_job_that_never_failed_starts_over(self):
        self.assertEqual(q.resume_stage({}), pc.STAGE_GENERATING)


class RetryWithoutNotebookLMTest(QueueTestCase):
    """A failed upload re-uploads the `.mp4` on disk, and goes nowhere near NotebookLM."""

    def setUp(self):
        super().setUp()
        self.run = pc.RunPaths(self.root / "Videos" / "attention")
        self.run.dir.mkdir(parents=True)
        self.run.video.write_bytes(b"an episode that is already muxed")
        self.commands = []
        self.uploaded = []

        def run_step(label, command):
            self.commands.append(command)
            return ""

        def upload(video, title, description):
            self.uploaded.append((video, title))
            return {"id": "vid123"}

        for target, attribute, value in (
            (pc, "run_step", run_step),
            (pc, "missing_tools", lambda: []),
            (ya, "check_credential", lambda: None),
            (ya, "upload_private", upload),
            (pc, "load_config", lambda *a: pc.parse_config("") | {"output_dir": self.root / "Videos"}),
        ):
            patch = mock.patch.object(target, attribute, value)
            patch.start()
            self.addCleanup(patch.stop)

        self.job = self.queued(title="Attention")
        self.job.update(stage=pc.STAGE_FAILED, failed_stage=pc.STAGE_UPLOADING,
                        run_dir=str(self.run.dir), error="quota exceeded")
        q.write_job(self.job)

    def retry(self):
        with mock.patch.object(q, "spawn_runner"):
            self.run_cli("queue", "retry", self.job["id"])
        q.drain()
        return q.read_job(self.job["id"])

    def test_nothing_it_runs_is_nlm(self):
        self.retry()
        self.assertEqual([command for command in self.commands if command[0] == "nlm"], [])

    def test_it_does_not_render_the_cover_or_mux_the_video_again(self):
        self.retry()
        self.assertEqual([command[0] for command in self.commands], [])

    def test_it_uploads_the_mp4_that_was_already_there(self):
        self.retry()
        self.assertEqual(self.uploaded, [(self.run.video, "Attention")])

    def test_the_job_ends_done_with_the_link_in_it(self):
        job = self.retry()
        self.assertEqual(job["stage"], pc.STAGE_DONE)
        self.assertEqual(job["video_url"], "https://studio.youtube.com/video/vid123/edit")
        self.assertIsNone(job["error"])
        self.assertIsNotNone(job["finished_at"])

    def test_retry_says_where_it_will_pick_up(self):
        with mock.patch.object(q, "spawn_runner"):
            status, printed = self.run_cli("queue", "retry", self.job["id"])
        self.assertEqual(status, 0)
        self.assertIn(pc.STAGE_UPLOADING, printed)

    def test_retrying_a_job_that_is_still_queued_is_refused(self):
        other = self.queued()
        self.assertEqual(self.run_cli("queue", "retry", other["id"])[0], 1)
        self.assertIn(pc.STAGE_QUEUED, self.said)

    def test_retrying_a_job_that_is_not_there_names_it(self):
        self.assertEqual(self.run_cli("queue", "retry", "nope")[0], 1)
        self.assertIn("nope", self.said)


class RunJobTest(QueueTestCase):
    """What the runner writes into the Job file, and into its log."""

    def run_one(self, pipeline):
        job = self.queued(title="A paper")
        with mock.patch.object(q, "cast_job", pipeline):
            q.run_job(job)
        return q.read_job(job["id"])

    def test_a_job_that_worked_ends_done_and_timestamped(self):
        job = self.run_one(lambda job, note, resume: note({"stage": pc.STAGE_UPLOADING}))
        self.assertEqual(job["stage"], pc.STAGE_DONE)
        self.assertIsNotNone(job["started_at"])
        self.assertIsNotNone(job["finished_at"])

    def test_the_stage_the_pipeline_notes_is_in_the_file_before_the_job_ends(self):
        seen = []

        def pipeline(job, note, resume):
            note({"stage": pc.STAGE_MUXING, "run_dir": "/tmp/run"})
            seen.append(q.read_job(job["id"]))

        self.run_one(pipeline)
        self.assertEqual(seen[0]["stage"], pc.STAGE_MUXING)
        self.assertEqual(seen[0]["run_dir"], "/tmp/run")

    def test_a_failure_records_the_stage_it_happened_in_and_the_message_whole(self):
        def pipeline(job, note, resume):
            note({"stage": pc.STAGE_UPLOADING})
            raise pc.PipelineError("ffmpeg exited 1\nwith a second line")

        job = self.run_one(pipeline)
        self.assertEqual(job["stage"], pc.STAGE_FAILED)
        self.assertEqual(job["failed_stage"], pc.STAGE_UPLOADING)
        self.assertEqual(job["error"], "ffmpeg exited 1\nwith a second line")

    def test_a_failure_nobody_predicted_is_recorded_rather_than_killing_the_runner(self):
        # The runner is detached with nowhere to print; an error that got out of
        # here would be a Job frozen mid-stage and a user with nothing to read.
        def pipeline(job, note, resume):
            raise ValueError("a Job file somebody edited by hand")

        job = self.run_one(pipeline)
        self.assertEqual(job["stage"], pc.STAGE_FAILED)
        self.assertIn("ValueError", job["error"])

    def test_what_the_pipeline_prints_goes_to_the_jobs_own_log(self):
        job = self.run_one(lambda job, note, resume: pc.say("Muxing the video"))
        self.assertIn("Muxing the video", Path(job["log"]).read_text())

    def test_a_retry_appends_to_the_log_rather_than_losing_the_failure_before_it(self):
        job = self.queued()
        with mock.patch.object(q, "cast_job", lambda *a: pc.say("first attempt")):
            q.run_job(job)
        with mock.patch.object(q, "cast_job", lambda *a: pc.say("second attempt")):
            q.run_job(q.read_job(job["id"]))
        log = Path(q.read_job(job["id"])["log"]).read_text()
        self.assertIn("first attempt", log)
        self.assertIn("second attempt", log)

    def test_a_ctrl_c_is_recorded_and_then_passed_on_to_stop_the_runner(self):
        # It was aimed at the runner, not at this Job: the Job file says what
        # happened, and the drain does not go on to the next one.
        def pipeline(job, note, resume):
            note({"stage": pc.STAGE_GENERATING})
            raise KeyboardInterrupt

        job = self.queued()
        with mock.patch.object(q, "cast_job", pipeline):
            with self.assertRaises(KeyboardInterrupt):
                q.run_job(job)
        self.assertEqual(q.read_job(job["id"])["stage"], pc.STAGE_FAILED)
        self.assertEqual(q.read_job(job["id"])["failed_stage"], pc.STAGE_GENERATING)

    def test_a_job_with_no_sources_fails_rather_than_running_nothing(self):
        job = q.write_job(q.new_job([], combine=False, title="", steering="", steering_preset=""))
        q.run_job(job)
        self.assertEqual(q.read_job(job["id"])["stage"], pc.STAGE_FAILED)

    def test_a_combined_job_fails_whole_and_says_it_is_not_built_yet(self):
        # A Job's failure is the Job's, whole (#13): a two-Source Job never ships
        # an Episode discussing one of them. Running one is #18.
        job = self.queued(sources=3, combine=True)
        q.run_job(job)
        failed = q.read_job(job["id"])
        self.assertEqual(failed["stage"], pc.STAGE_FAILED)
        self.assertIn("#18", failed["error"])
        self.assertIsNone(failed["video_url"])

    def test_the_jobs_steering_is_the_one_that_reaches_the_hosts(self):
        pc.CONFIG_FILE.write_text('focus = "Changed since."\n')
        job = self.queued(steering="As queued.")
        seen = {}
        with mock.patch.object(pc, "run_pipeline", lambda *args, **kw: seen.update(args[2])):
            q.run_job(job)
        self.assertEqual(seen["focus"], "As queued.")


class ListAndShowTest(QueueTestCase):
    """The JSON the panel reads when it is not reading the files itself."""

    def test_list_as_json_is_the_job_files_whole_and_in_queue_order(self):
        jobs = [self.queued(title=f"Paper {n}") for n in range(3)]
        listed = json.loads(self.run_cli("queue", "list", "--json")[1])
        self.assertEqual(listed, jobs)

    def test_list_as_json_on_an_empty_queue_is_an_empty_array_and_not_an_error(self):
        self.assertEqual(self.run_cli("queue", "list", "--json"), (0, "[]\n"))

    def test_show_as_json_is_exactly_the_file(self):
        job = self.queued()
        self.assertEqual(json.loads(self.run_cli("queue", "show", job["id"], "--json")[1]), job)

    def test_show_on_a_job_that_is_not_there_fails_naming_it(self):
        self.assertEqual(self.run_cli("queue", "show", "20260101T000000000000Z-abcdef")[0], 1)
        self.assertIn("20260101T000000000000Z-abcdef", self.said)

    def test_a_job_file_somebody_broke_is_skipped_rather_than_taking_the_list_down(self):
        kept = self.queued()
        (q.jobs_dir() / "20260101T000000000000Z-ffffff.json").write_text("{not json")
        self.assertEqual(q.all_jobs(), [kept])

    def test_the_terminal_listing_names_the_job_its_stage_and_its_paper(self):
        job = self.queued()
        printed = self.run_cli("queue", "list")[1]
        self.assertIn(job["id"], printed)
        self.assertIn(pc.STAGE_QUEUED, printed)
        self.assertIn("p0.pdf", printed)

    def test_queue_with_no_action_prints_its_own_help(self):
        status, printed = self.run_cli("queue")
        self.assertEqual(status, 2)
        self.assertIn("add", printed)
        self.assertIn("retry", printed)


class ClearTest(QueueTestCase):
    def test_clear_forgets_the_finished_jobs_and_their_logs(self):
        done = self.queued()
        q.write_job(done | {"stage": pc.STAGE_DONE})
        Path(done["log"]).write_text("what happened")
        waiting = self.queued()
        self.assertEqual(self.run_cli("queue", "clear")[0], 0)
        self.assertEqual([job["id"] for job in q.all_jobs()], [waiting["id"]])
        self.assertFalse(Path(done["log"]).exists())

    def test_clear_leaves_a_job_that_is_running_alone(self):
        running = self.queued()
        q.write_job(running | {"stage": pc.STAGE_GENERATING})
        self.run_cli("queue", "clear")
        self.assertEqual(len(q.all_jobs()), 1)


class SpawnTest(QueueTestCase):
    """The detached runner, really spawned, on a Queue with nothing in it."""

    def test_the_runner_it_spawns_outlives_this_process_and_drains(self):
        with mock.patch.object(q.subprocess, "Popen") as popen:
            q.spawn_runner()
        command, kwargs = popen.call_args
        self.assertEqual(command[0][1:], [str(Path(pc.__file__).resolve()), "run-queue"])
        self.assertIs(kwargs["start_new_session"], True)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)

    def test_a_real_run_queue_on_an_empty_queue_exits_cleanly(self):
        finished = subprocess.run(  # noqa: S603 — the CLI, as `queue add` spawns it
            [sys.executable, str(Path(pc.__file__).resolve()), "run-queue"],
            env=os.environ, capture_output=True, timeout=60,
        )
        self.assertEqual(finished.returncode, 0, finished.stderr.decode())


if __name__ == "__main__":
    unittest.main()
