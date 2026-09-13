"""Unit tests for scripts/config_cli.py — the serialiser and the `config` surface.

tomllib reads TOML and does not write it, so paper-cast owns the writing half of
the round trip, and these tests lean on it hard: a Steering preset's text is
prose with newlines, quotes and `\"\"\"` in it, and the panel writes it through
here (#11). Nothing starts a subprocess or touches the network; the only files
written are in a temporary directory.
"""

import contextlib
import io
import json
import random
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config_cli as cc  # noqa: E402
import paper_cast as pc  # noqa: E402

NASTY_TEXTS = (
    "",
    "Skip the background.",
    'Assume I know what "attention" is.',
    "Two lines.\nAnd a second one.",
    'A "quote" on line one,\nand a second "quote" here.',
    'Three in a row: """ — and four: """"',
    "A backslash \\ and an escape-looking \\n that is not one",
    "Trailing quote \"",
    "Trailing backslash \\",
    "\nA text that opens with a newline",
    "A text that ends with a newline\n",
    "Tabs\tinside\tit",
    "A carriage\r\nreturn pair",
    "Unicode: café, ☕, 数学, 🧪",
    "A bell \a and a null \x00 nobody meant to type",
    "word " * 500,
)


class TomlStringTest(unittest.TestCase):
    """Everything paper-cast writes has to read back as exactly what went in."""

    def round_trip(self, text):
        written = f"text = {cc.toml_string(text)}"
        return tomllib.loads(written)["text"], written

    def test_every_shape_of_steering_survives_the_round_trip(self):
        for text in NASTY_TEXTS:
            with self.subTest(text=text[:40]):
                self.assertEqual(self.round_trip(text)[0], text)

    def test_a_generated_pile_of_the_awkward_characters_survives_too(self):
        # The escaping rules interact — a quote run ending a line, a backslash
        # before a quote — so the cases are generated rather than imagined.
        alphabet = '"\\\n\tx é\r\x00'
        source = random.Random(11)
        for _ in range(400):
            text = "".join(source.choice(alphabet) for _ in range(source.randrange(12)))
            with self.subTest(text=repr(text)):
                self.assertEqual(self.round_trip(text)[0], text)

    def test_prose_with_newlines_is_written_in_the_readable_form(self):
        # A Steering is meant to be edited in the file, not squinted at as one
        # long escaped line.
        written = self.round_trip("Build up from the problem.\nDefine the terms.")[1]
        self.assertIn('"""Build up from the problem.\nDefine the terms."""', written)

    def test_a_quote_in_prose_is_left_alone_and_a_run_of_three_is_not(self):
        self.assertNotIn("\\", self.round_trip('He said "no".\nShe agreed.')[1])
        self.assertIn("\\", self.round_trip('A """ run.\nAnd more.')[1])

    def test_a_single_line_text_stays_on_one_line(self):
        self.assertEqual(cc.toml_string("Skip the background."), '"Skip the background."')


class TomlValueTest(unittest.TestCase):
    def test_booleans_are_written_as_toml_booleans(self):
        self.assertEqual(cc.toml_value("keep_video", True), "true")
        self.assertEqual(cc.toml_value("keep_video", False), "false")

    def test_a_path_under_home_is_written_back_with_the_tilde(self):
        # parse_config expands it; freezing this machine's home into the file
        # would make the config unshareable for no reason.
        self.assertEqual(
            cc.toml_value("output_dir", Path.home() / "Videos" / "paper-cast"),
            '"~/Videos/paper-cast"',
        )

    def test_a_path_outside_home_is_written_as_it_stands(self):
        self.assertEqual(cc.toml_value("output_dir", Path("/mnt/big/casts")), '"/mnt/big/casts"')

    def test_something_a_config_cannot_hold_is_a_config_error_naming_the_key(self):
        with self.assertRaises(pc.ConfigError) as caught:
            cc.toml_value("length", 7)
        self.assertIn("length", str(caught.exception))


class DumpConfigTest(unittest.TestCase):
    def test_the_defaults_dump_to_a_file_that_parses_back_to_the_defaults(self):
        defaults = pc.parse_config("")
        self.assertEqual(pc.parse_config(cc.dump_config(defaults)), defaults)

    def test_a_config_with_presets_round_trips_whole(self):
        config = pc.parse_config("") | {
            "steering_preset": "Teach me",
            "focus": 'Build up from the problem.\nAnd define "the terms".',
            "presets": [
                {"name": "Skim it", "text": "Five minutes, not twenty."},
                {"name": "Teach me", "text": 'Build up from the problem.\nAnd define "the terms".'},
            ],
        }
        self.assertEqual(pc.parse_config(cc.dump_config(config)), config)

    def test_the_presets_go_last_because_a_table_array_swallows_what_follows(self):
        text = cc.dump_config(pc.parse_config("") | {"presets": [{"name": "Skim it", "text": ""}]})
        self.assertLess(text.index("keep_video"), text.index("[[presets]]"))

    def test_a_key_the_caller_left_out_is_written_as_its_default(self):
        self.assertEqual(pc.parse_config(cc.dump_config({})), pc.parse_config(""))

    def test_every_key_is_written_even_when_it_is_the_default(self):
        text = cc.dump_config(pc.parse_config(""))
        for key in pc.DEFAULTS:
            if key != "presets":
                self.assertIn(f"{key} = ", text)

    def test_a_preset_that_is_not_a_table_is_a_config_error_naming_the_entry(self):
        with self.assertRaises(pc.ConfigError) as caught:
            cc.dump_config(pc.parse_config("") | {"presets": ["Skim it"]})
        self.assertIn("presets[0]", str(caught.exception))

    def test_an_entry_missing_its_name_is_written_and_then_rejected_by_the_parser(self):
        # dump_config writes what it was given; parse_config is the only validator
        # (#10), so a panel's bad preset fails with the parser's own message.
        text = cc.dump_config(pc.parse_config("") | {"presets": [{"text": "No name here."}]})
        with self.assertRaises(pc.ConfigError) as caught:
            pc.parse_config(text)
        self.assertIn("presets[0]", str(caught.exception))


class ValueFromTextTest(unittest.TestCase):
    def test_a_string_is_taken_exactly_as_it_was_given(self):
        for text in NASTY_TEXTS:
            with self.subTest(text=text[:40]):
                self.assertEqual(cc.value_from_text("focus", text), text)

    def test_a_boolean_key_takes_true_and_false_and_nothing_else(self):
        self.assertIs(cc.value_from_text("keep_video", "true"), True)
        self.assertIs(cc.value_from_text("keep_video", "false"), False)
        with self.assertRaises(pc.ConfigError) as caught:
            cc.value_from_text("keep_video", "yes")
        self.assertIn("keep_video", str(caught.exception))

    def test_the_presets_arrive_as_json_because_they_are_structured(self):
        self.assertEqual(
            cc.value_from_text("presets", '[{"name": "Skim it", "text": "Five minutes.\\nNot twenty."}]'),
            [{"name": "Skim it", "text": "Five minutes.\nNot twenty."}],
        )

    def test_presets_that_are_not_json_fail_naming_the_key(self):
        for text in ("[{name: 'Skim it'}]", '{"name": "Skim it"}'):
            with self.subTest(text=text), self.assertRaises(pc.ConfigError) as caught:
                cc.value_from_text("presets", text)
            self.assertIn("presets", str(caught.exception))

    def test_an_unknown_key_fails_naming_it_and_listing_what_is_known(self):
        with self.assertRaises(pc.ConfigError) as caught:
            cc.value_from_text("langauge", "en")
        message = str(caught.exception)
        self.assertIn("langauge", message)
        self.assertIn("language", message)


class ConfigFileTest(unittest.TestCase):
    """The file on disk: written whole, validated before it lands, optional throughout."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "paper-cast" / "config.toml"

    def test_saving_a_config_and_loading_it_back_returns_the_same_config(self):
        config = pc.parse_config("") | {
            "focus": 'A Steering with "quotes",\na newline, and a \\ backslash.',
            "length": "long",
            "keep_video": True,
        }
        cc.save_config(config, self.path)
        self.assertEqual(pc.load_config(self.path), config)

    def test_the_directory_is_made_when_it_is_not_there_yet(self):
        cc.save_config(pc.parse_config(""), self.path)
        self.assertTrue(self.path.is_file())

    def test_a_config_that_would_not_load_is_never_written(self):
        cc.save_config(pc.parse_config("") | {"focus": "The one that works."}, self.path)
        with self.assertRaises(pc.ConfigError):
            cc.save_config(pc.parse_config("") | {"presets": [{"text": "No name."}]}, self.path)
        self.assertEqual(pc.load_config(self.path)["focus"], "The one that works.")

    def test_the_written_file_says_where_the_documented_shape_lives(self):
        cc.save_config(pc.parse_config(""), self.path)
        self.assertIn("config.example.toml", self.path.read_text())

    def test_a_missing_file_still_loads_as_the_defaults(self):
        self.assertEqual(pc.load_config(self.path), pc.parse_config(""))


class WriteFreshConfigTest(unittest.TestCase):
    """#12's Setup seam: the starter presets land in the user's own file."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.toml"

    def test_it_writes_the_starter_presets_the_panel_flips_through(self):
        self.assertIs(cc.write_fresh_config(self.path), True)
        presets = pc.load_config(self.path)["presets"]
        self.assertEqual(
            [preset["name"] for preset in presets],
            ["ML researcher", "Skim it", "Critical read", "Teach me", "Adjacent field"],
        )

    def test_the_first_preset_is_the_loaded_one_so_a_fresh_box_has_a_steering(self):
        cc.write_fresh_config(self.path)
        config = pc.load_config(self.path)
        self.assertEqual(config["steering_preset"], "ML researcher")
        self.assertEqual(config["focus"], cc.STARTER_PRESETS[0]["text"])

    def test_everything_else_is_left_at_its_default(self):
        cc.write_fresh_config(self.path)
        config = pc.load_config(self.path)
        for key in ("language", "format", "length", "output_dir", "keep_artifacts", "keep_video"):
            self.assertEqual(config[key], pc.parse_config("")[key])

    def test_a_re_run_of_setup_never_overwrites_a_steering_somebody_wrote(self):
        cc.write_fresh_config(self.path)
        cc.save_config(pc.load_config(self.path) | {"steering_preset": "", "focus": "Mine."}, self.path)
        self.assertIs(cc.write_fresh_config(self.path), False)
        self.assertEqual(pc.load_config(self.path)["focus"], "Mine.")

    def test_no_starter_preset_is_capped_at_the_textareas_five_hundred_characters(self):
        # #11: the cap is Google's own textarea, so nothing here enforces one. The
        # presets are short because they are prompts, not because they must be.
        for preset in cc.STARTER_PRESETS:
            self.assertTrue(preset["text"].strip())


class ConfigCommandTest(unittest.TestCase):
    """The surface the bar panel shells out to, driven the way a shell drives it."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.toml"
        patch = mock.patch.object(cc, "CONFIG_FILE", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def given_presets(self):
        """A config with presets in it, as Setup would have left one."""
        cc.save_config(
            pc.parse_config("")
            | {"presets": [{"name": "Skim it", "text": "Five minutes."},
                           {"name": "Critical read", "text": "Like a reviewer.\nLooking to reject."}]},
            self.path,
        )

    def run_config(self, *argv, stdin=None):
        """The command, its exit code and its stdout; what it said on stderr in `self.said`."""
        out, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errors):
            with mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
                status = pc.main(["config", *argv])
        self.said = errors.getvalue()
        return status, out.getvalue()

    def test_get_prints_one_value_on_a_box_with_no_config_at_all(self):
        self.assertEqual(self.run_config("get", "format"), (0, "deep_dive\n"))

    def test_get_prints_a_boolean_as_toml_spells_it(self):
        self.assertEqual(self.run_config("get", "keep_video"), (0, "false\n"))

    def test_get_prints_the_output_dir_expanded_because_a_shell_cannot_use_a_tilde(self):
        self.assertEqual(self.run_config("get", "output_dir")[1].strip(), str(Path.home() / "Videos/paper-cast"))

    def test_get_on_an_unknown_key_fails_naming_the_key(self):
        self.assertEqual(self.run_config("get", "langauge"), (1, ""))
        self.assertIn("langauge", self.said)

    def test_set_then_get_round_trips_a_steering_with_newlines_and_quotes(self):
        steering = 'Talk to a researcher.\nSkip the background, and define "attention" for nobody.'
        self.assertEqual(self.run_config("set", "focus", steering)[0], 0)
        self.assertEqual(self.run_config("get", "focus")[1], steering + "\n")

    def test_set_reads_the_value_from_stdin_when_argv_would_mangle_it(self):
        steering = 'A Steering with a """ run in it,\nand a trailing backslash \\'
        self.assertEqual(self.run_config("set", "focus", "--stdin", stdin=steering + "\n")[0], 0)
        self.assertEqual(self.run_config("get", "focus")[1], steering + "\n")

    def test_set_presets_round_trips_the_text_byte_for_byte(self):
        text = 'Read it like a reviewer.\n\tWhere is the evidence thin?\nAnd the """ awkward bits \\ too.'
        as_json = json.dumps([{"name": "Critical read", "text": text}])
        self.assertEqual(self.run_config("set", "presets", as_json)[0], 0)
        self.assertEqual(pc.load_config(self.path)["presets"], [{"name": "Critical read", "text": text}])

    def test_set_takes_a_value_or_stdin_and_not_both_or_neither(self):
        self.assertEqual(self.run_config("set", "focus")[0], 1)
        self.assertEqual(self.run_config("set", "focus", "Mine.", "--stdin")[0], 1)

    def test_set_on_an_unknown_key_leaves_the_file_alone(self):
        self.assertEqual(self.run_config("set", "langauge", "en")[0], 1)
        self.assertFalse(self.path.exists())

    def test_setting_the_steering_preset_loads_its_text_into_the_focus(self):
        # Loading a preset is what puts a Steering in front of the hosts; the name
        # on its own would leave the panel and the Job disagreeing.
        self.given_presets()
        self.assertEqual(self.run_config("set", "steering_preset", "Critical read")[0], 0)
        self.assertEqual(self.run_config("get", "focus")[1], "Like a reviewer.\nLooking to reject.\n")

    def test_setting_the_steering_preset_to_nothing_keeps_the_steering(self):
        self.given_presets()
        self.run_config("set", "focus", "Hand written.")
        self.assertEqual(self.run_config("set", "steering_preset", "")[0], 0)
        self.assertEqual(self.run_config("get", "focus")[1], "Hand written.\n")

    def test_a_steering_preset_naming_no_preset_fails_and_writes_nothing(self):
        self.given_presets()
        before = self.path.read_text()
        self.assertEqual(self.run_config("set", "steering_preset", "Critcal read")[0], 1)
        self.assertIn("Critcal read", self.said)
        self.assertEqual(self.path.read_text(), before)

    def test_list_prints_what_config_toml_would_say(self):
        status, printed = self.run_config("list")
        self.assertEqual(status, 0)
        self.assertEqual(pc.parse_config(printed), pc.parse_config(""))
        self.assertIn('output_dir = "~/Videos/paper-cast"', printed)

    def test_list_as_json_holds_every_key_the_panel_needs(self):
        self.given_presets()
        listed = json.loads(self.run_config("list", "--json")[1])
        self.assertEqual(sorted(listed), sorted(pc.DEFAULTS))
        self.assertEqual([preset["name"] for preset in listed["presets"]], ["Skim it", "Critical read"])
        self.assertEqual(listed["output_dir"], str(Path.home() / "Videos/paper-cast"))

    def test_config_with_no_action_prints_its_own_help(self):
        status, printed = self.run_config()
        self.assertEqual(status, 2)
        self.assertIn("get", printed)
        self.assertIn("set", printed)


if __name__ == "__main__":
    unittest.main()
