# Project Skills

A Skill is a **reproducible procedure** — "how to do X in this repository, step by step" — not a set of general
instructions. Constraints live in `AGENTS.md` and `.claude/rules/`; Skills encode procedures that already exist.

Conventions:

- Create a Skill only when the procedure it encodes already exists and has been run by hand at least once
  (conditions per Skill: `docs/SKILLS.md`).
- One directory per Skill: `.claude/skills/<name>/SKILL.md`, with YAML frontmatter containing `name` (equal to the
  directory name) and `description` (when to use it).
- Optional material next to it: `scripts/`, `references/`, `assets/`.
- Every step references current project documents and commands (`AGENTS.md`, `docs/`, `scripts/`); no copied
  contract text.
- No empty or placeholder Skills; no marketplace or external Skills. An architecture test enforces this.

There are no Skills yet.
