---
name: Wrong answer (no error raised)
about: A tool returned a plausible result that was incorrect
title: ''
labels: bug, wrong-answer
assignees: ''
---

<!-- This template exists because it is the failure mode that matters most for an
     LLM-facing tool. A proxy that raises is easy to notice. A proxy that returns
     a precise, plausible, wrong number gets believed and repeated.

     Known examples are written up in docs/WORKED-EXAMPLE.md and listed under
     "Known issues" in CHANGELOG.md — worth a glance in case yours is there. -->

## The question you were asking

<!-- In plain language, what were you trying to find out? -->

## The tool call and its result

```json
{
  "tool": "",
  "arguments": {}
}
```

```json

```

## Why you believe it is wrong

<!-- The strongest reports include an independent check. Some that work well:

     - A curl against the backend that yields a different number
     - A cross-check in QGIS, rasterio or another tool
     - An internal inconsistency (an area, count or extent that cannot be right)
     - The same query at a different scale giving an incompatible answer -->

## The correct answer, if you know it

<!-- And how you established it. -->

## Is the backend or the proxy at fault?

<!-- The tools are thin, so the same request via curl usually settles it. -->

- [ ] `curl` against the backend gives the same wrong answer — likely the backend or the data
- [ ] `curl` gives the right answer — likely `eo-mcp`
- [ ] Haven't checked

```bash

```

## Environment

- `eo-mcp` version or commit:
- Backend URLs, if not the defaults:
