#!/usr/bin/env python3
"""`paper-cast config get|set|list` — read and write ~/.config/paper-cast/config.toml.

The bar panel never writes the file (#10): it shells out to `config set`, so
`paper_cast.parse_config` stays the single validator of everything that lands on
disk, and the panel cannot invent a config the CLI would reject.

`tomllib` reads TOML and does not write it, so the serialiser below is ours. It
exists for one value in particular — a Steering preset's text, which is prose
with newlines and quotes in it, and which has to come back byte for byte.

Stdlib only, per the standing decisions on the map.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from paper_cast import CONFIG_FILE, DEFAULTS, ConfigError, load_config, parse_config

CONFIG_HEADER = """\
# paper-cast. Written by `paper-cast config set`, which writes TOML and not your
# comments; config.example.toml in the repo is the documented shape.
"""


# --- writing TOML -----------------------------------------------------------

# The escapes TOML spells short. Everything else a config holds is printable.
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape_char(character: str) -> str:
    if character in _ESCAPES:
        return _ESCAPES[character]
    # A raw control character is not legal in a TOML string, and \u is the only
    # way to carry one through. Everything else — accents, emoji — goes as it is.
    if character < " " or character == "\x7f":
        return f"\\u{ord(character):04x}"
    return character


def _multiline_body(value: str) -> str:
    """The inside of a `\"\"\"` string: newlines and tabs raw, the rest escaped."""
    # One or two quotes in a row are legal anywhere inside the form, which keeps a
    # Steering that quotes someone readable. Three would close the string early,
    # and so would a quote sitting against the closing delimiter.
    risky = {index for match in re.finditer(r'"{3,}', value) for index in range(*match.span())}
    if value.endswith('"'):
        risky.add(len(value) - 1)
    characters = []
    for index, character in enumerate(value):
        # A newline straight after the opening delimiter is eaten by the reader,
        # so a text that starts with one is the single newline that gets escaped.
        raw_newline = character == "\n" and index > 0
        raw_quote = character == '"' and index not in risky
        characters.append(character if raw_newline or raw_quote or character == "\t" else _escape_char(character))
    return "".join(characters)


def toml_string(value: str) -> str:
    """A string as TOML, such that tomllib reads back exactly what went in."""
    # Prose with newlines in it goes in the `\"\"\"` form because a Steering is meant
    # to be read and edited in the file, not squinted at as one escaped line.
    if "\n" in value:
        return '"""' + _multiline_body(value) + '"""'
    return '"' + "".join(_escape_char(character) for character in value) + '"'


def toml_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        # parse_config hands back an expanded path; the `~` is written back so a
        # `config set` does not freeze this machine's home directory into the file.
        return toml_string(collapse_home(value))
    if isinstance(value, str):
        return toml_string(value)
    raise ConfigError(f"cannot write {key} = {value!r}: a config holds strings and booleans")


def collapse_home(path: Path) -> str:
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def dump_config(config: dict[str, Any]) -> str:
    """The whole config as TOML: flat keys in DEFAULTS order, then the presets.

    Every key is written, not only the ones that differ from their default. The
    file is the source of truth for an Episode (#10) and the panel edits it, so a
    complete picture beats a terse one. Anything the caller left out is written as
    its default. `[[presets]]` goes last because a table array swallows every key
    written after it.
    """
    lines = [
        f"{key} = {toml_value(key, config.get(key, DEFAULTS[key]))}"
        for key in DEFAULTS
        if key != "presets"
    ]
    for index, preset in enumerate(config.get("presets", DEFAULTS["presets"])):
        where = f"presets[{index}]"
        if not isinstance(preset, dict):
            raise ConfigError(f"cannot write {where} = {preset!r}: a Steering preset is a table")
        # An entry is written with whatever fields it arrived with, rather than
        # being tidied up here: parse_config is what judges it, as everywhere else.
        lines.append("\n[[presets]]")
        lines += [f"{field} = {toml_value(f'{where}.{field}', text)}" for field, text in preset.items()]
    return "\n".join(lines) + "\n"


def save_config(config: dict[str, Any], path: Path = CONFIG_FILE) -> None:
    """Write the config file whole, and never leave a half-written one behind."""
    text = CONFIG_HEADER + dump_config(config)
    # The strict parser judges what we write as hard as what we read, so a bad
    # `config set` fails naming the key instead of leaving a file that will not load.
    parse_config(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the file and renamed over it: a Job may be reading the config
    # at the moment the panel writes one, and a rename is atomic.
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(text)
    temporary.replace(path)


# --- what Setup writes ------------------------------------------------------

# The starter Steering presets. Setup writes these into the user's own
# config.toml (#12 calls write_fresh_config below) rather than paper-cast keeping
# them as a hidden fallback: the user owns the file, and a fallback would mean
# editing a preset never stuck. Five, because a carousel of five is flipped
# through and a carousel of twenty is searched.
STARTER_PRESETS: tuple[dict[str, str], ...] = (
    {
        "name": "ML researcher",
        "text": (
            "You are talking to a machine-learning researcher who reads papers every week. "
            "Skip the background: no explaining what a transformer, a learning rate or an "
            "embedding is. Spend the time on the method, the training setup and the ablations, "
            "name the baselines, and say where the comparison flatters the paper."
        ),
    },
    {
        "name": "Skim it",
        "text": (
            "Five minutes, not twenty. What problem, what idea, what result, and whether it is "
            "worth reading in full. No history of the field, no motivation section, no worked "
            "examples — and say so plainly if the answer is that it is not worth it."
        ),
    },
    {
        "name": "Critical read",
        "text": (
            "Read it like a reviewer looking for the reason to reject. Where is the evidence "
            "thin, which claim outruns the experiment that supports it, what is missing from the "
            "ablations, and which baseline is doing the real work? End with what you would ask "
            "the authors for."
        ),
    },
    {
        "name": "Teach me",
        "text": (
            "I am new to this area. Build up from the problem the paper is trying to solve, "
            "define each term as it arrives, and carry one concrete example all the way through. "
            "Take the time — saying the important idea a second way is better than covering "
            "everything once."
        ),
    },
    {
        "name": "Adjacent field",
        "text": (
            "I do research, and not in this field. Assume the maths is fine and the field's own "
            "vocabulary and landmark results are not. Translate the jargon the first time it "
            "appears, say what is standard practice here, and be clear about which part of this "
            "paper is the new one."
        ),
    },
)


def write_fresh_config(path: Path = CONFIG_FILE) -> bool:
    """Write a config holding the defaults and the starter presets — Setup's seam.

    Idempotent, as Setup is: an existing file is left exactly as it stands, because
    a re-run must never overwrite a Steering somebody wrote. True when it wrote one.
    """
    if path.exists():
        return False
    loaded = STARTER_PRESETS[0]
    save_config(
        parse_config("")
        | {
            "presets": [dict(preset) for preset in STARTER_PRESETS],
            # The first preset is the loaded one, so a fresh box has a Steering
            # rather than hosts talking to nobody in particular.
            "steering_preset": loaded["name"],
            "focus": loaded["text"],
        },
        path,
    )
    return True


# --- the subcommand ---------------------------------------------------------


def known_key(key: str) -> str:
    if key not in DEFAULTS:
        raise ConfigError(
            f"unknown config key {key!r}; paper-cast knows {', '.join(sorted(DEFAULTS))}"
        )
    return key


def value_from_text(key: str, text: str) -> Any:
    """Turn what the panel or the shell handed us into what the key holds.

    A string is taken exactly as given, newlines and quotes and all: that is the
    whole of why a Steering survives the round trip.
    """
    default = DEFAULTS[known_key(key)]
    if isinstance(default, bool):
        if text not in ("true", "false"):
            raise ConfigError(f"{key} must be true or false, not {text!r}")
        return text == "true"
    if isinstance(default, list):
        # The presets are structured, so they arrive as JSON — one
        # `[{"name": ..., "text": ...}]` a panel can build without quoting TOML.
        try:
            entries = json.loads(text)
        except json.JSONDecodeError as err:
            raise ConfigError(f"{key} must be a JSON array of {{name, text}}: {err}") from None
        if not isinstance(entries, list):
            raise ConfigError(f"{key} must be a JSON array of {{name, text}}, not {text!r}")
        return entries
    return text


def jsonable(value: Any) -> Any:
    return str(value) if isinstance(value, Path) else value


def plain(value: Any) -> str:
    """One value for a shell: `--focus "$(paper-cast config get focus)"` is the Steering."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(jsonable(value), ensure_ascii=False)
    # A path prints expanded, because that is the one a shell can cd into.
    return str(value)


def read_stdin() -> str:
    """The value byte for byte, minus the one trailing newline a shell adds.

    `echo` ends with a newline and nobody means it as part of their Steering;
    `printf` is there for whoever does.
    """
    text = sys.stdin.read()
    return text[:-1] if text.endswith("\n") else text


def get_command(args: argparse.Namespace) -> int:
    value = load_config(CONFIG_FILE)[known_key(args.key)]
    print(json.dumps(jsonable(value), ensure_ascii=False) if args.as_json else plain(value))
    return 0


def list_command(args: argparse.Namespace) -> int:
    config = load_config(CONFIG_FILE)
    if args.as_json:
        print(json.dumps({key: jsonable(config[key]) for key in DEFAULTS}, ensure_ascii=False, indent=2))
    else:
        # The file's own form, `~` and all: `config list` answers "what does
        # config.toml say", including on a box that has no config.toml yet.
        print(dump_config(config), end="")
    return 0


def set_command(args: argparse.Namespace) -> int:
    if args.stdin == (args.value is not None):
        raise ConfigError("config set takes a value, or --stdin, and not both")
    config = load_config(CONFIG_FILE)
    config[args.key] = value_from_text(args.key, read_stdin() if args.stdin else args.value)
    if args.key == "steering_preset":
        config["focus"] = loaded_text(config)
    save_config(config, CONFIG_FILE)
    return 0


def loaded_text(config: dict[str, Any]) -> str:
    """The Steering that naming a preset loads: its text.

    Naming a preset and leaving `focus` as it was would steer a Job with one
    preset while the panel showed another, and `focus` is the one that reaches the
    hosts. Naming nothing detaches the Steering from any preset and keeps it —
    a hand-written one is not thrown away for having no name.
    """
    for preset in config["presets"]:
        if preset["name"] == config["steering_preset"]:
            return preset["text"]
    # A name matching no preset is left for parse_config to reject, by name.
    return config["focus"]


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("config", help="read and write the config file")
    actions = parser.add_subparsers(dest="action", metavar="<action>")

    def usage(_args: argparse.Namespace) -> int:
        parser.print_help()
        return 2

    parser.set_defaults(handler=usage)

    getter = actions.add_parser("get", help="print one key")
    getter.add_argument("key", help="the key to print")
    getter.add_argument("--json", action="store_true", dest="as_json", help="print it as JSON")
    getter.set_defaults(handler=get_command)

    listing = actions.add_parser("list", help="print the whole config, defaults included")
    listing.add_argument("--json", action="store_true", dest="as_json", help="print it as JSON")
    listing.set_defaults(handler=list_command)

    setter = actions.add_parser("set", help="write one key")
    setter.add_argument("key", help="the key to write")
    setter.add_argument("value", nargs="?", help="the value; a Steering goes in as it stands")
    setter.add_argument(
        "--stdin",
        action="store_true",
        help="read the value from stdin instead, for text an argv would mangle",
    )
    setter.set_defaults(handler=set_command)
