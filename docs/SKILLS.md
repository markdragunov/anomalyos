# Future Skills

A Skill is a reproducible **procedure** (how to run something that exists), not a set of constraints: constraints live
in `AGENTS.md` and `.claude/rules/`. Conventions are in `.claude/skills/README.md`.

Do **not** install marketplace or external Skills. Do **not** add empty or placeholder Skills: Claude Code lists every
Skill it finds, so a stub costs context and promises a procedure that does not exist (ADR-004). An architecture test
fails on a Skill directory without a complete `SKILL.md`.

When a Skill is introduced, it lives at:

```
.claude/skills/<name>/SKILL.md
```

with YAML frontmatter `name` (equal to the directory name) and `description`; optional `scripts/`, `references/`,
`assets/` next to it.

## Planned Skills

| Skill | Path | Create when | What it should teach |
| --- | --- | --- | --- |
| **payment-domain** | `.claude/skills/payment-domain/SKILL.md` | A first complete payment-domain implementation exists | Vocabulary, epistemic labelling of payment metrics, what *not* to treat as an incident |
| **clickhouse** | `.claude/skills/clickhouse/SKILL.md` | Approved analytical interfaces are established | How to extend typed interfaces, versioned metric definitions, migrations, never exposing raw SQL to AI |
| **jev** | `.claude/skills/jev/SKILL.md` | Jev is implemented | Purity rules, question sets, replay, how to add a question without side effects or tools |
| **incident-investigation** | `.claude/skills/incident-investigation/SKILL.md` | The Investigation Agent exists | Read-only V2 loop, evidence IDs, budgets, forbidden tools |
| **evaluation** | `.claude/skills/evaluation/SKILL.md` | A full evaluation benchmark is implemented | How to add a scenario with ground truth, run DEV / HELDOUT evaluations, read the reports |
| **architecture-review** | `.claude/skills/architecture-review/SKILL.md` | A repeatable architecture-review process is needed | How to run the architecture checks, update invariants and record ADRs |

Do not create a Skill before its condition holds. Until then, use the matching documents (`docs/INVARIANTS.md`,
`docs/ARCHITECTURE.md`, `docs/TESTING.md`, `docs/METRICS.md`, `docs/DETECTION.md`).
