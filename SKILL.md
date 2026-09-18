---
name: omnisignal-project
description: Project rules for working on the OmniSignal financial market indicator dashboard. Use when modifying this repository, especially data ingestion, DuckDB storage, chart rendering, Streamlit/FastAPI dashboard behavior, startup scripts, logs, or project workflow.
---

# OmniSignal Project Rules

## Scope discipline

- Do not change anything the user did not ask to change.
- If a requested outcome appears to require changing code, files, behavior, data, configuration, UI, scripts, logs, or repository contents outside the explicitly requested scope, stop and ask the user for permission before making that extra change.
- Prefer the smallest safe change that satisfies the request.
- Preserve existing working behavior unless the user explicitly asks to change it.
- Do not modify the `Terminal/` reference project unless the user explicitly authorizes it.

## Dashboard/data boundary

- Dashboard refresh controls should only read from the existing database and regenerate display artifacts such as PNG/HTML charts for the selected display date range.
- Data fetching, database mutation, and derived-factor computation should run through the dedicated data update workflow, not through ordinary dashboard display refresh.
- Display date ranges must affect chart rendering only; they must not constrain historical data ingestion or database storage.

## Data integrity

- Use real data only. Do not fabricate placeholder series or draw fake charts.
- If a data source fails, report the failure and record the reason in logs instead of silently skipping it.
- Incremental updates should check existing database coverage first and only fetch missing data.
