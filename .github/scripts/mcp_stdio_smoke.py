#!/usr/bin/env python3
"""Drive an MCP server over stdio with a real JSON-RPC handshake.

Asserts the server completes `initialize`, accepts `notifications/initialized`,
and returns exactly the 11 documented tools from `tools/list`.

This exists because "the process started" is not evidence an MCP server works —
a server that starts and hangs looks identical from the outside. The only
honest check is to speak the protocol to it.

Usage:
    python mcp_stdio_smoke.py eo-mcp
    python mcp_stdio_smoke.py docker run --rm -i eo-mcp:ci
    python mcp_stdio_smoke.py --expect-version 0.1.0 eo-mcp

`--expect-version` additionally asserts that the `serverInfo.version` in the
initialize response matches the given string. Worth pinning: FastMCP defaults
that field to *its own* version when the server omits one, so eo-mcp once
reported itself to every client as "3.4.5" and the number changed silently with
an unrelated dependency bump. Omit the flag to report the version without
asserting on it.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time

# Kept in lockstep with CLAUDE.md, README.md, and servers/eo-mcp/README.md.
# A mismatch here should fail the build rather than let the tool surface drift.
EXPECTED_TOOLS = {
    "get_raster_info",
    "get_tile_url",
    "get_zonal_statistics",
    "list_vector_collections",
    "get_vector_features",
    "get_vector_feature",
    "get_vector_tile_url",
    "get_collection_queryables",
    "flood_rainfall_analysis",
    "multi_layer_summary",
    "collection_health_check",
}

PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 60.0


def main(argv: list[str]) -> int:
    # Parsed by hand rather than with argparse, which would swallow the flags
    # belonging to the child command (`docker run --rm -i ...`).
    expect_version: str | None = None
    if argv and argv[0] == "--expect-version":
        if len(argv) < 2:
            print("--expect-version needs a value", file=sys.stderr)
            return 2
        expect_version = argv[1]
        argv = argv[2:]

    if not argv:
        print("usage: mcp_stdio_smoke.py [--expect-version X] <command> [args...]", file=sys.stderr)
        return 2

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    inbox: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def pump(stream, tag: str) -> None:
        for line in stream:
            inbox.put((tag, line.rstrip("\n")))
        inbox.put((tag, None))

    threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True).start()
    threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True).start()

    def send(msg: dict) -> None:
        raw = json.dumps(msg)
        print(f">>> {raw}", flush=True)
        proc.stdin.write(raw + "\n")
        proc.stdin.flush()

    def await_response(want_id: int) -> dict | None:
        """Read stdout until the JSON-RPC response with `want_id` arrives.

        stderr is echoed but never parsed: it carries the FastMCP startup banner
        and logs, which are not part of the protocol stream.
        """
        deadline = time.time() + TIMEOUT
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                print(f"!!! timeout waiting for id={want_id}", file=sys.stderr)
                return None
            try:
                tag, line = inbox.get(timeout=remaining)
            except queue.Empty:
                print(f"!!! timeout waiting for id={want_id}", file=sys.stderr)
                return None
            if line is None:
                if tag == "out":
                    print("!!! stdout closed before a response arrived", file=sys.stderr)
                    return None
                continue
            if tag == "err":
                print(f"    [stderr] {line}", flush=True)
                continue
            print(f"<<< {line}", flush=True)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # not protocol traffic
            if msg.get("id") == want_id:
                return msg

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "ci-stdio-smoke", "version": "1.0"},
                },
            }
        )
        init = await_response(1)
        if init is None or "result" not in init:
            print("FAIL: no usable initialize response", file=sys.stderr)
            return 1

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = await_response(2)
        if listed is None or "result" not in listed:
            print("FAIL: no usable tools/list response", file=sys.stderr)
            return 1

        got = {t["name"] for t in listed["result"].get("tools", [])}
        server_info = init["result"].get("serverInfo", {})

        print()
        print(f"serverInfo:      {server_info}")
        print(f"protocolVersion: {init['result'].get('protocolVersion')}")
        print(f"tools returned:  {len(got)}")
        for name in sorted(got):
            print(f"  - {name}")

        missing = sorted(EXPECTED_TOOLS - got)
        unexpected = sorted(got - EXPECTED_TOOLS)
        if missing or unexpected:
            print(f"\nFAIL: missing={missing} unexpected={unexpected}", file=sys.stderr)
            return 1

        if expect_version is not None:
            actual = server_info.get("version")
            if actual != expect_version:
                print(
                    f"\nFAIL: serverInfo.version is {actual!r}, expected {expect_version!r}. "
                    "If this is the FastMCP version, build_server() has stopped passing "
                    "an explicit version= and is falling back to FastMCP's own.",
                    file=sys.stderr,
                )
                return 1
            print(f"serverInfo.version matches expected {expect_version}")

        print(f"\nOK: exactly {len(got)} documented tools served over stdio JSON-RPC")
        return 0
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
