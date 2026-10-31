---
name: taiwan-stock-research
description: Resume Taiwan Stock Lab development or research using bounded local context, reproducible Python jobs and explicit data-quality evidence.
---

# Taiwan Stock Lab workflow

Use the existing checkout. Do not create worktrees unless explicitly requested.
Read AGENTS.md, docs/STATUS.md and docs/VERIFICATION.md, then run:

```bash
.venv/bin/python -m stocklab context
```

Read the short summary before fetching raw data or old job exports. Inspect only relevant modules and tests.
Keep downloaded histories, computations and scheduled work in Python; do not implement per-bar/per-symbol LLM calls.

## Research

- Identify dataset source, fingerprint, asset type, price basis, date range and quality limitations first.
- Use the local API job queue for repeated tasks. Fetch bounded /api/jobs/{id}/summary for initial inspection.
- Read full trade logs or price rows only when needed to diagnose a concrete issue.
- Preserve next-open execution, chronological selection and holdout boundaries, costs and immutable data versions.
- Correlation is exploratory. Do not infer causal signals or trade foreign-market data before it was available.
- Reject futures/indexes in the cash-equity engine. Futures need contract, session, multiplier, margin and roll handling.
- Do not call synthetic fixtures market evidence. Unadjusted prices are not total returns.
- A provider's API documentation is not evidence of licensed complete histories or successful access.

## Development and continuation

Run meaningful regression tests after engine, data or API changes. Bump ENGINE_VERSION when calculation semantics change,
so cached computations cannot be mistaken for newly calculated results. Update docs/STATUS.md with validated changes and blockers.
Do not duplicate raw histories into chat, Git, Sheets or cloud document connectors.
Source/data completeness and correctness take priority over context reduction; inspect necessary evidence when a result is uncertain.

The installed app starts with bash scripts/start.sh on one persistent Linux host, with a single worker.
The development cloud is not an always-on production guarantee. Cross-computer production access still requires authorized hosting,
HTTPS, access control, off-host backup and end-to-end validation. Saved startup instructions do not prove those steps happened.
