"""Unit tests for the pure seams of scripts/paper_cast.py.

Per #8 these cover pure functions only — config parsing, slug derivation, title
resolution, the command lines handed to foreign programs, and how a studio poll
reads. Nothing here starts a subprocess or touches the network, so #7's decision
to inline `nlm` cannot invalidate them.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import paper_cast as pc  # noqa: E402
import youtube_auth as ya  # noqa: E402


class ParseConfigTest(unittest.TestCase):
    def test_an_absent_file_is_the_whole_default_set(self):
        config = pc.parse_config("")
        self.assertEqual(config["language"], "en")
        self.assertEqual(config["format"], "deep_dive")
        self.assertEqual(config["length"], "default")
        self.assertEqual(config["focus"], "")
        self.assertIs(config["keep_artifacts"], False)
        self.assertIs(config["keep_video"], False)

    def test_a_key_in_the_file_wins_over_its_default(self):
        config = pc.parse_config('language = "pt-BR"\nformat = "brief"\n')
        self.assertEqual(config["language"], "pt-BR")
        self.assertEqual(config["format"], "brief")
        self.assertEqual(config["length"], "default")

    def test_output_dir_comes_back_as_an_expanded_path(self):
        config = pc.parse_config('output_dir = "~/Elsewhere"')
        self.assertEqual(config["output_dir"], Path.home() / "Elsewhere")

    def test_the_default_output_dir_is_expanded_too(self):
        self.assertEqual(pc.parse_config("")["output_dir"], Path.home() / "Videos" / "paper-cast")

    def test_an_unknown_key_is_a_hard_error_naming_the_key(self):
        with self.assertRaises(pc.ConfigError) as caught:
            pc.parse_config('langauge = "en"')
        self.assertIn("langauge", str(caught.exception))

    def test_a_misspelled_format_is_rejected_rather_than_silently_ignored(self):
        with self.assertRaises(pc.ConfigError) as caught:
            pc.parse_config('format = "deep-dive"')
        message = str(caught.exception)
        self.assertIn("format", message)
        self.assertIn("deep_dive", message)

    def test_an_out_of_range_length_is_rejected(self):
        with self.assertRaises(pc.ConfigError) as caught:
            pc.parse_config('length = "epic"')
        self.assertIn("length", str(caught.exception))

    def test_a_key_of_the_wrong_type_is_rejected(self):
        for key in ("keep_artifacts", "keep_video"):
            with self.subTest(key=key), self.assertRaises(pc.ConfigError) as caught:
                pc.parse_config(f'{key} = "yes"')
            self.assertIn(key, str(caught.exception))

    def test_the_shipped_example_says_exactly_what_the_defaults_are(self):
        # config.example.toml is the repo's only prose about the settings, so it
        # drifting away from DEFAULTS would be a documented lie.
        example = Path(__file__).resolve().parents[1] / "config.example.toml"
        self.assertEqual(pc.parse_config(example.read_text()), pc.parse_config(""))

    def test_malformed_toml_is_a_config_error_not_a_traceback(self):
        with self.assertRaises(pc.ConfigError):
            pc.parse_config("language = ")


class SlugTest(unittest.TestCase):
    def test_a_plain_title_becomes_lowercase_and_hyphenated(self):
        self.assertEqual(pc.slugify("Attention Is All You Need"), "attention-is-all-you-need")

    def test_accents_are_folded_to_ascii_rather_than_dropped(self):
        self.assertEqual(pc.slugify("Atenção É Tudo"), "atencao-e-tudo")

    def test_a_run_of_punctuation_collapses_to_a_single_hyphen(self):
        self.assertEqual(pc.slugify("Deep   Learning: A  Survey (2024)"), "deep-learning-a-survey-2024")

    def test_leading_and_trailing_punctuation_is_stripped(self):
        self.assertEqual(pc.slugify("  -- Hello! --  "), "hello")

    def test_a_long_title_is_truncated_at_eighty_characters(self):
        slug = pc.slugify("word " * 40)
        self.assertLessEqual(len(slug), 80)
        self.assertFalse(slug.endswith("-"))

    def test_a_title_with_nothing_sluggable_in_it_is_empty(self):
        self.assertEqual(pc.slugify("中文 ???"), "")


class RunSlugTest(unittest.TestCase):
    def test_the_title_wins_when_it_slugs_to_something(self):
        self.assertEqual(
            pc.run_slug("Attention Is All You Need", Path("/tmp/1706.03762v7.pdf")),
            "attention-is-all-you-need",
        )

    def test_the_filename_stem_carries_it_when_the_title_does_not(self):
        # Per #2 this is the common path: the most-cited ML paper has no pdfinfo Title.
        self.assertEqual(pc.run_slug("", Path("/tmp/Attention Is All You Need.pdf")), "attention-is-all-you-need")

    def test_a_paper_with_no_usable_name_at_all_still_gets_a_directory(self):
        self.assertEqual(pc.run_slug("", Path("/tmp/中文.pdf")), "paper")


PDFINFO_WITH_TITLE = """\
Title:          Attention Is All You Need
Subject:
Author:         Ashish Vaswani
Creator:        LaTeX with hyperref
Pages:          15
Page size:      612 x 792 pts (letter)
"""

PDFINFO_WITHOUT_TITLE = """\
Creator:        LaTeX with hyperref
Producer:       pdfTeX-1.40.25
Pages:          15
"""


class PdfinfoTitleTest(unittest.TestCase):
    def test_reads_the_title_field(self):
        self.assertEqual(pc.pdfinfo_title(PDFINFO_WITH_TITLE), "Attention Is All You Need")

    def test_an_absent_title_field_reads_as_no_title(self):
        self.assertEqual(pc.pdfinfo_title(PDFINFO_WITHOUT_TITLE), "")

    def test_an_empty_title_field_reads_as_no_title(self):
        self.assertEqual(pc.pdfinfo_title("Title:\nPages:          15\n"), "")

    def test_a_title_holding_a_colon_survives_intact(self):
        self.assertEqual(
            pc.pdfinfo_title("Title:          BERT: Pre-training of Deep Transformers\n"),
            "BERT: Pre-training of Deep Transformers",
        )

    def test_a_field_that_merely_ends_in_title_is_not_the_title(self):
        self.assertEqual(pc.pdfinfo_title("Subtitle:       Not this one\n"), "")


class ResolveTitleTest(unittest.TestCase):
    def test_the_override_beats_the_metadata(self):
        self.assertEqual(
            pc.resolve_title("Mine", PDFINFO_WITH_TITLE, Path("/tmp/paper.pdf")), "Mine"
        )

    def test_the_metadata_is_used_when_there_is_no_override(self):
        self.assertEqual(
            pc.resolve_title(None, PDFINFO_WITH_TITLE, Path("/tmp/paper.pdf")),
            "Attention Is All You Need",
        )

    def test_the_filename_stem_carries_it_when_the_metadata_is_empty(self):
        self.assertEqual(
            pc.resolve_title(None, PDFINFO_WITHOUT_TITLE, Path("/tmp/1706.03762v7.pdf")),
            "1706.03762v7",
        )

    def test_a_paper_with_no_name_at_all_leaves_the_title_to_the_uploader(self):
        # Degenerate — a real PDF has a filename — but it is the seam's contract:
        # youtube_auth.sanitize_title turns an empty title into "Untitled paper" (#6),
        # and answering "paper" here would mean that settled fallback could never fire.
        self.assertEqual(pc.resolve_title(None, "", Path("")), "")

    def test_whitespace_around_a_title_does_not_survive(self):
        self.assertEqual(
            pc.resolve_title("  Spaced  Out  ", PDFINFO_WITHOUT_TITLE, Path("/tmp/p.pdf")),
            "Spaced Out",
        )


class CoverCommandTest(unittest.TestCase):
    def command(self):
        return pc.cover_command(Path("/papers/paper.pdf"), Path("/runs/slug/cover"))

    def test_renders_only_the_first_page(self):
        command = self.command()
        self.assertEqual(command[command.index("-f") + 1], "1")
        self.assertEqual(command[command.index("-l") + 1], "1")

    def test_writes_one_png_at_the_exact_prefix(self):
        # -singlefile is what makes the output `cover.png` rather than `cover-01.png`.
        command = self.command()
        self.assertIn("-png", command)
        self.assertIn("-singlefile", command)
        self.assertEqual(command[-2:], ["/papers/paper.pdf", "/runs/slug/cover"])


class AudioCreateCommandTest(unittest.TestCase):
    def command(self, **overrides):
        return pc.audio_create_command("nb-1", pc.parse_config("") | overrides)

    def test_carries_the_notebook_id_as_the_positional(self):
        self.assertEqual(self.command()[:3], ["nlm", "audio", "create"])
        self.assertEqual(self.command()[3], "nb-1")

    def test_passes_the_configured_language_format_and_length(self):
        command = self.command(language="pt-BR", format="brief", length="long")
        self.assertEqual(command[command.index("--language") + 1], "pt-BR")
        self.assertEqual(command[command.index("--format") + 1], "brief")
        self.assertEqual(command[command.index("--length") + 1], "long")

    def test_never_waits_for_a_confirmation_prompt(self):
        self.assertIn("--confirm", self.command())

    def test_asks_for_json_because_the_artifact_id_is_parsed_out_of_it(self):
        self.assertIn("--json", self.command())

    def test_an_empty_focus_is_left_off_rather_than_sent_as_an_empty_string(self):
        self.assertNotIn("--focus", self.command())

    def test_a_configured_focus_is_passed_through(self):
        command = self.command(focus="the training objective")
        self.assertEqual(command[command.index("--focus") + 1], "the training objective")


class FfmpegCommandTest(unittest.TestCase):
    def command(self):
        return pc.ffmpeg_command(Path("/r/cover.png"), Path("/r/episode.m4a"), Path("/r/episode.mp4"))

    def test_loops_the_cover_as_the_first_input_and_the_audio_as_the_second(self):
        command = self.command()
        self.assertEqual(command[command.index("-loop") + 1], "1")
        inputs = [command[i + 1] for i, arg in enumerate(command) if arg == "-i"]
        self.assertEqual(inputs, ["/r/cover.png", "/r/episode.m4a"])

    def test_terminates_with_shortest_because_a_looped_image_never_ends(self):
        self.assertIn("-shortest", self.command())

    def test_copies_the_audio_rather_than_re_encoding_it(self):
        # #2: the source is ~257 kbps AAC, so a 192k re-encode is slower and worse.
        command = self.command()
        self.assertEqual(command[command.index("-c:a") + 1], "copy")

    def test_encodes_the_video_in_a_pixel_format_players_accept(self):
        command = self.command()
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")

    def test_pads_the_page_to_even_dimensions_h264_can_encode(self):
        command = self.command()
        self.assertEqual(
            command[command.index("-vf") + 1],
            "scale=1280:-2,pad=ceil(iw/2)*2:ceil(ih/2)*2",
        )

    def test_keeps_the_frame_rate_low(self):
        command = self.command()
        self.assertEqual(command[command.index("-framerate") + 1], "2")
        self.assertEqual(command[command.index("-r") + 1], "2")

    def test_does_not_force_a_gop_length(self):
        # Measured: -g 4 on a 22-minute episode is a keyframe of a dense page every
        # two seconds — 200 MB against 47 MB with x264's own GOP. YouTube re-encodes
        # anyway, so its closed-GOP advice buys nothing here.
        self.assertNotIn("-g", self.command())

    def test_puts_the_moov_atom_up_front_for_youtube(self):
        command = self.command()
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")

    def test_overwrites_the_previous_run_and_writes_the_output_last(self):
        command = self.command()
        self.assertIn("-y", command)
        self.assertEqual(command[-1], "/r/episode.mp4")


class ArtifactStatusTest(unittest.TestCase):
    """`nlm studio status --json` prints a list of artifacts; the poll reads one of them."""

    def test_reads_the_status_of_the_artifact_being_waited_on(self):
        payload = [{"id": "art-1", "artifact_id": "art-1", "type": "audio", "status": "completed"}]
        self.assertEqual(pc.artifact_status(payload, "art-1"), "completed")

    def test_an_in_flight_generation_reads_as_unknown_which_means_keep_waiting(self):
        # #2: audio generation never reports in_progress, it falls through to unknown.
        payload = [{"artifact_id": "art-1", "type": "audio", "status": "unknown"}]
        self.assertEqual(pc.artifact_status(payload, "art-1"), "unknown")

    def test_an_artifact_that_has_not_appeared_yet_reads_as_unknown(self):
        self.assertEqual(pc.artifact_status([], "art-1"), "unknown")

    def test_another_artifact_in_the_notebook_is_not_mistaken_for_ours(self):
        payload = [{"artifact_id": "mind-map", "type": "mind_map", "status": "completed"}]
        self.assertEqual(pc.artifact_status(payload, "art-1"), "unknown")

    def test_an_artifact_keyed_only_by_id_is_still_found(self):
        self.assertEqual(pc.artifact_status([{"id": "art-1", "status": "failed"}], "art-1"), "failed")

    def test_a_missing_status_field_reads_as_unknown_rather_than_crashing(self):
        self.assertEqual(pc.artifact_status([{"artifact_id": "art-1"}], "art-1"), "unknown")


class EpisodeDescriptionTest(unittest.TestCase):
    """#6 bounded the description mechanically and deferred its content to this ticket."""

    def describe(self, **overrides):
        return pc.episode_description(
            "Attention Is All You Need",
            Path("/papers/1706.03762v7.pdf"),
            pc.parse_config("") | overrides,
        )

    def test_leads_with_the_paper_it_is_about(self):
        self.assertIn("Attention Is All You Need", self.describe())

    def test_names_the_pdf_it_was_made_from_without_leaking_the_local_path(self):
        description = self.describe()
        self.assertIn("1706.03762v7.pdf", description)
        self.assertNotIn("/papers/", description)

    def test_records_the_settings_the_overview_was_generated_with(self):
        description = self.describe(format="debate", length="long", language="pt-BR")
        self.assertIn("debate", description)
        self.assertIn("long", description)
        self.assertIn("pt-BR", description)

    def test_says_the_narration_is_machine_generated(self):
        self.assertIn("NotebookLM", self.describe())
        self.assertIn("synthetic", self.describe().lower())

    def test_a_configured_focus_is_recorded_and_an_empty_one_is_not(self):
        self.assertIn("the training objective", self.describe(focus="the training objective"))
        self.assertNotIn("Focus:", self.describe())

    def test_holds_nothing_youtube_would_reject(self):
        description = self.describe()
        self.assertEqual(ya.sanitize_description(description), description)

    def test_the_title_also_survives_youtube_as_it_stands(self):
        self.assertEqual(ya.sanitize_title("Attention Is All You Need"), "Attention Is All You Need")


class CleanupTest(unittest.TestCase):
    """#8's rule: the keep_ keys tidy up after a clean run, and never delete evidence."""

    def targets(self, uploaded, **overrides):
        run = pc.RunPaths(Path("/videos/paper-cast/slug"))
        return [
            path.name
            for path in pc.cleanup_targets(run, pc.parse_config("") | overrides, uploaded=uploaded)
        ]

    def test_a_clean_run_on_the_defaults_sheds_all_three(self):
        # The episode is on YouTube by now; the local copy is a second copy.
        self.assertEqual(self.targets(uploaded=True), ["cover.png", "episode.m4a", "episode.mp4"])

    def test_keep_video_keeps_the_mp4_and_still_sheds_the_intermediates(self):
        self.assertEqual(
            self.targets(uploaded=True, keep_video=True), ["cover.png", "episode.m4a"]
        )

    def test_keep_artifacts_keeps_the_intermediates_and_still_sheds_the_mp4(self):
        self.assertEqual(self.targets(uploaded=True, keep_artifacts=True), ["episode.mp4"])

    def test_both_keys_together_keep_all_three(self):
        self.assertEqual(self.targets(uploaded=True, keep_artifacts=True, keep_video=True), [])

    def test_a_run_that_never_uploaded_deletes_nothing(self):
        # A half-finished run is exactly when the .m4a needs to still be there — and
        # with the upload never made, the .mp4 is the only copy of the episode.
        self.assertEqual(self.targets(uploaded=False), [])


class RunPathsTest(unittest.TestCase):
    def test_the_three_artifacts_sit_in_one_directory_per_paper(self):
        run = pc.RunPaths(Path("/videos/paper-cast/attention-is-all-you-need"))
        self.assertEqual(run.cover, Path("/videos/paper-cast/attention-is-all-you-need/cover.png"))
        self.assertEqual(run.audio, Path("/videos/paper-cast/attention-is-all-you-need/episode.m4a"))
        self.assertEqual(run.video, Path("/videos/paper-cast/attention-is-all-you-need/episode.mp4"))


if __name__ == "__main__":
    unittest.main()
