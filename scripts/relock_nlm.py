#!/usr/bin/env python3
"""Regenerate `scripts/nlm-lock.txt`, the hashed lock step 1 installs `nlm` from.

A maintainer's tool, not part of Setup: it is the only thing in the repo that is
allowed to ask an index what exists today. Everything a user runs installs from
the lock this writes, so moving `nlm` forward is two reviewable edits —
`NLM_VERSION` in `setup_cli.py`, and the diff this produces:

    $ python3 scripts/relock_nlm.py            # lock the version setup_cli pins
    $ python3 scripts/relock_nlm.py 0.12.0     # lock a candidate before pinning it

`--universal` resolves for every Python from 3.11 — the package's own floor — so
one lock covers whatever interpreter a machine turns out to have, and
`--generate-hashes` is what makes it a lock rather than a list of versions.

Stdlib only, like the rest; `uv` does the work.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import setup_cli as sc  # noqa: E402

# The package's own requires-python. Resolving from the floor rather than from
# this machine's interpreter is what keeps the lock usable on any of them.
PYTHON_FLOOR = "3.11"


def compile_command(version: str, lock: Path = sc.NLM_LOCK) -> list[str]:
    return [
        "uv", "pip", "compile", "-",
        "--generate-hashes",
        "--universal",
        "--python-version", PYTHON_FLOOR,
        "-o", str(lock),
    ]


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(f"usage: {Path(argv[0]).name} [version]", file=sys.stderr)
        return 2
    version = argv[1] if len(argv) == 2 else sc.NLM_VERSION

    # Stamped into the lock's header in place of uv's own command line, so the
    # file says how to reproduce itself rather than naming a temporary file.
    environment = {
        **os.environ,
        "UV_CUSTOM_COMPILE_COMMAND": f"python3 scripts/relock_nlm.py {version}",
    }
    done = subprocess.run(
        compile_command(version),
        input=f"{sc.NLM_PACKAGE}=={version}\n",
        text=True,
        env=environment,
    )
    if done.returncode != 0:
        return done.returncode

    print(f"Wrote {sc.NLM_LOCK} for {sc.NLM_PACKAGE}=={version}")
    if version != sc.NLM_VERSION:
        print(f"Now set NLM_VERSION = {version!r} in scripts/setup_cli.py; the tests check that")
        print("the lock and the pin agree, and Setup installs whichever the pin names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
