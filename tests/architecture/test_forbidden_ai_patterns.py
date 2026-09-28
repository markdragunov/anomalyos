"""Static constraints: no arbitrary SQL tools, no LLM event firehoses, no secrets."""

from __future__ import annotations

import re
import unittest

from tests.architecture.paths import ROOT

CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".sql"}

# These are meaningful now: they fail if someone adds an AI→SQL escape hatch
# or an event firehose into model context before approved interfaces exist.
# Adjacent literals so this file does not match its own patterns.
FORBIDDEN_CODE_PATTERNS = [
    (
        "INV-002",
        re.compile(
            r"generate" r"_sql|"
            r"arbitrary" r"_sql|"
            r"run" r"_sql\(|"
            r"execute" r"_sql\(|"
            r"text" r"_to_sql",
            re.I,
        ),
    ),
    (
        "INV-001",
        re.compile(
            r"raw" r"_event_stream|"
            r"unbounded" r"_events|"
            r"events" r"_to_llm|"
            r"prompt.*" r"raw.?" r"events",
            re.I,
        ),
    ),
    (
        "INV-012",
        re.compile(
            r"auto" r"_refund|"
            r"autonomous" r"_retry|"
            r"disable" r"_processor\(",
            re.I,
        ),
    ),
]

SECRET_BASENAMES = {".env", "credentials.json", "id_rsa", "id_ed25519"}


def iter_code_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "evals/output" in path.parts:
            continue
        if path.suffix in CODE_SUFFIXES:
            yield path


class TestForbiddenAiPatterns(unittest.TestCase):
    def test_no_forbidden_ai_escape_hatches(self) -> None:
        hits: list[str] = []
        for path in iter_code_files():
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT).as_posix()
            for invariant, pattern in FORBIDDEN_CODE_PATTERNS:
                if pattern.search(text):
                    hits.append(f"{invariant}: {rel}")
        self.assertEqual(hits, [], f"forbidden AI/runtime patterns: {hits}")

    def test_gitignore_covers_dotenv(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore)
        self.assertIn(".env.*", gitignore)

    def test_no_committed_secret_filenames(self) -> None:
        hits = [
            path.relative_to(ROOT).as_posix()
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and path.name in SECRET_BASENAMES
        ]
        self.assertEqual(hits, [], f"secret-like files must not be committed: {hits}")
