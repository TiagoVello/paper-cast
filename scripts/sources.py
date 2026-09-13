#!/usr/bin/env python3
"""What a Source may be named as, and how a named one becomes a file on disk.

A Source is a paper (CONTEXT.md): a local PDF, or an arXiv entry named by id or
URL. This module is the only place that knows how to read a reference somebody
typed or pasted, ask arXiv what it is, and put the PDF in the Run directory (#17).

Two moments, deliberately far apart:

`resolve` runs at **stage time** — in the panel, and in `queue add` — and costs
one call to the arXiv API. It answers "what is this, and what is it called?", so
a dead id fails while the user is still looking at the box it was pasted into,
rather than fifteen minutes later in the runner (#17).

`materialise` runs in the **runner**, when the Job's turn comes, and downloads
the PDF into the Run directory, so the Source ends up beside the Episode it
produced. It is a single function, called from a single line of
`queue_cli.source_paths` (#18 left the line marked). The Run directory is named
before the pipeline that will name it has started, which is the awkward part:
`episode_directory` works it out from the same `resolve_title` and `run_slug` the
pipeline uses, fed the same inputs, so the two cannot answer differently.

What is accepted is a short, closed list: arXiv ids, arXiv `/abs/` and `/pdf/`
URLs, a direct link to a PDF, and a local file. A DOI or a publisher's landing
page is refused by name (#17): resolving those means following redirects into
paywalls and Cloudflare, which is a different problem from this one.

The dict these functions pass around *is* the Source entry of a Job file — the
same five keys, so `paper-cast resolve` prints exactly what the Queue will carry:

    kind    "pdf" (a file on disk), "arxiv" (an arXiv entry), or "url" (a PDF
            somewhere on the web).
    id      the arXiv id, version and all, or null.
    title   what arXiv calls it. "" when nothing has said yet — a local PDF is
            named by `pdfinfo` or by its filename, much later, in the pipeline.
    url     where it came from, for a Source that came from the web, or null.
    path    the paper on disk, or null until `materialise` has fetched it.

Stdlib only, per the standing decisions on the map.
"""

from __future__ import annotations

import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

import paper_cast as pc
from paper_cast import ConfigError, PipelineError

SOURCE_PDF = "pdf"
SOURCE_ARXIV = "arxiv"
SOURCE_URL = "url"

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_ABS = "https://arxiv.org/abs/"
ARXIV_PDF = "https://arxiv.org/pdf/"

# arXiv's API terms ask for a descriptive User-Agent, so this one names the
# program and where to complain about it. A robot that cannot be identified is
# the one that gets the whole user agent blocked.
USER_AGENT = "paper-cast/1 (+https://github.com/TiagoVello/paper-cast)"
API_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 60
# The API answers for one id in a couple of kilobytes. A megabyte is far more
# than that and still small enough that a feed which came back wrong cannot cost
# anything: it is read before the XML parser sees a byte of it.
MAX_FEED_BYTES = 1024 * 1024
# A dense paper with figures runs to tens of megabytes; nothing on arXiv is
# anywhere near this. It is here so a URL that turns out to be a video stream
# stops, rather than filling the disk under ~/Videos.
MAX_PDF_BYTES = 256 * 1024 * 1024
CHUNK = 64 * 1024

ATOM = "{http://www.w3.org/2005/Atom}"

# What arXiv's own error entries carry in place of an id, per its API docs: a
# malformed id comes back as HTTP 200 and a feed, not as an HTTP error.
API_ERROR_MARK = "/api/errors"


class SourceError(ConfigError):
    """This reference does not name a paper we can fetch.

    A ConfigError, and not a class of its own, because that is what the CLI
    already raises for a Source it was handed and cannot use (`no such paper`):
    it is in `paper_cast.RUN_ERRORS`, so it reaches the user as a message and
    never as a traceback.
    """


# --- reading a reference ----------------------------------------------------

# arXiv has had two id schemes and both are still live. New style is YYMM.NNNNN,
# with four digits before 2015 and five after; old style, retired in March 2007,
# is an archive and optional subject class before a seven-digit number. Either
# may carry a version suffix, and a paste from the site usually does.
NEW_STYLE = r"\d{4}\.\d{4,5}"
OLD_STYLE = r"[a-z][a-z-]+(?:\.[A-Za-z]{2})?/\d{7}"
VERSION = r"(?:v\d+)?"
BARE_ID = re.compile(rf"(?:arxiv:)?((?:{NEW_STYLE}|{OLD_STYLE}){VERSION})\Z", re.IGNORECASE)

# The archive part is matched by shape rather than against the list of real
# archives. The list is long, closed and easy to get one entry wrong in, and
# arXiv itself is a better judge of whether `chao-dyn/9401001` exists than a
# tuple in this file is: a shape that parses but names nothing comes back from
# the API as "no entry", which is the same clear failure by a shorter route.

DOI = re.compile(r"(?:doi:)?10\.\d{4,9}/\S+\Z", re.IGNORECASE)

# Close enough to an id to have been meant as one. Worth telling apart, because
# "that is not an arXiv id" and "arXiv has never heard of that" send the user to
# two different places, and a mistyped digit is the likelier of the two.
ALMOST_ID = re.compile(
    rf"(?:arxiv:)?(?:\d{{3,6}}\.\d{{1,6}}|[a-z][a-z-]+(?:\.[A-Za-z]{{2}})?/\d{{1,8}}){VERSION}\Z",
    re.IGNORECASE,
)


def normalise(text: str) -> str:
    """Collapse the whitespace out of a value. arXiv wraps its titles at 80 columns."""
    return " ".join((text or "").split())


def arxiv_source(arxiv_id: str, title: str = "") -> dict[str, Any]:
    return {
        "kind": SOURCE_ARXIV,
        "id": arxiv_id,
        "title": title,
        "url": ARXIV_ABS + arxiv_id,
        "path": None,
    }


def pdf_source(path: Path) -> dict[str, Any]:
    """A paper already on disk. Stored absolute: a Job outlives the directory it
    was queued from, and the runner has no idea what that was."""
    return {"kind": SOURCE_PDF, "id": None, "title": "", "url": None, "path": str(path.resolve())}


def url_source(url: str) -> dict[str, Any]:
    return {"kind": SOURCE_URL, "id": None, "title": "", "url": url, "path": None}


def arxiv_id_from_path(path: str) -> str:
    """The id out of an `/abs/…` or `/pdf/…` path, or "" if that is not what it is.

    Both schemes appear in both shapes of URL, and the old one has a slash in the
    middle of the id, so the tail is taken whole rather than as one segment.
    """
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2 or parts[0] not in ("abs", "pdf"):
        return ""
    tail = "/".join(parts[1:])
    # `arxiv.org/pdf/1706.03762v7.pdf` is what the site's own download link gives.
    if tail.lower().endswith(".pdf"):
        tail = tail[: -len(".pdf")]
    match = BARE_ID.match(tail)
    return match.group(1) if match else ""


def parse_reference(raw: str) -> dict[str, Any]:
    """Read a reference into a Source, without asking the network anything.

    Nothing here is a guess: a reference either matches one of the accepted
    shapes or it is refused, by name, saying what would have been accepted. The
    alternative — trying a DOI resolver, or fetching a page to look for a PDF
    link in it — is the thing #17 rules out.
    """
    ref = (raw or "").strip()
    if not ref:
        raise SourceError("no reference given; name a PDF, an arXiv id, or an arXiv URL")

    match = BARE_ID.match(ref)
    if match:
        return arxiv_source(match.group(1))
    if "://" in ref:
        return _parse_url(ref)

    # A file on disk is asked about before a scheme-less URL is guessed at, so a
    # directory with a dot in its name is still a path and not a hostname.
    path = Path(ref).expanduser()
    if path.is_file():
        return pdf_source(path)
    if ALMOST_ID.match(ref):
        raise SourceError(
            f"{ref} is not a well-formed arXiv id: a new-style id is four digits, a "
            "dot and four or five more (2401.12345), and an old-style one is an "
            "archive and seven digits (cs.CL/0301001), either with an optional vN"
        )
    if _looks_like_url(ref):
        return _parse_url(ref)
    if path.exists():
        raise SourceError(f"not a file: {ref}")
    if DOI.match(ref):
        raise SourceError(
            f"{ref} is a DOI, and paper-cast does not resolve DOIs (#17). "
            "Give the arXiv id or URL, or download the PDF and name the file."
        )
    raise SourceError(
        f"no such paper, and not an arXiv id: {ref}. "
        "Expected a PDF on disk, an arXiv id such as 2401.12345 or cs.CL/0301001, "
        "an arxiv.org/abs or /pdf URL, or a direct link to a .pdf"
    )


def _looks_like_url(ref: str) -> bool:
    """A scheme-less URL, the way an address bar shows one: `arxiv.org/abs/…`."""
    return bool(re.match(r"(?:[\w-]+\.)+[a-zA-Z]{2,}/", ref))


def _parse_url(ref: str) -> dict[str, Any]:
    # A pasted `arxiv.org/abs/…` has no scheme; the site is https, so assume it.
    url = ref if "://" in ref else "https://" + ref
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SourceError(f"{parts.scheme}: is not a scheme paper-cast fetches; use http or https")
    host = parts.hostname or ""
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        arxiv_id = arxiv_id_from_path(parts.path)
        if not arxiv_id:
            raise SourceError(
                f"{ref} is an arxiv.org URL, but not one naming a paper; "
                "paper-cast reads arxiv.org/abs/<id> and arxiv.org/pdf/<id>"
            )
        return arxiv_source(arxiv_id)
    if host in ("doi.org", "dx.doi.org"):
        raise SourceError(
            f"{ref} is a DOI, and paper-cast does not resolve DOIs (#17). "
            "Give the arXiv id or URL, or download the PDF and name the file."
        )
    if parts.path.lower().endswith(".pdf"):
        # Left unchecked until the runner fetches it: a HEAD here would cost a
        # round trip to a host that may well refuse HEAD, and would still not
        # promise the GET later succeeds. An arXiv id is the case worth
        # validating now (#17), and it is validated now.
        return url_source(url)
    raise SourceError(
        f"{ref} is not a link to a PDF. paper-cast does not read a paper's landing "
        "page to find one (#17) — give the direct .pdf link, or the arXiv id"
    )


# --- asking arXiv what it is ------------------------------------------------


def _open(request: urllib.request.Request, timeout: int) -> Any:
    """The one place this module touches the network; tests replace it."""
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — scheme checked above


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def fetch_feed(arxiv_id: str) -> bytes:
    """One `id_list` query to the arXiv API. Network trouble comes back as a message.

    A traceback out of here would reach the panel as a crash rather than as "the
    box you pasted into is wrong", so every way urllib has of failing is named.
    """
    url = f"{ARXIV_API}?{urllib.parse.urlencode({'id_list': arxiv_id, 'max_results': 1})}"
    try:
        with _open(_request(url), API_TIMEOUT) as response:
            return response.read(MAX_FEED_BYTES)
    except urllib.error.HTTPError as err:
        if err.code == 429:
            raise SourceError(
                "arXiv is rate-limiting this machine (HTTP 429); wait a minute and try again"
            ) from None
        raise SourceError(f"the arXiv API answered HTTP {err.code} for {arxiv_id}") from None
    except (urllib.error.URLError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise SourceError(f"could not reach the arXiv API ({reason}); is the network up?") from None


def entry_of(feed: bytes, arxiv_id: str) -> dict[str, str]:
    """The one entry of an `id_list` feed, as an id and a title.

    `xml.etree.ElementTree` is the stdlib parser and the one the house rules
    allow. It never fetches an external entity or a DTD, and the expat it is
    built on has capped entity amplification by default since 2.4 — so the
    billion-laughs shape is not open either. What is left is sheer size, and the
    caller has already read at most MAX_FEED_BYTES.
    """
    try:
        feed_root = ElementTree.fromstring(feed)
    except ElementTree.ParseError as err:
        raise SourceError(f"the arXiv API did not answer with a feed ({err})") from None

    entry = feed_root.find(f"{ATOM}entry")
    if entry is None:
        # A well-formed id that names nothing: the feed comes back with no entries.
        raise SourceError(f"arXiv has no entry {arxiv_id}")

    entry_id = normalise(_text(entry, "id"))
    if API_ERROR_MARK in entry_id:
        # A malformed id is HTTP 200 and an error entry, so the summary is the
        # only thing that says what arXiv objected to.
        raise SourceError(f"arXiv rejected {arxiv_id}: {normalise(_text(entry, 'summary'))}")

    resolved = entry_id.split("/abs/", 1)[-1] if "/abs/" in entry_id else arxiv_id
    # The title is what this ticket is for: `pdfinfo` reads no Title at all out of
    # *Attention Is All You Need* (#2), so arXiv's is the only good one there is.
    # Empty would be a broken feed; the id at least names the paper.
    return {"id": resolved, "title": normalise(_text(entry, "title")) or resolved}


def _text(entry: ElementTree.Element, tag: str) -> str:
    found = entry.find(ATOM + tag)
    return "" if found is None or found.text is None else found.text


def resolve(raw: str) -> dict[str, Any]:
    """A reference in, a Source out — the arXiv one having been looked up.

    This is `paper-cast resolve`, and it is what the panel calls before it stages
    anything (#17). The Source it returns is the Job file's Source entry, whole.
    """
    source = parse_reference(raw)
    if source["kind"] != SOURCE_ARXIV:
        return source
    entry = entry_of(fetch_feed(source["id"]), source["id"])
    # The version arXiv resolved to is kept, not the one that was asked for: the
    # title we just recorded describes that version, and so will the PDF.
    return arxiv_source(entry["id"], entry["title"])


# --- putting the paper on disk ----------------------------------------------


def pdf_url(source: dict[str, Any]) -> str:
    """Where the bytes are, for a Source that has to be fetched; "" for one on disk.

    arXiv's own API gives exactly this URL for the entry, version and all.
    """
    if source.get("kind") == SOURCE_ARXIV and source.get("id"):
        return ARXIV_PDF + str(source["id"])
    if source.get("kind") == SOURCE_URL:
        return str(source.get("url") or "")
    return ""


def download_name(source: dict[str, Any]) -> str:
    """What a fetched Source is filed under, inside the Run directory.

    The Source's own name, slugified the way the Run directory is: a paper is
    then `attention-is-all-you-need.pdf` beside the Episode it produced, and —
    the part that matters — a Source that nothing has titled is filed under a
    stem that slugifies back to the directory's own name, so the pipeline and
    this module go on agreeing about where the run is even then.

    Nothing disambiguates: a Run directory is overwritten when the same Source is
    run again (CONTEXT.md), and two Sources of one Job that come out with the same
    name are the same paper under every name we have for them.
    """
    # The last path segment without its `.pdf`, rather than `Path(...).stem`:
    # the stem of "2401.12345" is "2401", so two untitled papers from the same
    # month would both be filed as `2401.pdf` and the second would overwrite the
    # first — one Job, two Sources, one paper actually discussed.
    name = urllib.parse.urlsplit(pdf_url(source)).path.rsplit("/", 1)[-1]
    fallback = name[:-4] if name.lower().endswith(".pdf") else name
    return pc.slugify(source.get("title") or "") or pc.slugify(fallback) or pc.FALLBACK_SLUG


def primary_stem(primary: dict[str, Any]) -> str:
    """The filename `resolve_title` and `run_slug` will fall back to for this Job."""
    return Path(primary["path"]).stem if primary.get("path") else download_name(primary)


def primary_metadata(job: dict[str, Any], primary: dict[str, Any]) -> str:
    """`pdfinfo` on the first Source, asked only when its answer can still matter.

    It matters in exactly one case: a combined Job whose first Source is a paper
    already on disk that nothing has named, with a Source behind it that has to be
    fetched. The pipeline asks the same question a moment later and has to get the
    same answer, so it is asked the same way, through the same `run_step`.

    Skipped when `pdfinfo` is not installed: `run_pipeline` checks for that itself
    and fails the Job before a title is used for anything, so the two cannot
    disagree over a tool neither of them has.
    """
    if job.get("title") or primary.get("title") or not primary.get("path"):
        return ""
    if shutil.which("pdfinfo") is None:
        return ""
    return pc.run_step("pdfinfo", ["pdfinfo", str(primary["path"])])


def episode_directory(job: dict[str, Any], config: dict[str, Any]) -> Path | None:
    """The Run directory this Job is about to use, named before anything is fetched.

    `None` when every Source of the Job is already a file on disk: nothing has to
    be placed, so the pipeline names the run on its own exactly as it always has,
    off `pdfinfo` and the filename.

    Otherwise the name is worked out here from the same three things `run_pipeline`
    will use — `resolve_title`, `run_slug` and the first Source — and fed the same
    inputs, so the paper is downloaded into the directory the Episode is about to
    claim rather than into a sibling of it (#17). The one thing this module gets to
    choose, `download_name`, is chosen to keep that true when nothing has titled
    the Job at all.
    """
    sources = job.get("sources") or []
    if not any(pdf_url(source) for source in sources):
        return None
    primary = sources[0]
    stem = Path(primary_stem(primary))
    title = pc.resolve_title(
        job.get("title") or None,
        primary_metadata(job, primary),
        stem,
        extra=len(sources) - 1,
        source_title=primary.get("title") or "",
    )
    return pc.run_directory(title, stem, config)


def materialise(source: dict[str, Any], run_dir: Path | None) -> dict[str, Any]:
    """Make sure this Source is a paper on disk, and say where it is.

    The one entry point the runner uses (#17), called from one line of
    `queue_cli.source_paths`. A paper already on disk passes straight through —
    including one this Job downloaded on an earlier attempt, because a retry must
    never redo work that succeeded (#13). An arXiv entry or a PDF link is
    downloaded into the Run directory, so the Source sits beside the Episode it
    produced.

    The Source is updated in place as well as returned, so the next write of the
    Job file records where the paper landed, and the retry above has something to
    find.
    """
    url = pdf_url(source)
    if not url:
        # Nothing to fetch: this is a local paper, or a Job whose Source is broken.
        # Either way the caller is the one that checks there is a path, because the
        # stage a retry resumes at may not need the file at all.
        return source

    existing = Path(source["path"]) if source.get("path") else None
    if existing is not None and existing.is_file() and existing.stat().st_size > 0:
        return source
    if run_dir is None:
        raise PipelineError(f"there is nowhere to download {url} to")

    run_dir.mkdir(parents=True, exist_ok=True)
    destination = run_dir / f"{download_name(source)}.pdf"
    pc.say(f"Fetching {url}\n  {destination}")
    download(url, destination)
    source.update(path=str(destination))
    return source


def download(url: str, destination: Path) -> Path:
    """Fetch a PDF to a path, and refuse to call anything else a PDF.

    Written beside itself and renamed over, like every other file this program
    writes: a download killed half way must not leave something the retry logic
    would mistake for a paper it already has.
    """
    partial = destination.with_name(destination.name + ".part")
    try:
        with _open(_request(url), DOWNLOAD_TIMEOUT) as response, partial.open("wb") as handle:
            first = response.read(CHUNK)
            if not first.startswith(b"%PDF"):
                # arXiv answers a request for a paper it is still rendering with an
                # HTML holding page, and a mistyped link answers with somebody's
                # home page. Both are HTTP 200 and neither is a paper.
                raise PipelineError(
                    f"{url} did not return a PDF "
                    f"(it starts {first[:16]!r}), so there is nothing to cast"
                )
            written = 0
            while first:
                written += len(first)
                if written > MAX_PDF_BYTES:
                    raise PipelineError(
                        f"{url} is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB; "
                        "that is not a paper, so the download was stopped"
                    )
                handle.write(first)
                first = response.read(CHUNK)
    except urllib.error.HTTPError as err:
        partial.unlink(missing_ok=True)
        raise PipelineError(f"{url} answered HTTP {err.code}, so the paper could not be fetched") from None
    except (urllib.error.URLError, OSError) as err:
        partial.unlink(missing_ok=True)
        reason = getattr(err, "reason", err)
        raise PipelineError(f"could not download {url}: {reason}") from None
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)
    return destination
