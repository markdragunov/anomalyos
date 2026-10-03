# Tools

Classification of coding-agent and runtime tooling. **Claude Code is the primary development tool** (ADR-036). Do not install every MCP. Do not add a tool because it is convenient; add it when a job in this repo requires it.

| Tool | Class | Reason |
| --- | --- | --- |
| **Claude Code** | **Required** | Primary coding agent. Entry point `CLAUDE.md` (imports `AGENTS.md`); path-scoped rules in `.claude/rules/`; minimal permissions in `.claude/settings.json` (ADR-036). |
| **GitHub** | **Required** | Pull requests, code review and CI (GitHub Actions). The engineering loop is PR → checks → review; the owner merges. |
| **Filesystem** | **Required** | The working surface: agents implement AnomalyOS by reading and writing this tree. There is no other coding surface. |
| **Test runner** | **Required** | `python -m unittest` (architecture), `pytest` (product), plus `scripts/check_architecture.py` and `scripts/eval_smoke.py`. Architecture and eval gates are how invariants stay real. |
| **ClickHouse** | **Required** (product infrastructure) | Analytical infrastructure of the product, not a free-form tool for the agent. Analytical source of truth; local Docker Compose node and `clickhouse-connect` loader exist since Stage 1 (ADR-012, ADR-020; supersedes ADR-008). Agents reach it only through approved typed interfaces — never as a generic SQL MCP. |
| **Playwright** | **Useful later** | Only when an operator UI exists and we must assert real browser behavior. No UI in this harness; adding Playwright now would be a dependency with nothing to drive. |
| **Browser tools** | **Useful later** | Same trigger as Playwright: human-facing surfaces, visual verification, docs sites. Useless against JSON eval fixtures and markdown contracts. |
| **Research tools** (web search, docs fetch) | **Useful later** | Helpful while coding (processor behavior, ClickHouse SQL, payments domain). Not part of the V2 runtime agent. Must never become a path to dump unvetted web text into incident explanations (`INV-008`). |
| **Unrestricted SQL / warehouse MCP** | **Not needed** | Directly violates `INV-002` and `INV-003`. Analytical access is typed interfaces, not a promptable console. |
| **Vector DB / RAG MCP** | **Not needed** | Memory is git docs + future structured incident records (ADR-005). Embeddings would blur observed vs retrieved-as-if-observed. |
| **Production mutation tools** (refund, retry, disable rail) | **Not needed** (V2) | Violates `INV-007` and `INV-012`. Investigation is read-only. |
| **Marketplace / generic agent Skill packs** | **Not used** | The contract is `AGENTS.md` + this repo; project Skills follow `.claude/skills/README.md`. External Skills are not reproducible for clones and are easy to over-permission. |
| **MCP integrations** | **Only on concrete need** | None configured for this repository. Each needs a job, a trust boundary and an ADR; never a generic SQL or warehouse MCP. |
| **Slack/email send from the coding agent** | **Not needed** | Out of band for engineering. Runtime notifications, if any, will be explicit product components with audit, not chat plugins. |

When a tool is promoted from “useful later” to “required”, add an ADR in `docs/DECISIONS.md` stating the job, the trust boundary, and which invariants it must not weaken.
