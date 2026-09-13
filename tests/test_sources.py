"""Tests for scripts/sources.py — what a Source may be named as, and what it becomes.

Nothing here touches the network. `tests/fixtures/arxiv/attention.xml` is a real
response, captured once from export.arxiv.org while #17 was written, and every
test that needs a feed parses those bytes as they came back — deriving the empty
one, which is a feed with its entry removed. (`error.xml` is the exception and
says so in itself.) `sources._open` is the single seam they all go through, and
it is stubbed in `setUp` to fail the test if anything opens a URL without saying
first what it should get back.

The reference table is the part worth labouring: every shape a user can paste is
asserted here, because the alternative to a table is finding out in the panel.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import paper_cast as pc  # noqa: E402
import queue_cli as q  # noqa: E402
import sources  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arxiv"

# Enough of a PDF for everything that inspects one here: the magic is what
# `download` checks, and a file with bytes in it is what `materialise` reads as a
# paper it already has.
PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer\n%%EOF\n"


def feed(name):
    return (FIXTURES / f"{name}.xml").read_bytes()


class Response:
    """What `urllib.request.urlopen` hands back, as much of it as we use."""

    def __init__(self, body):
        self.stream = io.BytesIO(body)

    def read(self, size=-1):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        return False


def answering(*bodies):
    """An opener that hands back these bodies in turn, and records what was asked."""
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return Response(bodies[min(len(calls) - 1, len(bodies) - 1)])

    opener.calls = calls
    return opener


def raising(error):
    def opener(_request, _timeout):
        raise error

    return opener


class SourcesTestCase(unittest.TestCase):
    """A temporary output_dir, and a network that is not there until it is asked for."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = pc.parse_config("") | {"output_dir": self.root / "Videos"}
        self.answers(raising(AssertionError("this test reached the network")))
        # The runner's progress lines belong in the Job log, not in the test output.
        said = mock.patch.object(pc, "say", lambda message: None)
        said.start()
        self.addCleanup(said.stop)

    def answers(self, opener):
        patch = mock.patch.object(sources, "_open", opener)
        patch.start()
        self.addCleanup(patch.stop)
        return opener

    def paper(self, name="attention.pdf"):
        path = self.root / name
        path.write_bytes(PDF_BYTES)
        return path

    def arxiv_source(self):
        """The canonical Source, resolved off the real captured feed."""
        self.answers(answering(feed("attention")))
        source = sources.resolve("arxiv.org/abs/1706.03762")
        self.answers(answering(PDF_BYTES))
        return source


class ReferenceTest(SourcesTestCase):
    """The whole accept/reject table, which is the part of #17 a user meets first."""

    ARXIV = (
        # Bare ids, both schemes, with and without a version.
        ("2401.12345", "2401.12345"),
        ("2401.12345v3", "2401.12345v3"),
        ("0704.0001", "0704.0001"),            # four digits: new style, pre-2015
        ("1706.03762v11", "1706.03762v11"),    # two-digit versions exist
        ("arXiv:1706.03762", "1706.03762"),    # how arXiv itself prints an id
        ("cs.CL/0301001", "cs.CL/0301001"),    # old style, retired March 2007
        ("cs.CL/0301001v2", "cs.CL/0301001v2"),
        ("hep-th/9901001", "hep-th/9901001"),  # an archive with no subject class
        ("math-ph/0101001", "math-ph/0101001"),
        ("  2401.12345  ", "2401.12345"),      # a paste brings whitespace with it
        # /abs/ URLs, with and without a scheme, a www, a version, a trailing slash.
        ("arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762", "1706.03762"),
        ("http://arxiv.org/abs/1706.03762v7", "1706.03762v7"),
        ("https://www.arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://export.arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/abs/1706.03762/", "1706.03762"),
        ("https://arxiv.org/abs/cs.CL/0301001", "cs.CL/0301001"),
        # /pdf/ URLs, which is what the site's own download button gives.
        ("arxiv.org/pdf/1706.03762", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762v7", "1706.03762v7"),
        ("https://arxiv.org/pdf/1706.03762v7.pdf", "1706.03762v7"),
        ("https://arxiv.org/pdf/cs.CL/0301001v1", "cs.CL/0301001v1"),
    )

    REFUSED = (
        ("", "no reference given"),
        ("   ", "no reference given"),
        # A DOI, spelled both ways. Resolving one means following redirects into
        # paywalls and Cloudflare, which #17 rules out by name.
        ("10.1145/3292500.3330701", "does not resolve DOIs"),
        ("https://doi.org/10.1145/3292500.3330701", "does not resolve DOIs"),
        ("https://dx.doi.org/10.1145/3292500.3330701", "does not resolve DOIs"),
        # Ids that are nearly right. Saying so beats asking arXiv about them.
        ("2401.123", "not a well-formed arXiv id"),
        ("24011.2345", "not a well-formed arXiv id"),
        ("cs.CL/030100", "not a well-formed arXiv id"),
        ("cs.CL/03010011", "not a well-formed arXiv id"),
        # arxiv.org, but not a paper.
        ("https://arxiv.org/", "not one naming a paper"),
        ("https://arxiv.org/list/cs.CL/2401", "not one naming a paper"),
        ("https://arxiv.org/abs/", "not one naming a paper"),
        # A landing page is not a PDF, and #17 does not scrape one to find the link.
        ("https://openreview.net/forum?id=abcdef", "does not read a paper's landing page"),
        ("https://example.com/papers/attention.html", "does not read a paper's landing page"),
        ("https://example.com/papers/", "does not read a paper's landing page"),
        # Schemes we do not fetch, and things that are simply nothing.
        ("ftp://example.com/attention.pdf", "not a scheme paper-cast fetches"),
        ("file:///home/me/attention.pdf", "not a scheme paper-cast fetches"),
        ("attention", "no such paper"),
        ("~/nowhere/attention.pdf", "no such paper"),
    )

    def test_every_accepted_arxiv_reference_reads_as_that_id(self):
        for ref, arxiv_id in self.ARXIV:
            with self.subTest(ref=ref):
                source = sources.parse_reference(ref)
                self.assertEqual(source["kind"], "arxiv")
                self.assertEqual(source["id"], arxiv_id)
                self.assertIsNone(source["path"])

    def test_an_arxiv_reference_is_normalised_to_its_abs_page(self):
        # Whatever was pasted, the Source records the one URL a human recognises.
        self.assertEqual(
            sources.parse_reference("arxiv.org/pdf/1706.03762v7.pdf")["url"],
            "https://arxiv.org/abs/1706.03762v7",
        )

    def test_a_direct_pdf_link_is_taken_as_it_stands(self):
        for ref in ("https://example.com/papers/attention.pdf",
                    "http://example.com/a.PDF",
                    "example.com/papers/attention.pdf"):
            with self.subTest(ref=ref):
                source = sources.parse_reference(ref)
                self.assertEqual(source["kind"], "url")
                self.assertTrue(source["url"].startswith("http"))
                self.assertIsNone(source["path"])

    def test_a_paper_on_disk_is_a_source_too_stored_absolute(self):
        # A Job outlives the shell it was queued from, so a relative path would
        # name nothing by the time the runner reaches it.
        paper = self.paper()
        here = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, here)
        source = sources.parse_reference(paper.name)
        self.assertEqual(source["kind"], "pdf")
        self.assertEqual(source["path"], str(paper.resolve()))
        self.assertIsNone(source["id"])

    def test_a_directory_with_a_dot_in_its_name_is_still_a_path(self):
        # `my.papers/attention.pdf` has a hostname's shape and a path's meaning.
        (self.root / "my.papers").mkdir()
        paper = self.paper("my.papers/attention.pdf")
        self.assertEqual(sources.parse_reference(str(paper))["path"], str(paper))

    def test_a_reference_that_is_none_of_those_is_refused_and_says_why(self):
        for ref, complaint in self.REFUSED:
            with self.subTest(ref=ref):
                with self.assertRaises(sources.SourceError) as caught:
                    sources.parse_reference(ref)
                self.assertIn(complaint, str(caught.exception))

    def test_refusing_a_reference_never_reaches_the_network(self):
        # The opener from setUp fails the test if anything opens a URL, so this
        # passing at all is the assertion: nothing in the table costs a request.
        for ref, _ in self.REFUSED:
            with self.subTest(ref=ref), contextlib.suppress(sources.SourceError):
                sources.parse_reference(ref)


class FeedTest(SourcesTestCase):
    """Real captured arXiv API responses, parsed the way the resolver parses them."""

    def test_the_canonical_paper_is_read_off_the_real_feed(self):
        # #17's done-when, from the bytes arxiv.org actually sent back.
        entry = sources.entry_of(feed("attention"), "1706.03762")
        self.assertEqual(entry["title"], "Attention Is All You Need")

    def test_an_unversioned_query_comes_back_pinned_to_a_version(self):
        # We keep what arXiv resolved to: the title just recorded describes that
        # version of the paper, and so will the PDF that gets downloaded.
        self.assertEqual(sources.entry_of(feed("attention"), "1706.03762")["id"], "1706.03762v7")

    def test_a_title_arrives_wrapped_and_is_put_back_on_one_line(self):
        # arXiv wraps its titles, and a title with a newline in it would reach the
        # Run directory's name, the notebook's name and YouTube.
        wrapped = feed("attention").replace(
            b"<title>Attention Is All You Need</title>",
            b"<title>Attention Is\n  All You   Need</title>",
        )
        self.assertEqual(
            sources.entry_of(wrapped, "1706.03762")["title"], "Attention Is All You Need"
        )

    def test_a_feed_with_no_entry_in_it_is_a_clear_failure(self):
        empty = feed("attention")
        empty = empty[: empty.index(b"<entry>")] + b"</feed>\n"
        with self.assertRaises(sources.SourceError) as caught:
            sources.entry_of(empty, "2401.99999")
        self.assertIn("2401.99999", str(caught.exception))

    def test_arxivs_own_complaint_is_passed_on_rather_than_guessed_at(self):
        # A malformed id is answered with HTTP 200 and an error entry, so the feed
        # is the only thing that says what arXiv objected to. Defence in depth: the
        # reference table refuses a malformed id long before the API sees one, which
        # is why this is the one fixture here that was written rather than captured.
        with self.assertRaises(sources.SourceError) as caught:
            sources.entry_of(feed("error"), "not-an-id")
        self.assertIn("incorrect id format", str(caught.exception))

    def test_something_that_is_not_a_feed_at_all_is_a_message_not_a_traceback(self):
        with self.assertRaises(sources.SourceError):
            sources.entry_of(b"<html>502 Bad Gateway</html>", "1706.03762")


class ResolveTest(SourcesTestCase):
    """`resolve` is what the panel calls at stage time (#17), so it is the contract."""

    def test_resolving_the_canonical_url_names_the_paper(self):
        self.answers(answering(feed("attention")))
        self.assertEqual(sources.resolve("arxiv.org/abs/1706.03762"), {
            "kind": "arxiv",
            "id": "1706.03762v7",
            "title": "Attention Is All You Need",
            "url": "https://arxiv.org/abs/1706.03762v7",
            "path": None,
        })

    def test_the_api_is_asked_politely_and_once(self):
        opener = self.answers(answering(feed("attention")))
        sources.resolve("1706.03762")
        (request, timeout), = opener.calls
        self.assertTrue(request.full_url.startswith("https://export.arxiv.org/api/query?"))
        self.assertIn("id_list=1706.03762", request.full_url)
        # arXiv's API terms ask for a descriptive agent, and a hung socket must not
        # take the panel down with it.
        self.assertIn("paper-cast", request.get_header("User-agent"))
        self.assertIn("github.com/TiagoVello/paper-cast", request.get_header("User-agent"))
        self.assertEqual(timeout, sources.API_TIMEOUT)

    def test_an_old_style_id_survives_being_put_in_a_query_string(self):
        opener = self.answers(answering(feed("attention")))
        sources.resolve("cs.CL/0301001")
        request, _ = opener.calls[0]
        self.assertIn("id_list=cs.CL%2F0301001", request.full_url)

    def test_a_local_paper_and_a_pdf_link_cost_no_request_at_all(self):
        # The opener from setUp fails the test if either of these asks for a URL.
        self.assertEqual(sources.resolve(str(self.paper()))["kind"], "pdf")
        self.assertEqual(sources.resolve("https://example.com/a.pdf")["kind"], "url")

    def test_a_network_that_is_not_there_is_a_message_and_not_a_traceback(self):
        self.answers(raising(urllib.error.URLError("Name or service not known")))
        with self.assertRaises(sources.SourceError) as caught:
            sources.resolve("1706.03762")
        self.assertIn("could not reach the arXiv API", str(caught.exception))

    def test_a_timeout_is_a_message_too(self):
        self.answers(raising(TimeoutError("timed out")))
        with self.assertRaises(sources.SourceError):
            sources.resolve("1706.03762")

    def test_being_rate_limited_says_so_in_those_words(self):
        # Worth its own message: it is the one failure that is nobody's mistake and
        # that goes away by itself.
        self.answers(raising(urllib.error.HTTPError(
            "https://export.arxiv.org/api/query", 429, "Too Many Requests", {}, None)))
        with self.assertRaises(sources.SourceError) as caught:
            sources.resolve("1706.03762")
        self.assertIn("rate-limiting", str(caught.exception))

    def test_a_server_error_names_the_status(self):
        self.answers(raising(urllib.error.HTTPError(
            "https://export.arxiv.org/api/query", 503, "Service Unavailable", {}, None)))
        with self.assertRaises(sources.SourceError) as caught:
            sources.resolve("1706.03762")
        self.assertIn("503", str(caught.exception))

    def test_a_feed_larger_than_the_cap_never_reaches_the_parser_whole(self):
        # The XML parser only ever sees bounded input, however the far end behaves.
        self.answers(answering(b"<feed>" + b"x" * (sources.MAX_FEED_BYTES * 2)))
        with self.assertRaises(sources.SourceError):
            sources.resolve("1706.03762")


class RunDirectoryTest(SourcesTestCase):
    """The Run directory has to be named before the pipeline that names it starts."""

    def job(self, *source_dicts, title=""):
        return {"id": "j", "title": title, "sources": list(source_dicts)}

    def pipeline_would_name(self, pdfs, title_override, source_title="", metadata=""):
        """The directory `run_pipeline` will choose, by its own rules, not a copy."""
        title = pc.resolve_title(
            title_override or None, metadata, pdfs[0],
            extra=len(pdfs) - 1, source_title=source_title,
        )
        return pc.run_directory(title, pdfs[0], self.config)

    def test_a_job_of_papers_on_disk_is_left_entirely_alone(self):
        # Nothing to place, so the pipeline names the run exactly as it always has.
        job = self.job(sources.pdf_source(self.paper()))
        self.assertIsNone(sources.episode_directory(job, self.config))

    def test_the_directory_is_the_one_the_pipeline_will_pick(self):
        source = self.arxiv_source()
        job = self.job(source)
        directory = sources.episode_directory(job, self.config)
        sources.materialise(source, directory)
        self.assertEqual(
            directory,
            self.pipeline_would_name([Path(source["path"])], "", source["title"]),
        )
        self.assertEqual(directory.name, "attention-is-all-you-need")

    def test_the_episodes_own_title_wins_and_names_the_directory(self):
        job = self.job(self.arxiv_source(), title="A Reading of Transformers")
        self.assertEqual(
            sources.episode_directory(job, self.config).name, "a-reading-of-transformers"
        )

    def test_a_combined_job_lands_every_paper_in_the_one_directory(self):
        first, second = self.arxiv_source(), self.arxiv_source()
        second["id"], second["title"] = "2401.12345", "Another Paper Entirely"
        job = self.job(first, second)
        directory = sources.episode_directory(job, self.config)
        self.answers(answering(PDF_BYTES))
        paths = [Path(sources.materialise(source, directory)["path"]) for source in job["sources"]]
        self.assertEqual({path.parent for path in paths}, {directory})
        self.assertEqual([path.name for path in paths],
                         ["attention-is-all-you-need.pdf", "another-paper-entirely.pdf"])
        # And the name still agrees with the pipeline's, "+ 1 more" and all.
        self.assertEqual(directory, self.pipeline_would_name(paths, "", first["title"]))
        self.assertEqual(directory.name, "attention-is-all-you-need-1-more")

    def test_a_fetched_paper_joins_a_local_one_in_the_local_papers_directory(self):
        # The one case where `pdfinfo` still decides the name: the first Source is
        # on disk and nothing has titled it, so it is asked here exactly as the
        # pipeline will ask it a moment later.
        metadata = "Title:          A Local Paper\nPages:          9\n"
        job = self.job(sources.pdf_source(self.paper()), self.arxiv_source())
        with mock.patch.object(sources.shutil, "which", return_value="/usr/bin/pdfinfo"), \
             mock.patch.object(pc, "run_step", return_value=metadata) as pdfinfo:
            directory = sources.episode_directory(job, self.config)
        self.assertEqual(pdfinfo.call_args.args[1][0], "pdfinfo")
        self.assertEqual(
            directory,
            self.pipeline_would_name(
                [Path(job["sources"][0]["path"]), Path("fetched.pdf")], "", metadata=metadata
            ),
        )
        self.assertEqual(directory.name, "a-local-paper-1-more")

    def test_without_pdfinfo_the_name_falls_back_rather_than_crashing(self):
        # `run_pipeline` checks for the missing tool itself and fails the Job before
        # a title is used for anything, so the two cannot end up disagreeing.
        job = self.job(sources.pdf_source(self.paper()), self.arxiv_source())
        with mock.patch.object(sources.shutil, "which", return_value=None):
            self.assertEqual(sources.episode_directory(job, self.config).name, "attention-1-more")

    def test_a_plain_pdf_link_names_the_run_after_the_file_it_points_at(self):
        source = sources.resolve("https://example.com/papers/Attention_Final.pdf")
        job = self.job(source)
        directory = sources.episode_directory(job, self.config)
        self.answers(answering(PDF_BYTES))
        path = Path(sources.materialise(source, directory)["path"])
        # Nothing has titled it, so the pipeline will name the run from this stem —
        # and a stem that slugifies back to the directory is what keeps them agreeing.
        self.assertEqual(directory.name, "attention-final")
        self.assertEqual(directory, self.pipeline_would_name([path], ""))


class MaterialiseTest(SourcesTestCase):
    """The runner's half: the PDF lands in the Run directory, beside its Episode."""

    def setUp(self):
        super().setUp()
        self.run_dir = self.config["output_dir"] / "attention-is-all-you-need"

    def fetched(self):
        return sources.materialise(self.arxiv_source(), self.run_dir)

    def test_an_arxiv_source_is_downloaded_into_the_run_directory(self):
        path = Path(self.fetched()["path"])
        self.assertEqual(path.read_bytes(), PDF_BYTES)
        self.assertEqual(path, self.run_dir / "attention-is-all-you-need.pdf")

    def test_the_pdf_is_fetched_from_arxivs_own_url_for_that_version(self):
        source = self.arxiv_source()
        opener = self.answers(answering(PDF_BYTES))
        sources.materialise(source, self.run_dir)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "https://arxiv.org/pdf/1706.03762v7")
        self.assertIn("paper-cast", request.get_header("User-agent"))
        self.assertEqual(timeout, sources.DOWNLOAD_TIMEOUT)

    def test_the_source_learns_where_the_paper_landed(self):
        # Updated in place, so the next write of the Job file records the path and
        # a retry finds the paper instead of downloading it again.
        source = self.arxiv_source()
        self.assertIs(sources.materialise(source, self.run_dir), source)
        self.assertTrue(Path(source["path"]).is_file())

    def test_a_paper_this_job_already_downloaded_is_not_downloaded_again(self):
        # #13's rule: a retry reuses what is on disk and never redoes expensive work.
        source = self.fetched()
        self.answers(raising(AssertionError("downloaded the same paper twice")))
        self.assertEqual(sources.materialise(source, self.run_dir)["path"], source["path"])

    def test_a_paper_that_has_gone_missing_since_is_fetched_again(self):
        source = self.fetched()
        Path(source["path"]).unlink()
        self.answers(answering(PDF_BYTES))
        sources.materialise(source, self.run_dir)
        self.assertTrue(Path(source["path"]).is_file())

    def test_a_zero_byte_leftover_does_not_count_as_a_paper(self):
        source = self.fetched()
        Path(source["path"]).write_bytes(b"")
        self.answers(answering(PDF_BYTES))
        sources.materialise(source, self.run_dir)
        self.assertEqual(Path(source["path"]).read_bytes(), PDF_BYTES)

    def test_a_local_paper_passes_straight_through(self):
        source = sources.pdf_source(self.paper())
        self.assertEqual(sources.materialise(dict(source), None), source)

    def test_something_that_is_not_a_pdf_is_refused_and_leaves_nothing_behind(self):
        source = self.arxiv_source()
        self.answers(answering(b"<html>the PDF is being generated</html>"))
        with self.assertRaises(pc.PipelineError) as caught:
            sources.materialise(source, self.run_dir)
        self.assertIn("did not return a PDF", str(caught.exception))
        self.assertEqual(list(self.config["output_dir"].rglob("*.part")), [])
        self.assertIsNone(source["path"])

    def test_a_download_that_fails_is_a_message_and_leaves_no_part_file(self):
        source = self.arxiv_source()
        self.answers(raising(urllib.error.HTTPError(
            "https://arxiv.org/pdf/1706.03762v7", 404, "Not Found", {}, None)))
        with self.assertRaises(pc.PipelineError) as caught:
            sources.materialise(source, self.run_dir)
        self.assertIn("404", str(caught.exception))
        self.assertEqual(list(self.config["output_dir"].rglob("*.part")), [])

    def test_a_download_with_no_end_to_it_is_stopped(self):
        source = self.arxiv_source()
        self.answers(answering(PDF_BYTES + b"0" * (sources.MAX_PDF_BYTES + 1)))
        with self.assertRaises(pc.PipelineError) as caught:
            sources.materialise(source, self.run_dir)
        self.assertIn("larger than", str(caught.exception))


class ResolveCommandTest(SourcesTestCase):
    """`paper-cast resolve <ref>` — the one call the panel makes (#15, #17)."""

    def run_cli(self, *argv):
        out, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errors):
            status = pc.main(list(argv))
        self.said = errors.getvalue()
        return status, out.getvalue()

    def test_it_prints_one_json_object_and_exits_zero(self):
        self.answers(answering(feed("attention")))
        status, printed = self.run_cli("resolve", "arxiv.org/abs/1706.03762")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(printed), {
            "kind": "arxiv",
            "id": "1706.03762v7",
            "title": "Attention Is All You Need",
            "url": "https://arxiv.org/abs/1706.03762v7",
            "path": None,
        })

    def test_asking_for_json_out_loud_prints_the_same_one_object(self):
        self.answers(answering(feed("attention")))
        _, bare = self.run_cli("resolve", "1706.03762")
        _, spelled = self.run_cli("resolve", "1706.03762", "--json")
        self.assertEqual(json.loads(bare), json.loads(spelled))

    def test_a_reference_that_names_nothing_exits_non_zero_with_the_reason(self):
        empty = feed("attention")
        self.answers(answering(empty[: empty.index(b"<entry>")] + b"</feed>\n"))
        status, printed = self.run_cli("resolve", "2401.99999")
        self.assertEqual(status, 1)
        self.assertEqual(printed, "")
        self.assertIn("2401.99999", self.said)

    def test_an_unsupported_reference_exits_non_zero_without_asking_arxiv(self):
        status, printed = self.run_cli("resolve", "https://doi.org/10.1145/3292500.3330701")
        self.assertEqual(status, 1)
        self.assertEqual(printed, "")
        self.assertIn("DOI", self.said)


class QueueingTest(SourcesTestCase):
    """#17's done-when, end to end: paste the URL, and a Source is staged by name."""

    def setUp(self):
        super().setUp()
        state = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")})
        state.start()
        self.addCleanup(state.stop)
        config = mock.patch.object(pc, "CONFIG_FILE", self.root / "config.toml")
        config.start()
        self.addCleanup(config.stop)
        pc.CONFIG_FILE.write_text(f'output_dir = "{self.config["output_dir"]}"\n')

    def add(self, *argv):
        out, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(q, "spawn_runner", mock.Mock()):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errors):
                status = pc.main(["queue", "add", *argv])
        self.said = errors.getvalue()
        return status, out.getvalue()

    def test_pasting_the_url_stages_a_source_titled_attention_is_all_you_need(self):
        self.answers(answering(feed("attention")))
        status, _ = self.add("arxiv.org/abs/1706.03762")
        self.assertEqual(status, 0)
        job, = q.all_jobs()
        self.assertEqual(job["sources"], [{
            "kind": "arxiv",
            "id": "1706.03762v7",
            "title": "Attention Is All You Need",
            "url": "https://arxiv.org/abs/1706.03762v7",
            "path": None,
        }])

    def test_a_dead_id_fails_at_stage_time_and_queues_nothing(self):
        empty = feed("attention")
        self.answers(answering(empty[: empty.index(b"<entry>")] + b"</feed>\n"))
        status, _ = self.add("2401.99999")
        self.assertEqual(status, 1)
        self.assertIn("2401.99999", self.said)
        self.assertEqual(q.all_jobs(), [])

    def test_one_dead_reference_stops_the_add_before_anything_is_queued(self):
        empty = feed("attention")
        self.answers(answering(empty[: empty.index(b"<entry>")] + b"</feed>\n"))
        status, _ = self.add(str(self.paper()), "2401.99999")
        self.assertEqual(status, 1)
        self.assertEqual(q.all_jobs(), [])

    def cast(self, job):
        """Run the Job as the runner would, with the pipeline itself stubbed out."""
        with mock.patch.object(pc, "run_pipeline") as pipeline:
            q.cast_job(job, lambda facts: None, pc.STAGE_GENERATING)
        return pipeline.call_args

    def test_the_runner_downloads_the_paper_and_lets_arxivs_title_win(self):
        # The Job's Source has no file when it is queued. By the time the pipeline
        # is called it has one, in the Run directory, and the title handed over is
        # the one arXiv gave — `pdfinfo` reads no Title at all out of this paper (#2).
        self.answers(answering(feed("attention")))
        self.add("arxiv.org/abs/1706.03762")
        job, = q.all_jobs()
        self.answers(answering(PDF_BYTES))
        call = self.cast(job)
        (pdfs, title, config), source_title = call.args, call.kwargs["source_title"]
        self.assertEqual(source_title, "Attention Is All You Need")
        self.assertIsNone(title)
        self.assertEqual(pdfs[0].read_bytes(), PDF_BYTES)
        self.assertEqual(pdfs[0].parent, config["output_dir"] / "attention-is-all-you-need")
        # And the Source now knows where it landed, for the next write of the Job.
        self.assertEqual(job["sources"][0]["path"], str(pdfs[0]))

    def test_a_job_of_papers_on_disk_reaches_the_pipeline_exactly_as_before(self):
        paper = self.paper()
        self.add(str(paper))
        job, = q.all_jobs()
        call = self.cast(job)
        self.assertEqual(call.args[0], [paper])
        self.assertEqual(call.kwargs["source_title"], "")

    def test_a_retry_reuses_the_paper_it_already_downloaded(self):
        self.answers(answering(feed("attention")))
        self.add("1706.03762")
        job, = q.all_jobs()
        self.answers(answering(PDF_BYTES))
        first = self.cast(job)
        # The Job file carries the path now, so nothing may go near the network.
        self.answers(raising(AssertionError("a retry re-downloaded the paper")))
        self.assertEqual(self.cast(job).args[0], first.args[0])


if __name__ == "__main__":
    unittest.main()
