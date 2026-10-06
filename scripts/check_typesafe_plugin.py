#!/usr/bin/env python3
"""Check the installed TypeSafe plugin against the version reviewed in ADR-047.

Why: Claude Code settings cannot pin a plugin version, so an update could silently bring new guidance, hooks, MCP
servers or scripts into the coding agent. This script replaces "re-review by hand" with a fingerprint comparison.
Input: the local Claude Code plugin registry (`$CLAUDE_CONFIG_DIR` or `~/.claude`, `plugins/installed_plugins.json`)
and the plugin files it points to.
Output: a report on stdout; exit 0 if the installed plugin is exactly the reviewed one (or is not installed, which
ADR-047 allows), 1 on any difference. `--fingerprint` prints the installed fingerprint for updating `REVIEWED`
after a new review.
Invariants: read-only; stdlib only; no network. Not part of CI: it inspects the developer's machine, not the repo.
Failure modes: an unreadable registry or plugin directory is reported as a difference, never as a pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "typesafe@typesafe-ai"

# Fingerprint of the version reviewed in ADR-047. Update only after reviewing a new version, together with the ADR.
REVIEWED = {
    "version": "0.5.7",
    "commit": "65a39f393687675ce170e6094757de20370365b9",
    "files": {
        ".claude-plugin/marketplace.json": "7040ea575b7549d7db06cce21ebe479471930953eaede01d8cd9ed81511abd11",
        ".claude-plugin/plugin.json": "3d4e3433dc040fde349deaf773a4a4b070b3aa5ce34b3f321b170d2409a9fa5a",
        "LICENSE": "835f233f1d6ed84a9b9a351aba0689b47644a4137d6316911fc7957bde523b02",
        "README.md": "799ce1dc39dc1cb98977f930610bf693a26d1a78e5912b860014447d97390282",
        "skills/typesafe-ai/LICENSE": "835f233f1d6ed84a9b9a351aba0689b47644a4137d6316911fc7957bde523b02",
        "skills/typesafe-ai/SKILL.md": "71ea90d7906c6554c4f4c460ef7361b2d26f59116ccdae986dc6d997b9389f52",
    },
}

# Claude Code bookkeeping inside the install directory, not plugin content.
IGNORED_DIRS = {".git", ".in_use"}
# Components that execute code or widen the agent's reach; ADR-047 allows none of them.
EXECUTABLE_PATHS = ("hooks/", "commands/", "agents/", "bin/", "scripts/", "output-styles/")
EXECUTABLE_FILES = {".mcp.json", ".lsp.json", "hooks.json"}
EXECUTABLE_MANIFEST_KEYS = {"hooks", "mcpServers", "lspServers", "commands", "agents", "outputStyles"}


def fingerprint(plugin_dir: Path) -> dict[str, str]:
    """Map each plugin file (POSIX path relative to `plugin_dir`) to its SHA-256."""
    files = {}
    for path in sorted(plugin_dir.rglob("*")):
        rel = path.relative_to(plugin_dir)
        if path.is_file() and rel.parts[0] not in IGNORED_DIRS:
            files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def executable_components(plugin_dir: Path, files: dict[str, str]) -> list[str]:
    """Name every hook, MCP/LSP server, command, agent, script or executable file in the plugin."""
    found = [f for f in files if f.startswith(EXECUTABLE_PATHS) or Path(f).name in EXECUTABLE_FILES]
    found += [f for f in files if os.access(plugin_dir / f, os.X_OK)]
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        keys = EXECUTABLE_MANIFEST_KEYS & set(json.loads(manifest.read_text(encoding="utf-8")))
        found += [f".claude-plugin/plugin.json: {key}" for key in sorted(keys)]
    return sorted(set(found))


def compare(reviewed: dict, record: dict, files: dict[str, str], executables: list[str]) -> list[str]:
    """Return human-readable differences between the reviewed fingerprint and an installed plugin (empty = match)."""
    problems = []
    if record.get("version") != reviewed["version"]:
        problems.append(f"version {record.get('version')!r}, reviewed {reviewed['version']!r}")
    if record.get("gitCommitSha") != reviewed["commit"]:
        problems.append(f"commit {record.get('gitCommitSha')!r}, reviewed {reviewed['commit']!r}")
    for name in sorted(files.keys() - reviewed["files"].keys()):
        problems.append(f"new file: {name}")
    for name in sorted(reviewed["files"].keys() - files.keys()):
        problems.append(f"missing file: {name}")
    for name in sorted(files.keys() & reviewed["files"].keys()):
        if files[name] != reviewed["files"][name]:
            problems.append(f"changed file: {name}")
    problems += [f"executable component (not allowed by ADR-047): {item}" for item in executables]
    return problems


def installed_record(config_dir: Path) -> dict | None:
    """The registry entry for this project (project scope) or, failing that, the user-scope install."""
    registry = json.loads((config_dir / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    entries = registry.get("plugins", {}).get(PLUGIN_ID, [])
    for scope_matches in (lambda e: e.get("projectPath") == str(ROOT), lambda e: e.get("scope") == "user"):
        for entry in entries:
            if scope_matches(entry):
                return entry
    return None


def main(argv: list[str]) -> int:
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        record = installed_record(config_dir)
    except (OSError, ValueError) as exc:
        print(f"cannot read the plugin registry in {config_dir}: {exc}", file=sys.stderr)
        return 1
    if record is None:
        print(f"{PLUGIN_ID} is not installed for this project; nothing to check (optional, ADR-047)")
        return 0
    plugin_dir = Path(record.get("installPath", ""))
    if not plugin_dir.is_dir():
        print(f"{PLUGIN_ID}: install path {plugin_dir} does not exist", file=sys.stderr)
        return 1
    files = fingerprint(plugin_dir)
    if "--fingerprint" in argv:
        print(json.dumps({"version": record.get("version"), "commit": record.get("gitCommitSha"), "files": files}, indent=4))
        return 0
    problems = compare(REVIEWED, record, files, executable_components(plugin_dir, files))
    if problems:
        print(f"{PLUGIN_ID} differs from the version reviewed in ADR-047 ({REVIEWED['version']}):")
        print("\n".join(f"  - {p}" for p in problems))
        print("Review the new content, then update REVIEWED here and the version in ADR-047 together "
              "(`--fingerprint` prints the installed values). Until then: claude plugin disable typesafe@typesafe-ai")
        return 1
    print(f"{PLUGIN_ID} {REVIEWED['version']} matches the reviewed fingerprint ({len(files)} files, no executables)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
