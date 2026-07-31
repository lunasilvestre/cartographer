---
name: Bug report
about: Something behaves differently from how it is documented
title: ''
labels: bug
assignees: ''
---

## What happened

<!-- What you observed. If a tool returned an error, paste it verbatim. -->

## What you expected

<!-- -->

## The tool call

<!-- The tool name and arguments. If you were driving it through an LLM client,
     the client usually shows the JSON arguments it sent. -->

```json
{
  "tool": "",
  "arguments": {}
}
```

## What came back

```json

```

## Was the answer wrong, or was it an error?

<!-- Please say which. A tool that returns a plausible but incorrect number is a
     more serious bug here than one that raises, and it is the class of problem
     this project most wants to hear about. If you know the correct answer and
     how you established it, include that. -->

- [ ] It raised an error or failed loudly
- [ ] It returned a result that looked fine but was wrong

## Can you reproduce it against the backend directly?

<!-- Optional but very helpful. The tools are thin proxies, so an equivalent
     curl usually isolates whether the bug is in eo-mcp or in the backend.
     See docs/WORKED-EXAMPLE.md for the request patterns. -->

```bash

```

## Environment

- `eo-mcp` version or commit:
- Python version (`python --version`):
- Installed via: <!-- uvx / editable checkout / Docker / other -->
- MCP client: <!-- Claude Desktop, Claude Code, other, or "called directly" -->
- OS:

**Backend** — if you changed any of these from the defaults, say so:

- `EO_MCP_STAC_URL`:
- `EO_MCP_RASTER_URL`:
- `EO_MCP_VECTOR_URL`:

<!-- Do not paste EO_MCP_AUTH_TOKEN. -->

## Anything else

<!-- -->
