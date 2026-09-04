"""Small shared RU/EN message catalog; no machine-locale dependencies."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MESSAGES = json.loads((ROOT / "messages.json").read_text(encoding="utf-8"))
LANGUAGE = "en"


def tr(key, *values):
    return MESSAGES[key][LANGUAGE] % values


def select(argv):
    """Allow --lang before/after a subcommand; persist only during setup."""
    global LANGUAGE
    saved = ROOT / "language"
    language = os.environ.get("CI_RUNNER_LANG") or (saved.read_text().strip() if saved.is_file() else "en")
    remaining = []
    iterator = iter(argv)
    for arg in iterator:
        if arg == "--lang":
            language = next(iterator, "")
        elif arg.startswith("--lang="):
            language = arg.split("=", 1)[1]
        else:
            remaining.append(arg)
    if language not in ("ru", "en"):
        raise ValueError(tr("language_invalid"))
    LANGUAGE = language
    return remaining


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.update(add_help=False, allow_abbrev=False)
        super().__init__(*args, **kwargs)
        self._positionals.title = tr("commands_title")
        self._optionals.title = tr("options_title")
        self.add_argument("-h", "--help", action="help", help=tr("help"))

    def format_usage(self):
        return super().format_usage().replace("usage: ", tr("usage") + ": ", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", tr("usage") + ": ", 1)

    def error(self, message):
        # argparse's internal diagnostics are not localized on stock macOS.
        # Do not echo arbitrary invalid arguments, which might contain a token.
        self.print_usage(sys.stderr)
        self.exit(2, tr("error", tr("arguments_invalid")) + "\n")
