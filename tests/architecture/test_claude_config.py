"""Claude Code configuration (ADR-036): thin entry point, path-scoped rules, no empty Skills, minimal permissions.

Stdlib only (no YAML parser): the frontmatter format checked here is the small subset the rules use.
"""

from __future__ import annotations

import json
import re
import unittest

from tests.architecture.paths import ROOT
from tests.architecture.test_no_product_implementation import FORBIDDEN_PACKAGES

CLAUDE = ROOT / ".claude"
EXPECTED_RULES = {"architecture.md", "stage-guard.md", "testing.md", "python.md", "clickhouse.md", "ai-safety.md"}


def frontmatter(text: str) -> tuple[dict[str, list[str]], str]:
    """Parse '---\\nkey:\\n  - "glob"\\n---' (lists of quoted strings only). Returns (fields, body)."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    fields: dict[str, list[str]] = {}
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^[A-Za-z_]+:\s*$", line):
            key = line.split(":")[0]
            fields[key] = []
        elif key and (item := re.match(r'^\s+-\s+"([^"]+)"\s*$', line)):
            fields[key].append(item.group(1))
        elif line.strip():
            fields.setdefault("_unparsed", []).append(line)
    return fields, m.group(2)


def _outside_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


class TestNoCursorConfiguration(unittest.TestCase):
    def test_cursor_configuration_is_gone(self) -> None:
        self.assertFalse((ROOT / ".cursor").exists(), ".cursor/ was replaced by .claude/ (ADR-036)")
        self.assertFalse((ROOT / ".cursorrules").exists())

    def test_contract_has_no_cursor_specific_instructions(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)cursor")


class TestClaudeEntryPoint(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    def test_imports_agents_md_with_the_native_import(self) -> None:
        lines = [line.strip() for line in _outside_code(self.text).splitlines()]
        self.assertIn("@AGENTS.md", lines)

    def test_stays_within_the_recommended_size(self) -> None:
        self.assertLessEqual(len(self.text.splitlines()), 200)

    def test_does_not_duplicate_the_contract(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        numbered_rules = [line for line in agents.splitlines() if re.match(r"^\d+\. \*\*", line)]
        self.assertGreaterEqual(len(numbered_rules), 15)
        copied = [rule for rule in numbered_rules if rule in self.text]
        self.assertEqual(copied, [], "CLAUDE.md must import AGENTS.md, not copy its rules")
        self.assertNotIn("## Non-negotiable rules", self.text)


class TestRules(unittest.TestCase):
    def test_the_six_contextual_rules_exist(self) -> None:
        names = {p.name for p in (CLAUDE / "rules").glob("*.md")}
        self.assertEqual(names, EXPECTED_RULES)

    def test_every_rule_is_path_scoped_with_valid_frontmatter(self) -> None:
        for path in sorted((CLAUDE / "rules").rglob("*.md")):
            fields, body = frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual(set(fields), {"paths"}, f"{path.name}: `paths` is the only frontmatter key Claude Code reads")
            self.assertTrue(fields["paths"], f"{path.name}: needs at least one path glob")
            self.assertTrue(body.strip(), f"{path.name}: empty rule")
            self.assertLess(len(body.splitlines()), 60, f"{path.name}: keep rules short; link documents instead")

    def test_rule_globs_point_at_existing_or_planned_locations(self) -> None:
        planned = set(FORBIDDEN_PACKAGES)
        for path in sorted((CLAUDE / "rules").glob("*.md")):
            for glob in frontmatter(path.read_text(encoding="utf-8"))[0]["paths"]:
                base = glob.split("*")[0].rstrip("/")
                target = ROOT / base
                if target.exists():
                    continue
                parts = base.split("/")
                is_planned_package = parts[:2] == ["src", "pulseos"] and len(parts) >= 3 and parts[2] in planned
                self.assertTrue(is_planned_package, f"{path.name}: {glob!r} points at nothing (not even a planned package)")

    def test_rules_do_not_copy_the_contract(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        numbered_rules = [line for line in agents.splitlines() if re.match(r"^\d+\. \*\*", line)]
        for path in sorted((CLAUDE / "rules").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertEqual([r for r in numbered_rules if r in text], [], f"{path.name} copies AGENTS.md")


class TestSkills(unittest.TestCase):
    def test_readme_documents_the_conventions(self) -> None:
        text = (CLAUDE / "skills" / "README.md").read_text(encoding="utf-8")
        for phrase in ("SKILL.md", "name", "description", "marketplace"):
            self.assertIn(phrase, text)

    def test_no_empty_or_incomplete_skills(self) -> None:
        for d in sorted(p for p in (CLAUDE / "skills").iterdir() if p.is_dir()):
            skill = d / "SKILL.md"
            self.assertTrue(skill.is_file(), f"{d.name}: a Skill directory needs SKILL.md (ADR-004)")
            text = skill.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
            self.assertIsNotNone(m, f"{d.name}: SKILL.md needs YAML frontmatter")
            self.assertIn(f"name: {d.name}", m.group(1))
            self.assertRegex(m.group(1), r"description:\s*\S")
            self.assertGreater(len(m.group(2).strip().splitlines()), 5, f"{d.name}: empty Skill")


class TestSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))

    def test_only_minimal_permissions_no_hooks_or_mcp(self) -> None:
        self.assertEqual(set(self.settings), {"permissions"})
        self.assertEqual(set(self.settings["permissions"]), {"allow", "deny"})

    def test_allow_rules_are_exact_commands(self) -> None:
        for rule in self.settings["permissions"]["allow"]:
            self.assertRegex(rule, r"^Bash\([^*]+\)$", f"{rule}: allow rules must be exact (no wildcards; ADR-036)")

    def test_force_push_and_dotenv_are_denied(self) -> None:
        deny = self.settings["permissions"]["deny"]
        for rule in ("Bash(git push --force*)", "Bash(git push -f*)", "Read(./.env)", "Read(./**/.env)"):
            self.assertIn(rule, deny)

    def test_personal_settings_are_not_committed(self) -> None:
        self.assertIn(".claude/settings.local.json", (ROOT / ".gitignore").read_text(encoding="utf-8"))
