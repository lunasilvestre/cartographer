<!-- Thanks for contributing. CONTRIBUTING.md has the full workflow; this is the
     short version. Delete any section that does not apply. -->

## What this changes

<!-- One or two sentences. -->

## Why

<!-- The problem being solved. Link an issue if there is one: "Fixes #123". -->

---

## Checks

Run from `servers/eo-mcp/`. These are exactly what CI runs.

- [ ] `uv run ruff check src/ tests/`
- [ ] `EO_MCP_SKIP_INTEGRATION=1 uv run pytest -m "not slow" -q`
- [ ] `uv lock --check` — passes, or the lockfile is regenerated and committed
- [ ] `uv sync --frozen --extra dev`

If you changed request-building code, also run the live suite once:

- [ ] `uv run pytest -m slow` — or N/A

## Constraints

- [ ] The tool set is still exactly the eleven documented tools, with the same names
- [ ] No tool accepts a URL, hostname, or file path as a parameter
- [ ] No geospatial computation was added to the server

<!-- If you are intentionally changing the tool set, it needs prior agreement —
     see CONTRIBUTING.md — and all of .github/scripts/mcp_stdio_smoke.py (the
     EXPECTED_TOOLS set), README.md, servers/eo-mcp/README.md and CLAUDE.md must
     be updated together. ci.yml contains no tool names; it invokes the script. -->

## Tests

- [ ] Behavioural changes are covered by tests
- [ ] Unit tests do not touch the network
- [ ] Failure paths are covered where relevant (timeout, 5xx, malformed GeoJSON, empty result, oversized body)

## Documentation

- [ ] Tool signature changes are reflected in `servers/eo-mcp/README.md` (the canonical reference)
- [ ] New or changed settings are in `.env.example`, `README.md` and `docs/SELF-HOSTING.md`
- [ ] `CHANGELOG.md` updated under `[Unreleased]`

**Any claim about backend behaviour was verified against the live API, not written from memory.**

- [ ] Yes — probed it
- [ ] N/A — no backend claims

<!-- The documentation in this repo was wrong once because collection names were
     written from memory. If you are stating what an endpoint returns, please
     paste the request you ran. -->

## Anything reviewers should know

<!-- Trade-offs, things you were unsure about, follow-up work you deliberately
     left out. -->
