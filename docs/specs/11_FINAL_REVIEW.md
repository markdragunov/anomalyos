# Final — Critical Product, Architecture and AI Review

> **Status:** after all stages (`STATUS.md`). Run it in a fresh context, preferably with a different model than the one that built the code; an agent grading its own work is not a review.

Review the completed AnomalyOS repository. Do not modify production code initially.

Act as:
1. Staff AI Engineer
2. Senior Billing/Payments Product Manager
3. SRE/Operations reviewer
4. AI evaluation researcher
5. skeptical startup technical reviewer

## 1. Product review

Answer:
- What exact operational problem does AnomalyOS solve?
- Is the core workflow compelling?
- Is this merely observability with an LLM attached?
- What is genuinely differentiated?
- What is commodity?
- Who is the first user?
- What would make them trust it?
- What would make them reject it?

## 2. JevOps architecture review

Compare our implementation to the architectural lessons of JevOps:

- prefilter
- bounded burst ranking
- typed decision fan-out
- policy in code
- IGNORE/DIGEST/INCIDENT routing
- hierarchical drilldown
- bounded work
- verifiable citations
- honest degradation
- separation of decision and explanation

Identify where AnomalyOS copied the pattern correctly and where it diverged intentionally.

Do not copy vendor-specific claims as facts.

## 3. Data/analytics review

Check:
- Stripe-shaped relationships
- metric definitions
- ClickHouse queries
- cohort contribution logic
- baselines
- leakage
- evidence IDs
- reproducibility

## 4. AI review

Identify:
- hallucination risk
- causal inference risk
- calibration risk
- prompt injection risk
- state bloat
- unsupported claims
- premature conclusions
- agent loops
- tool misuse
- model failures
- overreliance on synthetic data

## 5. Decision safety review

Find every place where:
- AI can influence an irreversible action
- low confidence is treated as certainty
- policy is hidden in prompts
- evidence cannot be reconstructed
- model output bypasses verification

## 6. Benchmark review

Check:
- data leakage
- biased scenario generation
- unfair baselines
- insufficient seeds
- cherry-picking
- unrealistic event distributions
- benchmark contamination
- calibration methodology

## 7. UX review

Verify that:
- observed ≠ inferred
- inferred ≠ estimated
- recommendation ≠ action
- evidence is traceable
- investigation trace is understandable

## 8. Final output

Create:

```text
docs/FINAL_REVIEW.md
```

Sections:
1. Executive summary
2. Product assessment
3. Architecture assessment
4. JevOps-pattern assessment
5. AI safety/reliability
6. Benchmark integrity
7. UX assessment
8. Top 10 issues
9. Quick fixes
10. Architectural changes
11. Open research questions
12. What not to build yet
13. Portfolio/demo readiness
