#!/usr/bin/env python3
"""`paper-cast resolve <ref>` — say what a reference names, before anything is queued.

The panel's arXiv box calls this and nothing else (#15, #17): paste a reference,
get back the Source the Queue would carry, or exit non-zero with a line to show
in the box. That is what makes a dead id fail while the user is still looking at
it, instead of fifteen minutes later in the runner.

The whole of the work is in `sources`; this file is only the seam that puts it on
the command line.

Stdlib only, per the standing decisions on the map.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sources import resolve


def resolve_command(args: argparse.Namespace) -> int:
    """One JSON object on stdout, and the exit code is the answer.

    A failure comes out of `sources` as a ConfigError and is printed by `main` as
    `error: …` on stderr, which is where a caller that is not a shell will look.
    """
    print(json.dumps(resolve(args.ref), ensure_ascii=False))
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "resolve", help="say what a reference names: a PDF, an arXiv id, a URL"
    )
    parser.add_argument(
        "ref",
        metavar="<ref>",
        help="a PDF on disk, an arXiv id or arxiv.org URL, or a direct link to a .pdf",
    )
    # There is one output shape and it is JSON: this command exists to be read by
    # a program, and the panel is already written against that (#17). The flag is
    # accepted because every other command here spells `--json` to ask for it, and
    # a caller that spells it should not be told it is wrong.
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="the default, spelled out"
    )
    parser.set_defaults(handler=resolve_command)
