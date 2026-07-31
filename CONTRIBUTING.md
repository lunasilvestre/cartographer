# Contributing

Thanks for looking. This is a small, focused project — a proxy MCP server over an
eoAPI stack — and the constraints below exist to keep it that way.

---

## Setup

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python
3.11 or newer.

```bash
git clone https://github.com/lunasilvestre/cartographer.git
cd cartographer/servers/eo-mcp

uv sync --extra dev
```

`uv sync` creates the virtualenv and installs the exact versions from the
committed `uv.lock`, so you get what CI tested. There is no separate `uv venv`
step.

> **Do not mix `uv pip install` with `uv run`.** `uv run` re-syncs the
> environment from `uv.lock` before executing, silently discarding anything
> `uv pip install` put there. Verified: with `fastmcp==3.2.4` installed via
> `uv pip install`, the next `uv run` uninstalled it and reinstalled the locked
> version. Use `uv sync` + `uv run` together, or `uv pip install` +
> `.venv/bin/…` together — never one of each. CI's `test` job deliberately calls
> `.venv/bin/ruff` and `.venv/bin/python` directly for exactly this reason.

No `uv`? Plain pip works:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -m "not slow"
```

All development commands run from `servers/eo-mcp/`, which is also CI's working
directory.

---

## Before you open a pull request

These mirror what CI runs, so if they pass locally the build should be green.

```bash
uv run ruff check src/ tests/                              # lint
EO_MCP_SKIP_INTEGRATION=1 uv run pytest -m "not slow" -q   # unit tests
uv lock --check                                            # lockfile matches pyproject
```

Coverage, which CI enforces:

```bash
EO_MCP_SKIP_INTEGRATION=1 uv run coverage run -m pytest -m "not slow"
uv run coverage report
```

`--source` and `--branch` are configured in `pyproject.toml`, so the bare
commands do the right thing. Branch coverage is on, not just line coverage.

Formatting fixes:

```bash
uv run ruff check --fix src/ tests/
```

---

## What CI enforces

Four jobs, from [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

> **One honest caveat.** Every command described below has been executed locally
> and behaves as stated. **The workflow itself has never run in GitHub Actions** —
> this repository was not public while it was written, so no push has triggered
> it. The runner setup (`actions/checkout`, `astral-sh/setup-uv`, the matrix, the
> Docker job) is unproven until the first push, and the CI badge at the top of the
> README will be grey until then. Treat a first-push failure as expected teething
> rather than as a broken repository.

### `test (py3.11 / py3.12 / py3.13)`

Installs with a **fresh resolve** (`uv pip install -e '.[dev]'`, ignoring
`uv.lock`), then runs `ruff check src/ tests/`, the unit suite, and coverage.
The fresh resolve is deliberate: it is the path a contributor gets from a plain
pip install, and it catches a dependency shipping a breaking change inside our
declared version ranges.

Every step in this job invokes `.venv/bin/…` directly rather than `uv run`,
because `uv run` would re-sync from `uv.lock` and turn the job into a duplicate
of `locked`.

**Coverage is gated**, with the floor set as `fail_under` in
`[tool.coverage.report]` and `branch = true`. Read the current value from
`pyproject.toml` rather than from any document — it has moved more than once.
Coverage runs on all three Python versions rather than once: a branch reachable
only on 3.11 is exactly what a single-version run misses.

The step runs `coverage run -m pytest` and *then* `coverage report`, and that
order is load-bearing — **don't reorder them.**

The hazard is measured: on a tree with failing tests, `coverage run -m pytest`
exits non-zero, and `coverage report` on that same red tree printed 98% against a
floor of 97. So a reversed order really could report a green coverage gate on a
red suite. What makes the current order safe is that GitHub Actions runs `run:`
blocks under `bash -e`, aborting the step at the first non-zero exit — that is
documented Actions behaviour rather than something observed here, per the caveat
above.

### `locked install (uv.lock)`

Runs `uv lock --check` to fail if `uv.lock` has drifted from `pyproject.toml`,
then installs with `uv sync --frozen` and runs the unit suite against the pinned
versions.

**If you change a dependency, regenerate the lockfile and commit it**, or this
job fails.

### `clean-install smoke test`

Installs non-editable from a clean tree, builds a wheel and asserts it ships
`eo_mcp/py.typed` plus the package modules, then starts the installed `eo-mcp`
console script and drives a real MCP handshake over stdio, asserting the server
serves **exactly the eleven documented tools and reports the correct version**.

The version check (`--expect-version`, read from `pyproject.toml` at runtime so a
bump needs no workflow edit) guards a regression that is invisible locally: if
`build_server()` stops passing an explicit `version=`, FastMCP substitutes its
own and every client is told `eo-mcp` is version `3.x` — a number that then
changes with an unrelated dependency bump.

Starting the process proves nothing — a server that starts and hangs looks
identical to one that works. The smoke script talks to it.

### `docker image`

Builds the image, runs the same stdio handshake against `docker run --rm -i`, and
asserts the container's UID is not 0.

### Not enforced: integration tests

The tests in `tests/test_integration.py` hit the live backend at
`api.cheias.pt`. **They never run in CI.** A backend outage must not turn the
build red. Run them locally when you are changing request-building code:

```bash
uv run pytest              # everything, including the live tests
uv run pytest -m slow      # only the live tests
```

Two independent ways to skip them, both verified:

```bash
EO_MCP_SKIP_INTEGRATION=1 uv run pytest   # env var -> skipped
uv run pytest -m "not slow"               # marker  -> deselected
```

Deliberately no test count here. The suite moved from 60 to over 180 unit tests
in a single working session, and a number in a document is wrong the moment
someone adds a test. Run the command; it will tell you.


---

## Constraints on changes

### The tool set is exactly eleven, with these names

```
get_raster_info          get_tile_url             get_zonal_statistics
list_vector_collections  get_vector_features      get_vector_feature
get_vector_tile_url      get_collection_queryables
flood_rainfall_analysis  multi_layer_summary      collection_health_check
```

CI asserts this set exactly — adding, removing or renaming a tool fails the
build. That is intentional: the list is quoted in several documents and drifting
silently is worse than a red build.

**Adding or changing a tool's *parameters* is fine** and does not touch this
constraint. Adding a *tool* needs a conversation first — open an issue.

If a change to the set is agreed, update all of: `.github/scripts/mcp_stdio_smoke.py`
(the `EXPECTED_TOOLS` set that CI asserts against), `README.md`,
`servers/eo-mcp/README.md`, and `CLAUDE.md`.

### The server does no geospatial computation

`eo-mcp` builds URLs, validates parameters, forwards requests and shapes
responses. If your change needs `numpy`, `rasterio`, or `shapely`, it belongs in
a backend or in [gdal-mcp](docs/SIDEKICKS.md), not here. See
[ARCHITECTURE.md](ARCHITECTURE.md).

### No tool takes a URL

Every tool resolves against the configured backend from a `collection_id` /
`item_id`. Accepting a caller-supplied URL would turn the server into an SSRF
primitive. See [SECURITY.md](SECURITY.md).

### Never document an unverified API fact

The collection lists, field names and item IDs in this repo were wrong once
because someone wrote them from memory. If you are documenting backend
behaviour, probe it:

```bash
curl -s "https://api.cheias.pt/collections?limit=100" | jq -r '.collections[].id'
```

Divergences between the live API and its documentation belong in
`servers/eo-mcp/docs/tipg-api-notes.md` or `titiler-api-notes.md`, with the
request that demonstrates them.

---

## Tests

Every behavioural change needs a test. The unit suite must not touch the
network — mock at the HTTP layer.

Failure paths matter more than happy paths here, because the interesting bugs in
a proxy are the ones that return a plausible wrong answer rather than an error.
Worth covering: timeouts, 5xx, malformed GeoJSON, empty FeatureCollections,
unreachable hosts, and oversized request bodies.

Mark anything that hits the network:

```python
@pytest.mark.slow
@pytest.mark.integration
def test_something_live(): ...
```

and honour `EO_MCP_SKIP_INTEGRATION`.

---

## Style

Ruff handles it — `line-length = 120`, `target-version = "py311"`, with lint
rules selected explicitly in `pyproject.toml` rather than relying on ruff's
defaults, which widen between releases. The ruff version is pinned to
`>=0.16,<0.17` for the same reason.

Type annotations on anything public; the package ships `py.typed`.

---

## Reporting bugs

Use the issue templates. For a tool returning a *wrong* answer rather than an
error, please include the exact tool call, what you got, and what you expected —
that class of bug is the one this project most wants to hear about.

Security issues: **do not open an issue**. See [SECURITY.md](SECURITY.md).

---

## Licence

By contributing you agree your contributions are licensed under Apache-2.0, the
same as the project.
