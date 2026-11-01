# STOCK / Taiwan Stock Lab

- Personal Taiwan-market research app. User-facing text is Traditional Chinese.
- User requires free hosting/data/API paths (2026-11-01). Do not provision paid services or repeat monthly-fee approval questions. Prefer their own computer for durable research; the default Render deployment is free and ephemeral.
- Read README.md and docs/STATUS.md first; run `.venv/bin/python -m stocklab context` for a bounded summary of local data and jobs. Do not read entire price datasets or old job output into chat unless needed.
- For chat/PWA work read docs/CHAT_APP.md. Never execute unrecognized text constraints, rank with held-out outcomes, or put private API responses in offline caches. Do not load complete user chat histories by default.
- Use the existing checkout. Cloud tasks are isolated; do not create a worktree unless explicitly requested.
- Run `.venv/bin/python -m pytest -q` for financial-engine/API changes.
- No LLM is called by downloads, indicators, backtests, research or scheduling. Keep computation in Python and persist results, versions and parameters.
- Never present synthetic data as market evidence or correlation as causation. Preserve next-open execution, chronological holdouts and transaction costs. Futures need a separate engine; never run the cash-equity engine on futures.
- Secrets stay in runtime environment / private service settings, never source or results. Keep `.stocklab/` local and ignored. Do not publish datasets or connect trading accounts implicitly.
