"""Unit tests for scripts/setup_cli.py — the Setup wizard's detectors and seams.

Setup's whole contract is that it is a detector before it is an action (#12), so
that is what these tests exercise: what "already satisfied" means for each of the
six steps, what goes in the wrapper, and the headline acceptance criterion —
running Setup twice changes nothing the second time.

Nothing here runs `pacman`, `sudo`, `uv`, `nlm`, `gum`, `omarchy` or a browser.
Every foreign program is a fake, `run_visible` fails the test if a second run
calls it at all, and the only files written are in a temporary directory.
"""

import argparse
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config_cli as cc  # noqa: E402
import paper_cast as pc  # noqa: E402
import setup_cli as sc  # noqa: E402


class Finished:
    """What subprocess.run hands back, with only the two fields we read."""

    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def which_map(present):
    """A `shutil.which` that knows about exactly these programs."""
    return lambda program: f"/usr/bin/{program}" if program in present else None


ALL_PROGRAMS = ("ffmpeg", "pdfinfo", "pdftoppm", "uv", "inotifywait", "nlm")


def quietly(call, *args, **kwargs):
    """Run something noisy and hand back (result, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        result = call(*args, **kwargs)
    return result, out.getvalue()


class PromptTest(unittest.TestCase):
    """Interactive by default, --yes for scripts, and never a guess (#12)."""

    def test_yes_answers_without_asking_anything(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("asked anyway")):
            self.assertTrue(sc.Prompt(assume_yes=True, interactive=False, gum="/usr/bin/gum").confirm("?"))

    def test_no_tty_and_no_yes_refuses_rather_than_guessing(self):
        prompt = sc.Prompt(assume_yes=False, interactive=False, gum="/usr/bin/gum")
        with self.assertRaises(sc.SetupError) as caught:
            prompt.confirm("Install things?")
        self.assertIn("--yes", str(caught.exception))

    def test_a_tty_asks_gum_and_reads_its_exit_code(self):
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum="/usr/bin/gum")
        with mock.patch("subprocess.run", return_value=Finished(0)) as run:
            self.assertTrue(prompt.confirm("Go on?"))
        self.assertEqual(run.call_args.args[0], ["/usr/bin/gum", "confirm", "Go on?"])
        with mock.patch("subprocess.run", return_value=Finished(1)):
            self.assertFalse(prompt.confirm("Go on?"))

    def test_without_gum_it_falls_back_to_stdin(self):
        # A `curl | bash` machine need not have gum; it is Omarchy's, not ours.
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with mock.patch("builtins.input", return_value="y"):
            self.assertTrue(prompt.confirm("Go on?"))
        with mock.patch("builtins.input", return_value=""):
            self.assertFalse(prompt.confirm("Go on?"))

    def test_choose_keeps_the_default_for_anyone_who_cannot_be_asked(self):
        for prompt in (
            sc.Prompt(assume_yes=True, interactive=True, gum="/usr/bin/gum"),
            sc.Prompt(assume_yes=False, interactive=False, gum="/usr/bin/gum"),
            sc.Prompt(assume_yes=False, interactive=True, gum=None),
        ):
            with mock.patch("subprocess.run", side_effect=AssertionError("asked anyway")):
                self.assertEqual(prompt.choose("Which?", ["a", "b"], "a"), "a")

    def test_choose_takes_gums_answer_and_survives_a_cancel(self):
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum="/usr/bin/gum")
        with mock.patch("subprocess.run", return_value=Finished(0, "b\n")):
            self.assertEqual(prompt.choose("Which?", ["a", "b"], "a"), "b")
        # A cancelled gum exits non-zero, and cancelling means "leave it alone".
        with mock.patch("subprocess.run", return_value=Finished(130, "")):
            self.assertEqual(prompt.choose("Which?", ["a", "b"], "a"), "a")


class PrerequisiteTest(unittest.TestCase):
    """Step 1 is satisfied by binaries on PATH, not by a package database."""

    def test_nothing_missing_when_every_program_is_on_path(self):
        self.assertEqual(sc.missing_packages(which_map(ALL_PROGRAMS)), [])

    def test_a_package_is_named_when_any_one_of_its_programs_is_absent(self):
        # poppler ships both, so losing either is losing the package.
        self.assertEqual(
            sc.missing_packages(which_map(("ffmpeg", "pdfinfo", "uv", "inotifywait"))), ["poppler"]
        )
        self.assertEqual(
            sc.missing_packages(which_map(("ffmpeg", "pdftoppm", "uv", "inotifywait"))), ["poppler"]
        )

    def test_a_bare_machine_needs_every_package(self):
        self.assertEqual(
            sc.missing_packages(which_map(())), ["ffmpeg", "poppler", "uv", "inotify-tools"]
        )

    def test_inotify_tools_is_the_bar_widgets_and_never_the_pipelines(self):
        # #14's QueueState.qml is the only thing that calls `inotifywait`, so a
        # headless box must not be refused a Job for want of it.
        self.assertIn("inotify-tools", sc.missing_packages(which_map(())))
        self.assertNotIn("inotifywait", pc.REQUIRED_TOOLS)

    def test_pacman_is_asked_for_what_is_needed_and_nothing_more(self):
        self.assertEqual(
            sc.pacman_command(["poppler"]), ["sudo", "pacman", "-S", "--needed", "poppler"]
        )
        # --noconfirm only for a caller that has already said yes to everything.
        self.assertIn("--noconfirm", sc.pacman_command(["poppler"], assume_yes=True))

    def test_nlm_comes_from_uv_and_not_from_pip_or_pacman(self):
        self.assertEqual(
            sc.uv_tool_install_command(), ["uv", "tool", "install", "notebooklm-mcp-cli"]
        )

    def test_the_step_is_satisfied_and_silent_when_everything_is_installed(self):
        with mock.patch.object(sc.shutil, "which", which_map(ALL_PROGRAMS)), mock.patch.object(
            sc, "run_visible", side_effect=AssertionError("installed something anyway")
        ):
            status, _ = quietly(sc.step_prerequisites, sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.SATISFIED)

    def test_a_just_installed_nlm_is_findable_for_the_rest_of_the_run(self):
        # uv puts it in ~/.local/bin, which this process's PATH may not hold, and
        # step 2 would otherwise report the nlm step 1 just installed as missing.
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}):
            sc.ensure_bin_on_path()
            self.assertTrue(sc.on_path(sc.BIN_DIR))
            was = os.environ["PATH"]
            sc.ensure_bin_on_path()
            self.assertEqual(os.environ["PATH"], was, "added twice")

    def test_declining_pacman_leaves_the_step_outstanding(self):
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with mock.patch.object(sc.shutil, "which", which_map(("nlm",))), mock.patch.object(
            sc, "run_visible", side_effect=AssertionError("installed anyway")
        ), mock.patch.object(prompt, "confirm", return_value=False):
            status, _ = quietly(sc.step_prerequisites, prompt)
        self.assertEqual(status, sc.OUTSTANDING)


class NotebookLMTest(unittest.TestCase):
    """Step 2 asks the server, because only the server knows if a session lives."""

    def test_the_session_probe_is_the_one_the_pipeline_makes(self):
        calls = []

        def fake(command):
            calls.append(command)
            return Finished(0)

        self.assertTrue(sc.notebooklm_ready(fake))
        self.assertEqual(calls, [["nlm", "auth", "refresh"]])
        self.assertFalse(sc.notebooklm_ready(lambda command: Finished(1)))

    def test_a_live_session_logs_nobody_in_again(self):
        with mock.patch.object(sc.shutil, "which", which_map(("nlm",))), mock.patch.object(
            sc, "run_quiet", return_value=Finished(0)
        ), mock.patch.object(sc, "run_visible", side_effect=AssertionError("opened a browser")):
            status, _ = quietly(sc.step_notebooklm, sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.SATISFIED)

    def test_no_nlm_means_there_is_no_session_to_log_in_to(self):
        with mock.patch.object(sc.shutil, "which", which_map(())), mock.patch.object(
            sc, "run_visible", side_effect=AssertionError("opened a browser")
        ):
            status, _ = quietly(sc.step_notebooklm, sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.OUTSTANDING)


class CredentialTest(unittest.TestCase):
    """Step 3 is satisfied by both halves of the credential being on disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.secret = Path(self.tmp.name) / "client_secret.json"
        self.token = Path(self.tmp.name) / "youtube_token.json"

    def test_both_halves_are_needed(self):
        self.assertFalse(sc.youtube_ready(self.secret, self.token))
        self.secret.write_text("{}")
        self.assertFalse(sc.youtube_ready(self.secret, self.token))
        self.token.write_text("{}")
        self.assertTrue(sc.youtube_ready(self.secret, self.token))

    def test_a_present_credential_does_not_reopen_the_console(self):
        self.secret.write_text("{}")
        self.token.write_text("{}")
        with mock.patch.object(sc, "CLIENT_SECRET", self.secret), mock.patch.object(
            sc, "TOKEN_FILE", self.token
        ), mock.patch.object(sc, "run_visible", side_effect=AssertionError("ran the bootstrap")):
            status, _ = quietly(sc.step_youtube, sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.SATISFIED)

    def test_the_detector_never_goes_to_the_network(self):
        # check_credential() refreshes the token over the wire; Setup must not,
        # because the pipeline's own pre-flight does it with a better error.
        self.secret.write_text("{}")
        self.token.write_text("{}")
        with mock.patch.object(
            sc.youtube_auth, "check_credential", side_effect=AssertionError("went to Google")
        ):
            self.assertTrue(sc.youtube_ready(self.secret, self.token))


class WrapperTest(unittest.TestCase):
    """Step 4: one small file, pointing back into this checkout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.wrapper = self.bin / "paper-cast"
        self.entry = Path(self.tmp.name) / "checkout" / "scripts" / "paper_cast.py"

    def wrote(self, prompt=None):
        with mock.patch.object(sc, "BIN_DIR", self.bin), mock.patch.object(
            sc, "WRAPPER", self.wrapper
        ), mock.patch.object(sc, "ENTRY_POINT", self.entry), mock.patch.dict(
            os.environ, {"PATH": str(self.bin)}
        ):
            return quietly(sc.step_wrapper, prompt or sc.Prompt(assume_yes=True))

    def test_it_execs_the_python_inside_the_checkout(self):
        text = sc.wrapper_text(self.entry)
        self.assertIn(f'exec python3 {self.entry} "$@"', text)
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        # Nothing is copied out of the checkout, so an `omarchy plugin update`
        # moves the CLI and the bar widget together (#10).
        self.assertNotIn("cp ", text)

    def test_a_checkout_path_with_a_space_in_it_still_runs(self):
        awkward = Path("/home/a b/plugins/it's here/scripts/paper_cast.py")
        text = sc.wrapper_text(awkward)
        self.assertIn("'/home/a b/plugins/it'\"'\"'s here/scripts/paper_cast.py'", text)

    def test_current_means_ours_and_pointing_here(self):
        self.assertFalse(sc.wrapper_is_current(self.wrapper, self.entry))
        self.wrote()
        self.assertTrue(sc.wrapper_is_current(self.wrapper, self.entry))
        # A wrapper from a checkout that has since moved is not current, which is
        # why this compares contents rather than just asking if the file exists.
        self.assertFalse(sc.wrapper_is_current(self.wrapper, Path("/elsewhere/paper_cast.py")))

    def test_it_is_written_executable(self):
        self.wrote()
        self.assertTrue(self.wrapper.stat().st_mode & stat.S_IXUSR)

    def test_a_second_run_rewrites_nothing(self):
        status, _ = self.wrote()
        self.assertEqual(status, sc.DONE)
        before = self.wrapper.stat().st_mtime_ns
        status, output = self.wrote()
        self.assertEqual(status, sc.SATISFIED)
        self.assertEqual(self.wrapper.stat().st_mtime_ns, before)
        self.assertIn("already runs", output)

    def test_a_foreign_paper_cast_is_not_clobbered_without_a_yes(self):
        self.bin.mkdir(parents=True)
        self.wrapper.write_text("#!/bin/sh\n# somebody else's\n")
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with mock.patch.object(prompt, "confirm", return_value=False):
            status, _ = self.wrote(prompt)
        self.assertEqual(status, sc.OUTSTANDING)
        self.assertIn("somebody else's", self.wrapper.read_text())

    def test_ours_is_recognised_by_its_marker(self):
        self.wrote()
        self.assertTrue(sc.is_ours(self.wrapper))
        self.wrapper.write_text("#!/bin/sh\nexec something-else\n")
        self.assertFalse(sc.is_ours(self.wrapper))
        self.assertFalse(sc.is_ours(self.bin / "not-there"))

    def test_a_symlink_at_the_wrapper_path_is_never_written_through(self):
        # The exact shape of the incident this guards against: a hand-made
        # symlink at ~/.local/bin/paper-cast pointing into a real working
        # checkout. Setup must not open that path for writing and follow it
        # into the file it points at.
        self.bin.mkdir(parents=True)
        victim = Path(self.tmp.name) / "working-checkout" / "paper_cast.py"
        victim.parent.mkdir(parents=True)
        victim_contents = "#!/usr/bin/env python3\nprint('the real CLI, hand-edited')\n"
        victim.write_text(victim_contents)
        self.wrapper.symlink_to(victim)

        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with mock.patch.object(prompt, "confirm", return_value=True):
            status, _ = self.wrote(prompt)

        self.assertEqual(status, sc.DONE)
        # The symlink is gone, replaced with a real file that is ours...
        self.assertFalse(self.wrapper.is_symlink())
        self.assertTrue(sc.wrapper_is_current(self.wrapper, self.entry))
        # ...and the file it used to point at is untouched, byte for byte.
        self.assertEqual(victim.read_text(), victim_contents)

    def test_a_broken_symlink_is_replaced_and_never_followed(self):
        # `exists()` calls a dangling symlink absent, which would have written
        # the wrapper to wherever it pointed — creating a file somewhere nobody
        # asked for one.
        self.bin.mkdir(parents=True)
        target = Path(self.tmp.name) / "gone" / "paper_cast.py"
        self.wrapper.symlink_to(target)

        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with mock.patch.object(prompt, "confirm", return_value=True):
            status, _ = self.wrote(prompt)

        self.assertEqual(status, sc.DONE)
        self.assertFalse(self.wrapper.is_symlink())
        self.assertTrue(sc.wrapper_is_current(self.wrapper, self.entry))
        self.assertFalse(target.exists())

    def test_a_missing_bin_directory_on_path_is_warned_about(self):
        with mock.patch.object(sc, "BIN_DIR", self.bin), mock.patch.object(
            sc, "WRAPPER", self.wrapper
        ), mock.patch.object(sc, "ENTRY_POINT", self.entry), mock.patch.dict(
            os.environ, {"PATH": "/usr/bin"}
        ):
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                sc.step_wrapper(sc.Prompt(assume_yes=True))
        self.assertIn("not on your PATH", errors.getvalue())


class PathTest(unittest.TestCase):
    def test_a_directory_is_found_however_the_path_spells_it(self):
        home = Path.home() / ".local" / "bin"
        self.assertTrue(sc.on_path(home, f"/usr/bin{os.pathsep}{home}"))
        self.assertTrue(sc.on_path(home, "~/.local/bin"))
        self.assertFalse(sc.on_path(home, "/usr/bin:/usr/local/bin"))
        # An empty PATH entry means the current directory, and is not this one.
        self.assertFalse(sc.on_path(home, f"{os.pathsep}/usr/bin{os.pathsep}"))
        self.assertFalse(sc.on_path(home, ""))


class SteeringTest(unittest.TestCase):
    """Step 5 is the one about the product: which Steering the hosts get."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.toml"

    def run_step(self, prompt):
        with mock.patch.object(sc, "CONFIG_FILE", self.config), mock.patch.object(
            cc, "CONFIG_FILE", self.config
        ):
            return quietly(sc.step_steering, prompt)

    def loaded(self):
        return tomllib.loads(self.config.read_text())

    def test_it_writes_the_starter_presets_and_loads_one(self):
        status, output = self.run_step(sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.DONE)
        config = self.loaded()
        self.assertEqual([p["name"] for p in config["presets"]], sc.preset_names())
        self.assertEqual(config["steering_preset"], sc.preset_names()[0])
        self.assertEqual(config["focus"], sc.preset_text(config["steering_preset"]))
        # The step explains what Steering is for; it is not a silent file write.
        self.assertIn("Steering is the instruction", output)

    def test_the_presets_are_the_ones_ticket_11_left_and_not_a_second_copy(self):
        self.assertIs(sc.STARTER_PRESETS, cc.STARTER_PRESETS)
        self.assertEqual(sc.preset_names(), [p["name"] for p in cc.STARTER_PRESETS])

    def test_a_chosen_preset_becomes_the_loaded_steering(self):
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with mock.patch.object(prompt, "choose", return_value="Critical read"):
            self.run_step(prompt)
        config = self.loaded()
        self.assertEqual(config["steering_preset"], "Critical read")
        self.assertEqual(config["focus"], sc.preset_text("Critical read"))

    def test_an_existing_config_is_never_rewritten(self):
        # The headline rule, at its sharpest: a Steering somebody wrote by hand
        # must survive a re-run of Setup untouched.
        self.config.write_text('focus = "My own Steering, thank you"\n')
        before = self.config.read_text()
        status, output = self.run_step(sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.SATISFIED)
        self.assertEqual(self.config.read_text(), before)
        self.assertIn("already exists", output)

    def test_the_steering_is_shown_wrapped_rather_than_as_one_long_line(self):
        lines = sc.wrap_quote("word " * 40, width=30)
        self.assertTrue(all(len(line) <= 30 for line in lines))
        self.assertEqual(" ".join(lines), ("word " * 40).strip())
        self.assertEqual(sc.wrap_quote(""), [])


class PluginTest(unittest.TestCase):
    """Step 6 is an offer, never a requirement: the CLI is the whole product."""

    def listing(self, **plugin):
        return json.dumps([{"id": "omarchy.clock", "enabled": True}, plugin] if plugin else [])

    def test_it_reads_the_plugins_own_enabled_flag(self):
        self.assertEqual(sc.plugin_state(self.listing(id=sc.PLUGIN_ID, enabled=True)), "enabled")
        self.assertEqual(sc.plugin_state(self.listing(id=sc.PLUGIN_ID, enabled=False)), "disabled")
        self.assertEqual(sc.plugin_state(self.listing()), "absent")

    def test_output_that_is_not_json_reads_as_absent_rather_than_crashing(self):
        self.assertEqual(sc.plugin_state(""), "absent")
        self.assertEqual(sc.plugin_state("omarchy-plugin-list: command not found"), "absent")

    def test_an_enabled_widget_is_satisfied(self):
        with mock.patch.object(
            sc.shutil, "which", which_map(("omarchy-plugin-list",))
        ), mock.patch.object(
            sc, "run_quiet", return_value=Finished(0, self.listing(id=sc.PLUGIN_ID, enabled=True))
        ), mock.patch.object(sc, "run_visible", side_effect=AssertionError("enabled it again")):
            status, _ = quietly(sc.step_plugin, sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.SATISFIED)

    def test_no_omarchy_is_not_a_failure(self):
        with mock.patch.object(sc.shutil, "which", which_map(())):
            status, output = quietly(sc.step_plugin, sc.Prompt(assume_yes=True))
        self.assertEqual(status, sc.NOT_APPLICABLE)
        self.assertIn("works exactly the same from a terminal", output)


class SecondRunTest(unittest.TestCase):
    """The acceptance criterion on #12: running Setup twice changes nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.bin = self.tmp / "bin"
        self.wrapper = self.bin / "paper-cast"
        self.entry = self.tmp / "checkout" / "scripts" / "paper_cast.py"
        self.config = self.tmp / "config.toml"
        self.secret = self.tmp / "client_secret.json"
        self.token = self.tmp / "youtube_token.json"
        self.secret.write_text("{}")
        self.token.write_text("{}")

    @contextlib.contextmanager
    def machine(self, allow_actions):
        """A machine with the foreign programs present and nothing of ours on it."""
        visible = mock.DEFAULT if allow_actions else mock.Mock(
            side_effect=AssertionError("a satisfied step did something anyway")
        )
        with mock.patch.object(sc, "BIN_DIR", self.bin), mock.patch.object(
            sc, "WRAPPER", self.wrapper
        ), mock.patch.object(sc, "ENTRY_POINT", self.entry), mock.patch.object(
            sc, "CONFIG_FILE", self.config
        ), mock.patch.object(cc, "CONFIG_FILE", self.config), mock.patch.object(
            sc, "CLIENT_SECRET", self.secret
        ), mock.patch.object(sc, "TOKEN_FILE", self.token), mock.patch.object(
            sc.shutil, "which", which_map(ALL_PROGRAMS)
        ), mock.patch.object(sc, "run_quiet", return_value=Finished(0)), mock.patch.object(
            sc, "run_visible", visible
        ), mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            yield

    def run_setup(self, allow_actions=False):
        with self.machine(allow_actions):
            return quietly(sc.setup_command, argparse.Namespace(assume_yes=True))

    def test_the_first_run_sets_the_machine_up_and_the_second_touches_nothing(self):
        code, output = self.run_setup(allow_actions=True)
        self.assertEqual(code, 0)
        self.assertIn("Ready.", output)

        state = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (self.wrapper, self.config)
        }

        code, output = self.run_setup()
        self.assertEqual(code, 0)
        for path, (content, mtime) in state.items():
            self.assertEqual(path.read_bytes(), content, f"{path} was rewritten")
            self.assertEqual(path.stat().st_mtime_ns, mtime, f"{path} was touched")
        # Every step reported itself already done, and none of them acted: the
        # run_visible above raises if anything tried.
        self.assertNotIn("▸ 6/6", output.split("already")[0])

    def test_a_declined_required_step_is_named_and_the_exit_code_says_not_ready(self):
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with self.machine(allow_actions=False), mock.patch.object(
            sc.shutil, "which", which_map(("ffmpeg", "pdfinfo", "pdftoppm", "uv", "inotifywait"))
        ), mock.patch.object(sc, "Prompt", return_value=prompt), mock.patch.object(
            prompt, "confirm", return_value=False
        ):
            code, output = quietly(sc.setup_command, argparse.Namespace(assume_yes=False))
        self.assertEqual(code, 1)
        self.assertIn("Prerequisites", output + "")

    def test_no_tty_and_no_yes_stops_before_anything_happens(self):
        with self.machine(allow_actions=False), mock.patch.object(sc, "is_interactive", return_value=False):
            code, _ = quietly(sc.guarded(sc.setup_command), argparse.Namespace(assume_yes=False))
        self.assertEqual(code, 1)
        self.assertFalse(self.wrapper.exists())
        self.assertFalse(self.config.exists())


class UninstallTest(unittest.TestCase):
    """Take the wrapper off PATH, and say plainly what is being left behind."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.wrapper = self.bin / "paper-cast"
        self.wrapper.write_text(sc.wrapper_text(self.tmp / "scripts" / "paper_cast.py"))
        self.config_dir = self.tmp / "config"
        self.state_dir = self.tmp / "state"
        self.videos = self.tmp / "Videos"
        for directory in (self.config_dir, self.state_dir, self.videos):
            directory.mkdir()

    @contextlib.contextmanager
    def machine(self, omarchy=True):
        with mock.patch.object(sc, "WRAPPER", self.wrapper), mock.patch.object(
            sc, "CONFIG_DIR", self.config_dir
        ), mock.patch.object(sc, "STATE_DIR", self.state_dir), mock.patch.object(
            sc, "output_dir", return_value=self.videos
        ), mock.patch.object(
            sc.shutil, "which", which_map(("omarchy-plugin-list",) if omarchy else ())
        ):
            yield

    def test_a_symlink_is_left_alone_and_what_it_points_at_survives(self):
        # The mirror of the write bug (#12): `uninstall` must not decide a
        # symlink is ours by reading through it, and must not remove it.
        victim = self.tmp / "working-checkout" / "paper_cast.py"
        victim.parent.mkdir(parents=True)
        victim.write_text("#!/usr/bin/env python3\nprint('the real CLI')\n")
        self.wrapper.unlink()
        self.wrapper.symlink_to(victim)

        with self.machine():
            quietly(sc.uninstall_command, argparse.Namespace(assume_yes=True))

        self.assertTrue(self.wrapper.is_symlink())
        self.assertEqual(victim.read_text(), "#!/usr/bin/env python3\nprint('the real CLI')\n")

    def uninstall(self, assume_yes=True):
        with self.machine():
            return quietly(sc.uninstall_command, argparse.Namespace(assume_yes=assume_yes))

    def test_it_removes_our_wrapper_and_names_what_survives(self):
        code, output = self.uninstall()
        self.assertEqual(code, 0)
        self.assertFalse(self.wrapper.exists())
        for path in (self.config_dir, self.state_dir, self.videos):
            self.assertIn(str(path), output)
            self.assertTrue(path.exists())

    def test_it_says_to_remove_the_plugin_afterwards_and_not_before(self):
        _, output = self.uninstall()
        self.assertIn(f"omarchy plugin remove {sc.PLUGIN_ID}", output)

    def test_it_names_the_things_that_are_not_ours_to_take_away(self):
        _, output = self.uninstall()
        self.assertIn("uv tool uninstall notebooklm-mcp-cli", output)
        self.assertIn("YouTube channel", output)

    def test_a_foreign_paper_cast_on_path_is_left_alone(self):
        self.wrapper.write_text("#!/bin/sh\n# not ours\n")
        errors = io.StringIO()
        with self.machine(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            code = sc.uninstall_command(argparse.Namespace(assume_yes=True))
        self.assertEqual(code, 0)
        self.assertTrue(self.wrapper.exists())
        self.assertIn("not written by paper-cast", errors.getvalue())

    def test_saying_no_changes_nothing(self):
        prompt = sc.Prompt(assume_yes=False, interactive=True, gum=None)
        with self.machine(), mock.patch.object(sc, "Prompt", return_value=prompt), mock.patch.object(
            prompt, "confirm", return_value=False
        ):
            code, _ = quietly(sc.uninstall_command, argparse.Namespace(assume_yes=False))
        self.assertEqual(code, 0)
        self.assertTrue(self.wrapper.exists())

    def test_a_second_uninstall_is_not_an_error(self):
        self.uninstall()
        code, output = self.uninstall()
        self.assertEqual(code, 0)
        self.assertIn("No ", output)

    def test_only_the_directories_that_exist_are_listed(self):
        __import__("shutil").rmtree(self.state_dir)
        with self.machine():
            paths = [path for path, _ in sc.leftovers()]
        self.assertNotIn(self.state_dir, paths)
        self.assertIn(self.config_dir, paths)

    def test_a_config_that_will_not_parse_does_not_stop_the_listing(self):
        broken = self.tmp / "broken.toml"
        broken.write_text("format = 'not-a-format'\n")
        with mock.patch.object(sc, "CONFIG_FILE", broken):
            self.assertEqual(sc.output_dir(), Path(pc.DEFAULTS["output_dir"]).expanduser())


class SubcommandTest(unittest.TestCase):
    """The seam into paper_cast.py: `register(subparsers)` and nothing else."""

    def setUp(self):
        self.parser = pc.build_parser()

    def test_both_commands_are_on_the_cli(self):
        self.assertIn("setup", self.parser.commands)
        self.assertIn("uninstall", self.parser.commands)

    def test_yes_is_spelled_both_ways_on_both_commands(self):
        for command in ("setup", "uninstall"):
            for flag in ("--yes", "-y"):
                self.assertTrue(self.parser.parse_args([command, flag]).assume_yes)
            self.assertFalse(self.parser.parse_args([command]).assume_yes)

    def test_a_bare_setup_is_not_mistaken_for_a_paper(self):
        # `with_default_command` turns anything unknown into `cast <that>`.
        self.assertEqual(pc.with_default_command(["setup"], self.parser.commands), ["setup"])
        self.assertEqual(pc.with_default_command(["uninstall"], self.parser.commands), ["uninstall"])

    def test_a_setup_error_becomes_a_message_and_an_exit_code(self):
        def boom(_args):
            raise sc.SetupError("no terminal to ask on")

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = sc.guarded(boom)(argparse.Namespace())
        self.assertEqual(code, 1)
        self.assertIn("no terminal to ask on", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
