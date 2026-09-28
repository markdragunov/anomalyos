# Future Skills

Do **not** install marketplace or external Skills. Do **not** add empty `.cursor/skills/*/SKILL.md` stubs (Cursor will list them and waste context).

When a Skill is introduced, it lives at:

```
.cursor/skills/<name>/SKILL.md
```

Optional on-demand material: `scripts/`, `references/`, `assets/` under that folder. `name` in frontmatter must match the folder. Skills encode **procedures** (how to run something that exists). Rules encode **constraints**. `AGENTS.md` remains the contract.

## Planned Skills

| Skill | Path | Introduce when | What it should teach |
| --- | --- | --- | --- |
| **payment-domain** | `.cursor/skills/payment-domain/SKILL.md` | First product code that touches auth, capture, refund, decline codes, processors, or rails | Vocabulary, epistemic labeling of payment metrics, what *not* to treat as an incident |
| **clickhouse** | `.cursor/skills/clickhouse/SKILL.md` | ClickHouse is actually provisioned and approved interfaces exist | How to extend typed interfaces, migration discipline, never exposing raw SQL to AI |
| **jev** | `.cursor/skills/jev/SKILL.md` | Jev module exists | Purity rules, input/output records, how to add a signal without adding side effects or tools |
| **incident-investigation** | `.cursor/skills/incident-investigation/SKILL.md` | Investigation agent exists | Read-only V2 loop, evidence IDs, budgets, what tools are forbidden |
| **evaluation** | `.cursor/skills/evaluation/SKILL.md` | First non-smoke eval (baselines captured, scoring defined) | How to add a scenario with ground truth, replay, read traces, interpret cost/latency |
| **architecture-review** | `.cursor/skills/architecture-review/SKILL.md` | Product packages exist and import graphs matter | How to run `scripts/check_architecture.py`, update invariants, record ADRs |

Until then, agents should open the matching **doc** (`docs/INVARIANTS.md`, `docs/ARCHITECTURE.md`, `evals/README.md`) instead of a Skill.
