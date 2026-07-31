# Claude Code prompt — cartographer hardening

Run from the repo root: `cd ~/Documents/dev/cartographer`

Enable Agent Teams first (experimental, off by default):

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Then paste everything below the line into Claude Code.

---

I'm about to make this repo public as the portfolio piece behind a job application. It has to be
bombproof, trivially easy for a stranger to run, and documented so well that nobody needs to ask me
a question. Right now it's close but not there.

**Read `CLAUDE.md`, `README.md`, `ARCHITECTURE.md` and `servers/eo-mcp/README.md` first.** A previous
session already did the following, so don't redo it:

- Added Apache-2.0 `LICENSE`, a root `README.md`, `.env.example`, and `.github/workflows/ci.yml`
- Fixed `fastmcp>=2.0` → `>=3.0,<4` (2.x genuinely cannot import this code — verified)
- Pinned `ruff>=0.16,<0.17` and added an explicit `[tool.ruff.lint]` select, because ruff's default
  rule set widens between releases and was silently breaking lint
- Corrected the collection lists in `CLAUDE.md` and `servers/eo-mcp/README.md` — the old ones were
  fictional. The real backend was probed live on 2026-07-31.
- Verified: wheel builds clean, ruff passes, 60 unit tests pass, server registers exactly 11 tools

Use delegate mode. Spawn 4 teammates with strict directory ownership — two teammates editing the
same file will clobber each other.

**Teammate 1 — packaging and distribution. Owns `servers/eo-mcp/pyproject.toml`, `uv.lock`,
`Dockerfile`, and `.github/`.**
`uv.lock` is stale: it predates the fastmcp and ruff pin changes, so `uv sync` and `uv pip install`
can disagree. Regenerate it and make sure a locked install and a fresh resolve both work. The
Dockerfile has never been built — build it, run it, prove the container actually speaks MCP over
stdio rather than just starting. Verify the headline install path works from a clean machine with no
checkout: `uvx --from "git+https://github.com/lunasilvestre/cartographer.git#subdirectory=servers/eo-mcp" eo-mcp`
(it will fail until the repo is public — test the equivalent against a local path and say so
explicitly rather than claiming it passed). Add `py.typed` so downstream type checkers see the
annotations. Consider whether this should also publish to PyPI, and if you think yes, say why and
wire up a release workflow gated on a tag.

**Teammate 2 — server robustness. Owns `servers/eo-mcp/src/` and `servers/eo-mcp/tests/`.**
Three known weaknesses, all confirmed:

1. `raster.py` and `vector.py` each hold a module-level `_client` singleton built from whatever
   `Settings` arrived first. It ignores settings on every later call and is never closed. The test
   suite has an autouse fixture that resets both globals, which is the smell. Fix the lifecycle
   properly.
2. Errors come back as `{"error": ..., "status": ...}` dicts. An LLM may read that as a successful
   result and confidently report nonsense. Decide between raising `ToolError` and returning a
   structured failure, apply it consistently across all 11 tools, and document the contract.
3. No retry, no backoff, no explicit connect-vs-read timeout split. A flaky backend currently
   surfaces as a bare exception mid-conversation.

Also: `get_tile_url` ignores the `cheias:rescale` and `cheias:colormap` properties that the STAC
collections already publish, so agents guess values that are sitting right there in the metadata.
Wire that in as a default that callers can override.

Every change needs tests. The 60 existing unit tests must stay green. Push coverage up and enforce a
floor in CI. Add tests for the failure paths — timeout, 500, malformed GeoJSON, empty
FeatureCollection, unreachable host.

**Teammate 3 — documentation. Owns all `.md` files and `.env.example`. Does not touch code.**
Restructure into something a stranger can follow linearly: what this is → 60-second try-it →
tool reference → configuration → running your own backend → development → contributing. Create the
`docs/` directory that `ARCHITECTURE.md` promises but never delivered, or delete the promise —
either is fine, ambiguity is not. Add `CONTRIBUTING.md`, `SECURITY.md`, a `CHANGELOG.md`, and GitHub
issue/PR templates. Write a genuine worked example: a real question, the actual tool calls it
triggers, and the real answer — run it against the live backend and paste real output, do not invent
it. Check every command in every code block by executing it. Documentation that has not been run is
a guess.

**Teammate 4 — adversarial verification. Read-only until the other three report, then owns nothing
and breaks everything.**
While the others work, write the acceptance checklist. Then execute it against a fresh `git clone`
into `/tmp` — not the working tree, which has state the others created. Specifically: does a cold
install work with no network access to anything but PyPI and GitHub? What happens when
`api.cheias.pt` is unreachable, returns 500, or hangs — simulate all three. What happens with a
malformed `EO_MCP_RASTER_URL`, a URL with a trailing slash, an `http://` URL, an empty string? Does
every command in every markdown file actually run? Does the CI workflow pass on all three Python
versions? Try to make the server return a confidently wrong answer to a plausible question — that is
the failure mode that matters most for an LLM-facing tool. Report what you broke; don't fix it
yourself, hand it back.

Have teammates message each other directly when their work overlaps — teammate 2's error-contract
decision changes what teammate 3 documents and what teammate 4 tests, so that decision should be
broadcast as soon as it's made. Everything else, report to the lead.

Non-negotiables:

- The tool set stays at exactly 11, with the current names. If you have a strong case for changing
  one, raise it with me rather than doing it — the name list is asserted in `.github/workflows/ci.yml`
  and quoted in three documents and in a job application.
- Never invent collection names, item IDs, or field names. Probe `https://api.cheias.pt` and use what
  is actually there. The last round of documentation was wrong precisely because someone did not.
- Integration tests hit a live personal backend. They must stay skippable via
  `EO_MCP_SKIP_INTEGRATION=1` and must never gate CI — a backend outage must not turn the build red.
- `LICENSE` and `pyproject.toml` say "Nelson Luna Silvestre". Leave it; I'll confirm separately.
- Delete `prompts/backfill-repos-frontmatter.md` — it's an unrelated stray that would otherwise ship.

Two things I could not verify myself and want proven, not assumed: the 11 live integration tests
have never been run to completion, and the Docker image has never been built. If either is broken,
I'd rather find out now.

When the team converges, give me: a summary of what changed and why, anything you deliberately chose
not to do, the acceptance checklist with pass/fail per line, and a single honest answer to "could a
competent stranger clone this and have it working in five minutes without asking a question?"
