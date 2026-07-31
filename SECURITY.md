# Security

## What this software is

`eo-mcp` is a **stdio MCP server that proxies HTTP requests** to three
Earth Observation backends (a STAC API, TiTiler, and tipg). It performs no
geospatial computation, executes no user-supplied code, has no database, no
authentication of its own, and no persistent state.

It normally runs as a subprocess of an MCP client (Claude Desktop, Claude Code)
on a single user's machine, communicating over stdin/stdout. It is not a network
service and does not listen on a port.

Being realistic about scale: this is a personal portfolio project, not
production infrastructure with a security team behind it. Treat the guarantees
below as design intent that has been reasoned about and tested, not as an
audited assurance.

---

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private reporting on the repository:
**Security → Advisories → Report a vulnerability**, or email
**lunasilvestre@gmail.com** with `eo-mcp security` in the subject.

Please include what you did, what happened, and what you expected. A proof of
concept helps enormously.

This is a single-maintainer side project. Expect an acknowledgement within about
a week, and please allow reasonable time for a fix before public disclosure.
There is no bounty programme.

---

## Threat model

### The main deliberate control: no tool accepts a URL

Every tool takes a `collection_id` and `item_id` (or a vector `collection_id`)
and resolves them against the backends configured in the environment. **No tool
parameter is a URL, a hostname, a file path, or anything else that selects a
request target.**

This is the single most important property of the design. A geospatial proxy
that accepts `url=` — as plain TiTiler's `/cog/info?url=…` routes do — hands
every caller a server-side request forgery primitive: the LLM, or anyone able to
influence it via prompt injection, could point it at cloud metadata endpoints or
internal hosts. `eo-mcp` deliberately uses only the *pgstac* routes, which are
keyed by catalogue identifiers rather than URLs.

The backend URLs come from `EO_MCP_RASTER_URL`, `EO_MCP_VECTOR_URL` and
`EO_MCP_STAC_URL`, which are operator configuration, not tool input. They are
validated at startup: a missing scheme, a non-`http(s)` scheme, an empty value,
or a query string is rejected.

Identifiers are still interpolated into request paths, so a hostile
`collection_id` can shape a path within the configured host. It cannot change
which host is contacted.

### What an attacker who controls the LLM can do

Assume prompt injection succeeds and the model calls tools with hostile
arguments. They can:

- Read any data the configured backends expose without authentication. For the
  demo backend that is all of it — it is public.
- Cause requests to the configured backends, bounded by
  `EO_MCP_MAX_CONCURRENCY` (default 5) and `EO_MCP_MAX_RETRIES` (default 2).
- Get back large responses, consuming context and tokens.

They cannot select a different host, read local files, execute code, or reach
your internal network through this server.

### What is explicitly out of scope

- **The backends themselves.** Vulnerabilities in TiTiler, tipg,
  stac-fastapi-pgstac, PostGIS or nginx belong to those projects.
- **The MCP client.** How your client sandboxes tools, and whether it confirms
  calls with you, is the client's concern.
- **The demo data.** Everything at `api.cheias.pt` is public, non-personal
  Copernicus EMS and ERA5/NOAA data. There is nothing there to protect.
- **Prompt injection in returned data.** Tool output is passed back to the model.
  If your backend contains attacker-controlled text in a feature property, that
  text reaches the model. `eo-mcp` does not sanitise it, and no proxy sensibly
  could. This matters if you point the server at a backend where third parties
  can write.

---

## Operational notes

### `EO_MCP_AUTH_TOKEN` is sent to all three backends

If you set it, the same bearer token goes to the raster, vector and STAC URLs. If
those are different trust domains, that is a credential leak — configure only
backends you are willing to give the same token.

Provide it via your MCP client's `env` block or the environment. Avoid putting a
real token in a committed `.env`; `.env.example` ships with the field empty.

### The demo backend has no uptime commitment

`api.cheias.pt` is a personal deployment offered so a stranger can try this
without deploying a stack. There is **no SLA, no support channel, no guarantee it
will keep existing, and no commitment to any particular data staying available**.
It is not suitable as a dependency for anything that matters. Point the server at
your own stack — see [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md).

It serves public, non-personal data over HTTPS and requires no authentication.
Requests are not knowingly logged for analysis, but it is a normal web server
behind nginx and standard access logs exist.

### No supply-chain guarantees beyond pinning

Dependencies are declared in `pyproject.toml` with ranges and pinned in
`uv.lock`. CI verifies the lockfile matches and that a clean install works. There
is no artifact signing, no SBOM, and no automated vulnerability scanning. Four
runtime dependencies: `fastmcp`, `httpx`, `pydantic`, `pydantic-settings`.

---

## Supported versions

Pre-1.0 and single-maintainer. Fixes land on `main`; there are no maintained
release branches and no backports.

| Version | Supported |
|---|---|
| `main` | Yes |
| Anything else | No |
