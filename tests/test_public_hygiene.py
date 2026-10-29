"""Public-hygiene test: the repository must read as a stranger's clone, with nothing private in it.

Scans every text file of the checkout (docs and fixtures included) for real home paths, internal
Amazon hosts, 12-digit AWS account ids, e-mail addresses other than the Amazon Open Source Code of
Conduct contact, AI-agent configuration files and AI commit trailers.

A maintainer can add private names (a customer, a colleague, a host) to an untracked
``.private-names`` file at the repository root: one regular expression per line, ``#`` starts a
comment. That file is git-ignored, because listing the names in the repository would publish them.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIPPED_DIRS = {".git", ".state", ".build", "evidence", "__pycache__", ".ruff_cache", ".venv", "venv"}
SKIPPED_FILES = {".private-names"}

# The rules themselves match their own examples, so this file is not scanned.
SELF = Path(__file__).resolve()

RULES = {
    "real home path": re.compile(r"/(?:home|Users)/(?!me\b|user\b|<)[A-Za-z][\w.-]*/"),
    "internal Amazon host": re.compile(
        r"(?:quip-amazon|w\.amazon|issues\.amazon|corp\.amazon|a2z\.com|gitlab\.aws\.dev|"
        r"\.people\.aws\.dev|\.aws\.dev\b|midway|brazil\b|apollo\b)",
        re.I,
    ),
    "12-digit account id": re.compile(r"(?<![\w.-])\d{12}(?![\w.-])"),
    "e-mail address": re.compile(r"[\w.+-]+@(?!amazon\.com\b|example\.com\b)[\w-]+\.[\w.-]+"),
    "AI attribution": re.compile(r"Co-Authored-By:\s*Claude|Generated with \[?Claude", re.I),
}

# Files that must not exist in the repository.
FORBIDDEN_FILES = ("CLAUDE.md", "AGENTS.md", ".claude")


def _files() -> list[Path]:
    """Return the files a clone would contain: tracked plus untracked-but-not-ignored (git), else a directory walk."""
    try:
        listing = subprocess.run(  # noqa: S603  fixed git command, no user input
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607  git from PATH
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        candidates = [ROOT / name for name in listing]
    except (OSError, subprocess.CalledProcessError):
        candidates = sorted(ROOT.rglob("*"))
    out: list[Path] = []
    for path in candidates:
        rel = path.relative_to(ROOT)
        if any(part in SKIPPED_DIRS for part in rel.parts) or path.name in SKIPPED_FILES:
            continue
        if path.is_file() and path != SELF:
            out.append(path)
    return out


def _hits(rule: re.Pattern[str]) -> list[str]:
    found = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if rule.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:100]}")
    return found


def private_names(path: Path) -> re.Pattern[str] | None:
    """Return the maintainer's names from ``path`` as one case-insensitive pattern, or None without such a file."""
    if not path.exists():
        return None
    parts = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    parts = [p for p in parts if p and not p.startswith("#")]
    return re.compile("|".join(f"(?:{p})" for p in parts), re.I) if parts else None


class PublicHygieneTest(unittest.TestCase):
    """The repository names nothing a stranger should not see."""

    def test_generic_rules(self) -> None:
        """Each generic rule has no hit in any tracked-looking file."""
        for name, rule in RULES.items():
            with self.subTest(rule=name):
                self.assertEqual(_hits(rule), [])

    def test_no_agent_configuration_files(self) -> None:
        """AI-agent configuration files are absent."""
        for name in FORBIDDEN_FILES:
            with self.subTest(file=name):
                self.assertFalse((ROOT / name).exists())

    def test_private_names(self) -> None:
        """None of the maintainer's private names appears (skipped without a .private-names file)."""
        names = private_names(ROOT / ".private-names")
        if names is None:
            self.skipTest("no .private-names file")
        self.assertEqual(_hits(names), [])

    def test_rules_match_their_examples(self) -> None:
        """The rules catch what they are meant to catch and leave neutral text alone."""
        self.assertTrue(RULES["real home path"].search("/home/alice/work/x"))
        self.assertFalse(RULES["real home path"].search("/home/me/work /Users/me/x"))
        self.assertTrue(RULES["12-digit account id"].search("account 123456789012 here"))
        self.assertFalse(RULES["12-digit account id"].search("id 12345678901234"))
        self.assertTrue(RULES["e-mail address"].search("write to someone@gmail.com"))
        self.assertFalse(RULES["e-mail address"].search("opensource-codeofconduct@amazon.com"))


if __name__ == "__main__":
    unittest.main()
