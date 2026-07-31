"""The shared HTTP layer: client lifecycle, retry policy, and error messages.

These test :mod:`eo_mcp.http` directly rather than through a tool, because the
behaviour they pin — how many attempts a 503 gets, how long the gap is, which
requests are safe to repeat — is the same for all eleven tools and is not
visible from any one of them.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastmcp.exceptions import ToolError

from eo_mcp.config import Settings
from eo_mcp.http import (
    Backends,
    HttpBackend,
    backends_lifespan,
    list_all_collections,
    parse_rescale,
    require,
    rescale_covers,
    rescale_low_contrast,
)

from .conftest import make_response


def _settings(**overrides) -> Settings:
    base = {
        "raster_url": "https://fake.example/raster",
        "stac_url": "https://fake.example",
        "vector_url": "https://fake.example/vector",
        "max_retries": 0,
        "retry_backoff": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def no_sleep():
    """Replace the backoff sleep with a recorder so tests do not actually wait."""
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    with patch("eo_mcp.http.asyncio.sleep", new=_sleep):
        yield slept


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------


async def test_client_is_created_lazily_and_reused():
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")
    assert backend._client is None

    first = await backend.client()
    second = await backend.client()

    assert first is second
    await backend.aclose()


async def test_aclose_closes_the_client_and_is_idempotent():
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")
    client = await backend.client()

    await backend.aclose()
    await backend.aclose()

    assert client.is_closed


async def test_backends_are_instance_scoped_not_module_scoped():
    """The regression this whole refactor exists for.

    The clients used to be module-level singletons built from whichever
    Settings arrived first, so a second server in the same process silently
    talked to the first server's backends. They are now owned per instance.
    """
    first = Backends(_settings(raster_url="https://first.test/raster"))
    second = Backends(_settings(raster_url="https://second.test/raster"))

    first_client = await first.raster.client()
    second_client = await second.raster.client()

    # httpx appends a trailing slash to base_url when it normalises it.
    assert str(first_client.base_url).rstrip("/") == "https://first.test/raster"
    assert str(second_client.base_url).rstrip("/") == "https://second.test/raster"
    assert first_client is not second_client

    await first.aclose()
    await second.aclose()


async def test_timeout_is_split_between_connect_and_read():
    """A slow analysis query must not be killed by a connect-tuned budget."""
    backend = HttpBackend(
        "raster",
        "https://fake.example/raster",
        _settings(connect_timeout=2.0, request_timeout=45.0), "EO_MCP_RASTER_URL",
    )

    client = await backend.client()

    assert client.timeout.connect == 2.0
    assert client.timeout.read == 45.0
    await backend.aclose()


async def test_auth_token_becomes_a_bearer_header():
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(auth_token="s3cret"), "EO_MCP_RASTER_URL")

    client = await backend.client()

    assert client.headers["Authorization"] == "Bearer s3cret"
    await backend.aclose()


async def test_no_auth_header_when_token_is_blank():
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    client = await backend.client()

    assert "Authorization" not in client.headers
    await backend.aclose()


async def test_lifespan_closes_every_backend():
    """The lifespan owns shutdown; nothing leaks a live connection pool."""
    backends = Backends(_settings())
    clients = [await b.client() for b in backends.all()]

    lifespan = backends_lifespan(backends)
    async with lifespan(object()):
        assert not any(c.is_closed for c in clients)

    assert all(c.is_closed for c in clients)


async def test_lifespan_closes_backends_even_when_the_body_raises():
    backends = Backends(_settings())
    client = await backends.raster.client()

    lifespan = backends_lifespan(backends)
    with pytest.raises(RuntimeError):
        async with lifespan(object()):
            raise RuntimeError("server crashed")

    assert client.is_closed


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


def _sequence_router(*statuses: int, body=None, headers=None):
    """Return responses with the given statuses, in order, then 200 forever."""
    remaining = list(statuses)

    def _router(request: httpx.Request, **kw):
        status = remaining.pop(0) if remaining else 200
        if status >= 400:
            return make_response(request, status, text=f"failure {status}", headers=headers)
        return make_response(request, 200, body if body is not None else {"ok": True})

    return _router


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
async def test_retryable_status_is_retried_then_succeeds(status: int, no_sleep):
    backend = HttpBackend(
        "raster", "https://fake.example/raster", _settings(max_retries=2, retry_backoff=0.5), "EO_MCP_RASTER_URL"
    )
    send = AsyncMock(side_effect=_sequence_router(status))

    with patch("httpx.AsyncClient.send", new=send):
        result = await backend.request("GET", "/info")

    assert result == {"ok": True}
    assert send.call_count == 2
    assert len(no_sleep) == 1
    await backend.aclose()


async def test_retries_are_exhausted_and_then_raise(no_sleep):
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=2), "EO_MCP_RASTER_URL")
    send = AsyncMock(side_effect=_sequence_router(503, 503, 503))

    with patch("httpx.AsyncClient.send", new=send), pytest.raises(ToolError) as excinfo:
        await backend.request("GET", "/info")

    assert send.call_count == 3  # 1 attempt + 2 retries
    assert "503" in str(excinfo.value)
    await backend.aclose()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
async def test_client_errors_are_never_retried(status: int, no_sleep):
    """A 4xx will fail identically however many times it is repeated."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=3), "EO_MCP_RASTER_URL")
    send = AsyncMock(side_effect=_sequence_router(status, status, status, status))

    with patch("httpx.AsyncClient.send", new=send), pytest.raises(ToolError, match=str(status)):
        await backend.request("GET", "/info")

    assert send.call_count == 1
    assert no_sleep == []
    await backend.aclose()


async def test_max_retries_zero_disables_retrying(no_sleep):
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=0), "EO_MCP_RASTER_URL")
    send = AsyncMock(side_effect=_sequence_router(503))

    with patch("httpx.AsyncClient.send", new=send), pytest.raises(ToolError):
        await backend.request("GET", "/info")

    assert send.call_count == 1
    await backend.aclose()


async def test_connect_error_is_retried_then_succeeds(no_sleep):
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=2), "EO_MCP_RASTER_URL")
    calls = {"n": 0}

    def _router(request: httpx.Request, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return make_response(request, 200, {"ok": True})

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        assert await backend.request("GET", "/info") == {"ok": True}

    assert calls["n"] == 2
    await backend.aclose()


async def test_read_timeout_is_not_retried(no_sleep):
    """A hung backend must not cost three full read budgets.

    Retrying a read timeout multiplies the worst case by max_retries+1 — at the
    default 30s that is ~92 seconds of silence for one tool call — and it is the
    retry least likely to help, because a backend hung on a slow query hangs the
    same way again. Connect failures are the opposite and are still retried.
    """
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=2), "EO_MCP_RASTER_URL")
    send = AsyncMock(side_effect=lambda request, **kw: (_ for _ in ()).throw(
        httpx.ReadTimeout("too slow", request=request)
    ))

    with patch("httpx.AsyncClient.send", new=send), pytest.raises(ToolError) as excinfo:
        await backend.request("GET", "/info")

    assert send.call_count == 1
    assert no_sleep == []
    message = str(excinfo.value)
    assert "did not answer" in message
    assert "https://fake.example/raster/info" in message
    assert "EO_MCP_REQUEST_TIMEOUT" in message
    await backend.aclose()


async def test_connect_timeout_is_still_retried(no_sleep):
    """The cheap, genuinely-transient failure keeps its retries."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=2), "EO_MCP_RASTER_URL")
    calls = {"n": 0}

    def _router(request: httpx.Request, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("no route", request=request)
        return make_response(request, 200, {"ok": True})

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        assert await backend.request("GET", "/info") == {"ok": True}

    assert calls["n"] == 3
    await backend.aclose()


async def test_post_is_not_retried_by_default(no_sleep):
    """A write is not repeated on a 5xx: the server may have applied it."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=3), "EO_MCP_RASTER_URL")
    send = AsyncMock(side_effect=_sequence_router(503, 503, 503, 503))

    with patch("httpx.AsyncClient.send", new=send), pytest.raises(ToolError):
        await backend.request("POST", "/statistics", json={})

    assert send.call_count == 1
    await backend.aclose()


async def test_post_declared_retry_safe_is_retried(no_sleep):
    """The statistics endpoint computes and returns; repeating it is correct."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=2), "EO_MCP_RASTER_URL")
    send = AsyncMock(side_effect=_sequence_router(503))

    with patch("httpx.AsyncClient.send", new=send):
        result = await backend.request("POST", "/statistics", json={}, retry_safe=True)

    assert result == {"ok": True}
    assert send.call_count == 2
    await backend.aclose()


async def test_backoff_grows_and_is_jittered(no_sleep):
    """Exponential ceiling, full jitter: concurrent callers must not resynchronise."""
    backend = HttpBackend(
        "raster", "https://fake.example/raster", _settings(max_retries=3, retry_backoff=1.0), "EO_MCP_RASTER_URL"
    )
    send = AsyncMock(side_effect=_sequence_router(503, 503, 503, 503))

    with patch("httpx.AsyncClient.send", new=send), pytest.raises(ToolError):
        await backend.request("GET", "/info")

    assert len(no_sleep) == 3
    # Full jitter draws uniformly from [0, ceiling], and the ceiling doubles.
    assert 0 <= no_sleep[0] <= 1.0
    assert 0 <= no_sleep[1] <= 2.0
    assert 0 <= no_sleep[2] <= 4.0
    await backend.aclose()


async def test_retry_after_header_is_honoured(no_sleep):
    backend = HttpBackend(
        "raster", "https://fake.example/raster", _settings(max_retries=1, retry_backoff=0.01), "EO_MCP_RASTER_URL"
    )
    send = AsyncMock(side_effect=_sequence_router(429, headers={"Retry-After": "3"}))

    with patch("httpx.AsyncClient.send", new=send):
        await backend.request("GET", "/info")

    assert no_sleep == [3.0]
    await backend.aclose()


async def test_retry_after_is_capped(no_sleep):
    """A hostile or broken Retry-After must not stall a conversation."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_retries=1), "EO_MCP_RASTER_URL")
    send = AsyncMock(side_effect=_sequence_router(503, headers={"Retry-After": "86400"}))

    with patch("httpx.AsyncClient.send", new=send):
        await backend.request("GET", "/info")

    assert no_sleep == [10.0]
    await backend.aclose()


async def test_http_date_retry_after_falls_back_to_backoff(no_sleep):
    """Retry-After may be an HTTP date; that form is ignored, not misparsed."""
    backend = HttpBackend(
        "raster", "https://fake.example/raster", _settings(max_retries=1, retry_backoff=1.0), "EO_MCP_RASTER_URL"
    )
    send = AsyncMock(
        side_effect=_sequence_router(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    )

    with patch("httpx.AsyncClient.send", new=send):
        await backend.request("GET", "/info")

    assert len(no_sleep) == 1
    assert 0 <= no_sleep[0] <= 1.0
    await backend.aclose()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_concurrency_is_bounded_per_backend():
    """The gate lives in the client, so every fan-out respects it."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(max_concurrency=3), "EO_MCP_RASTER_URL")
    state = {"in_flight": 0, "peak": 0}

    async def _router(request: httpx.Request, **kw):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0)
        state["in_flight"] -= 1
        return make_response(request, 200, {"ok": True})

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        await asyncio.gather(*(backend.request("GET", f"/info/{i}") for i in range(20)))

    assert state["peak"] <= 3
    await backend.aclose()


async def test_retries_do_not_widen_the_concurrency_gate(no_sleep):
    """A retry storm must not become a thundering herd."""
    backend = HttpBackend(
        "raster",
        "https://fake.example/raster",
        _settings(max_concurrency=2, max_retries=2, retry_backoff=0.0), "EO_MCP_RASTER_URL",
    )
    state = {"in_flight": 0, "peak": 0, "calls": 0}

    async def _router(request: httpx.Request, **kw):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        state["calls"] += 1
        await asyncio.sleep(0)
        state["in_flight"] -= 1
        return make_response(request, 503, text="overloaded")

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        results = await asyncio.gather(
            *(backend.request("GET", f"/info/{i}") for i in range(10)),
            return_exceptions=True,
        )

    assert all(isinstance(r, ToolError) for r in results)
    assert state["calls"] == 30  # 10 requests x 3 attempts
    assert state["peak"] <= 2
    await backend.aclose()


# ---------------------------------------------------------------------------
# Error message content
# ---------------------------------------------------------------------------


async def test_error_message_carries_status_method_url_and_body():
    backend = HttpBackend("vector", "https://fake.example/vector", _settings(), "EO_MCP_VECTOR_URL")

    def _router(request: httpx.Request, **kw):
        return make_response(request, 404, text='{"detail":"table not found"}')

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(ToolError) as excinfo:
        await backend.request("GET", "/collections/nope/items", params={"limit": 1})

    message = str(excinfo.value)
    assert "vector backend returned HTTP 404" in message
    assert "GET https://fake.example/vector/collections/nope/items?limit=1" in message
    assert "table not found" in message
    await backend.aclose()


async def test_long_error_body_is_truncated():
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")
    huge = "x" * 5000

    def _router(request: httpx.Request, **kw):
        return make_response(request, 500, text=huge)

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(ToolError) as excinfo:
        await backend.request("GET", "/info")

    message = str(excinfo.value)
    assert len(message) < 1000
    assert "5000 bytes total" in message
    await backend.aclose()


async def test_empty_error_body_is_described_not_blank():
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    def _router(request: httpx.Request, **kw):
        return make_response(request, 502, text="")

    with (
        patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)),
        pytest.raises(ToolError, match="empty response body"),
    ):
        await backend.request("GET", "/info")

    await backend.aclose()


def test_url_for_encodes_parameters():
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    url = backend.url_for("/tilejson.json", [("color_formula", "Gamma RGB 3.5"), ("bidx", 1)])

    assert url == (
        "https://fake.example/raster/tilejson.json?color_formula=Gamma+RGB+3.5&bidx=1"
    )


def test_url_for_accepts_a_dict():
    backend = HttpBackend("vector", "https://fake.example/vector", _settings(), "EO_MCP_VECTOR_URL")

    assert backend.url_for("/collections", {"f": "json"}) == (
        "https://fake.example/vector/collections?f=json"
    )


def test_url_for_without_params():
    backend = HttpBackend("vector", "https://fake.example/vector", _settings(), "EO_MCP_VECTOR_URL")

    assert backend.url_for("/collections") == "https://fake.example/vector/collections"


# ---------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------


def test_require_returns_the_value():
    assert require({"tiles": ["a"]}, "tiles", source="s", url="u") == ["a"]


def test_require_names_the_missing_key_and_what_was_there():
    with pytest.raises(ToolError) as excinfo:
        require({"bounds": [], "minzoom": 0}, "tiles", source="TiTiler tilejson", url="https://u")

    message = str(excinfo.value)
    assert "missing the expected 'tiles' field" in message
    assert "https://u" in message
    assert "bounds, minzoom" in message


def test_require_handles_a_non_dict_payload():
    with pytest.raises(ToolError, match="list"):
        require([1, 2], "tiles", source="s", url="u")


def test_require_handles_an_empty_dict():
    with pytest.raises(ToolError, match="empty object"):
        require({}, "tiles", source="s", url="u")


# ---------------------------------------------------------------------------
# STAC render defaults
# ---------------------------------------------------------------------------


async def test_render_defaults_caches_a_missing_collection(no_sleep):
    """A 404 is cached too, so a bad id does not re-probe on every tile call."""
    backends = Backends(_settings())
    send = AsyncMock(side_effect=_sequence_router(404, 404, 404, 404))

    with patch("httpx.AsyncClient.send", new=send):
        assert await backends.render_defaults("gone") == {}
        assert await backends.render_defaults("gone") == {}

    assert send.call_count == 1
    await backends.aclose()


async def test_render_defaults_ignores_a_malformed_rescale():
    backends = Backends(_settings())

    def _router(request: httpx.Request, **kw):
        return make_response(
            request, 200, {"cheias:rescale": "not-a-range-really", "cheias:colormap": ""}
        )

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        defaults = await backends.render_defaults("weird")

    # A string rescale is passed through as-is (TiTiler's own wire form);
    # an empty colormap is not a colormap.
    assert defaults == {"rescale": "not-a-range-really"}
    await backends.aclose()


async def test_render_defaults_ignores_a_non_dict_payload():
    backends = Backends(_settings())

    def _router(request: httpx.Request, **kw):
        return make_response(request, 200, ["not", "a", "collection"])

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        assert await backends.render_defaults("odd") == {}

    await backends.aclose()


async def test_concurrent_render_defaults_probe_once():
    backends = Backends(_settings())
    send = AsyncMock(
        side_effect=lambda request, **kw: make_response(
            request, 200, {"cheias:colormap": "blues"}
        )
    )

    with patch("httpx.AsyncClient.send", new=send):
        results = await asyncio.gather(
            *(backends.render_defaults("precipitation-daily") for _ in range(5))
        )

    assert all(r == {"colormap": "blues"} for r in results)
    assert send.call_count == 1
    await backends.aclose()


async def test_render_defaults_double_check_under_contention():
    """Waiters blocked on the lock read the cache rather than re-probing.

    The first caller is held inside the STAC request until every other caller
    has queued on the lock, so the second-chance cache read is genuinely
    exercised rather than being skipped by lucky scheduling.
    """
    backends = Backends(_settings())
    release = asyncio.Event()
    entered = asyncio.Event()

    async def _router(request: httpx.Request, **kw):
        entered.set()
        await release.wait()
        return make_response(request, 200, {"cheias:colormap": "blues"})

    send = AsyncMock(side_effect=_router)
    with patch("httpx.AsyncClient.send", new=send):
        first = asyncio.create_task(backends.render_defaults("precipitation-daily"))
        await entered.wait()
        waiters = [
            asyncio.create_task(backends.render_defaults("precipitation-daily"))
            for _ in range(4)
        ]
        await asyncio.sleep(0)  # let the waiters reach the lock
        release.set()
        results = await asyncio.gather(first, *waiters)

    assert all(r == {"colormap": "blues"} for r in results)
    assert send.call_count == 1
    await backends.aclose()


async def test_concurrent_first_use_creates_one_client():
    """Two callers racing on first use share a client rather than leaking one."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    clients = await asyncio.gather(*(backend.client() for _ in range(5)))

    assert len({id(c) for c in clients}) == 1
    await backend.aclose()


# ---------------------------------------------------------------------------
# Oversized request bodies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [413, 403])
async def test_oversize_body_gets_an_actionable_message(status: int):
    """413 and 403 on a POST body mean the same thing and neither answers in JSON.

    nginx sends 413 with an HTML page; Cloudflare sends 403 with
    `error code: 1010`. Both were reproduced against api.cheias.pt on
    2026-07-31 with a real flood polygon. Dumping either at the caller explains
    nothing, so the message names the size sent and the lever that reduces it.
    """
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    def _router(request: httpx.Request, **kw):
        return make_response(request, status, text="<html><title>413 Request Entity Too Large</title>")

    body = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0]] * 5000]}}
    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(ToolError) as excinfo:
        await backend.request("POST", "/statistics", json=body, retry_safe=True)

    message = str(excinfo.value)
    assert "larger than the backend" in message
    assert "simplify=0.001" in message
    assert "bytes" in message
    await backend.aclose()


async def test_oversize_status_without_a_body_is_an_ordinary_error():
    """A 403 on a GET is an authorisation problem, not a size problem."""
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    def _router(request: httpx.Request, **kw):
        return make_response(request, 403, text="forbidden")

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(ToolError) as excinfo:
        await backend.request("GET", "/info")

    message = str(excinfo.value)
    assert "returned HTTP 403" in message
    assert "simplify" not in message
    await backend.aclose()


# ---------------------------------------------------------------------------
# Rescale validation against the item's real data range
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rescale,data_range,expected",
    [
        # precipitation-daily publishes [0, 80]; its daily items sit inside it.
        ("0,80", (0.0, 20.75), True),
        # ...but precipitation-storm-total runs 100-582. Verified live 2026-07-31.
        ("0,80", (133.4, 516.5), False),
        ("0,80", (50.0, 90.0), False),
        ("0,80", (0.0, 80.0), True),
        # Nothing to check against: trust the curated value.
        ("0,80", None, True),
        (None, (1.0, 2.0), True),
        ("nonsense", (1.0, 2.0), True),
    ],
)
def test_rescale_covers(rescale, data_range, expected):
    assert rescale_covers(rescale, data_range) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [("0,80", (0.0, 80.0)), ("-3,3", (-3.0, 3.0)), ("0", None), ("a,b", None), (None, None)],
)
def test_parse_rescale(raw, expected):
    assert parse_rescale(raw) == expected


async def test_item_data_range_reads_percentiles():
    """Percentiles, not min/max: one outlier pixel should not set the stretch."""
    backends = Backends(_settings())

    def _router(request: httpx.Request, **kw):
        return make_response(request, 200, {
            "data_b1": {"min": 100.3, "max": 581.6, "percentile_2": 133.4, "percentile_98": 516.4}
        })

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        assert await backends.item_data_range("c", "i", "data") == (133.4, 516.4)

    await backends.aclose()


async def test_item_data_range_falls_back_to_min_max():
    backends = Backends(_settings())

    def _router(request: httpx.Request, **kw):
        return make_response(request, 200, {"data_b1": {"min": 1.0, "max": 9.0}})

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        assert await backends.item_data_range("c", "i", "data") == (1.0, 9.0)

    await backends.aclose()


async def test_item_data_range_is_cached_including_failures(no_sleep):
    backends = Backends(_settings())
    send = AsyncMock(side_effect=_sequence_router(404, 404, 404))

    with patch("httpx.AsyncClient.send", new=send):
        assert await backends.item_data_range("c", "i", "data") is None
        assert await backends.item_data_range("c", "i", "data") is None

    assert send.call_count == 1
    await backends.aclose()


async def test_item_data_range_tolerates_a_useless_payload():
    backends = Backends(_settings())

    def _router(request: httpx.Request, **kw):
        return make_response(request, 200, {"data_b1": {"histogram": [[1], [2]]}})

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)):
        assert await backends.item_data_range("c", "i", "data") is None

    await backends.aclose()


# ---------------------------------------------------------------------------
# Collection enumeration
# ---------------------------------------------------------------------------


async def test_list_all_collections_rejects_a_malformed_first_page():
    """An unexpected payload must not read as 'this catalogue is empty'."""
    backend = HttpBackend("STAC", "https://fake.example", _settings(), "EO_MCP_STAC_URL")

    def _router(request: httpx.Request, **kw):
        return make_response(request, 200, {"unexpected": True})

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(
        ToolError, match="missing the expected 'collections' field"
    ):
        await list_all_collections(backend, {})

    await backend.aclose()


async def test_list_all_collections_sends_an_explicit_limit():
    """The whole point: never rely on the backend's default page size."""
    backend = HttpBackend("STAC", "https://fake.example", _settings(), "EO_MCP_STAC_URL")
    send = AsyncMock(side_effect=lambda request, **kw: make_response(
        request, 200, {"collections": [{"id": "a"}], "links": []}
    ))

    with patch("httpx.AsyncClient.send", new=send):
        await list_all_collections(backend, {})

    assert "limit=100" in str(send.call_args.args[0].url)
    await backend.aclose()


# ---------------------------------------------------------------------------
# Error messages name the environment variable that configures the backend
# ---------------------------------------------------------------------------


async def test_http_error_names_the_env_var():
    """A 404 from a misconfigured backend is indistinguishable from a real 404.

    Pointing EO_MCP_RASTER_URL at the tipg endpoint yields a plain 404 for a
    collection that does exist. Naming the variable turns that into a
    one-round-trip diagnosis.
    """
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    def _router(request: httpx.Request, **kw):
        return make_response(request, 404, text="not found")

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(
        ToolError, match="EO_MCP_RASTER_URL"
    ):
        await backend.request("GET", "/collections/precipitation-daily")

    await backend.aclose()


async def test_unreachable_error_names_the_env_var(no_sleep):
    backend = HttpBackend("vector", "https://fake.example/vector", _settings(), "EO_MCP_VECTOR_URL")

    def _router(request: httpx.Request, **kw):
        raise httpx.ConnectError("refused", request=request)

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(
        ToolError, match="EO_MCP_VECTOR_URL names the right host"
    ):
        await backend.request("GET", "/collections")

    await backend.aclose()


async def test_read_timeout_error_names_the_env_var():
    backend = HttpBackend("STAC", "https://fake.example", _settings(), "EO_MCP_STAC_URL")

    def _router(request: httpx.Request, **kw):
        raise httpx.ReadTimeout("slow", request=request)

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(
        ToolError, match=r"STAC backend \(EO_MCP_STAC_URL\)"
    ):
        await backend.request("GET", "/collections")

    await backend.aclose()


def test_backends_wire_the_right_env_var_to_each_client():
    backends = Backends(_settings())

    assert backends.raster.env_var == "EO_MCP_RASTER_URL"
    assert backends.vector.env_var == "EO_MCP_VECTOR_URL"
    assert backends.stac.env_var == "EO_MCP_STAC_URL"


async def test_oversize_message_recommends_a_tolerance_that_keeps_geometry():
    """The advice in this message must not itself destroy data.

    An earlier version recommended simplify=0.001, which nulls 61% of features
    in public.flood_extent_emsr864 — measured live 2026-07-31.
    """
    backend = HttpBackend("raster", "https://fake.example/raster", _settings(), "EO_MCP_RASTER_URL")

    def _router(request: httpx.Request, **kw):
        return make_response(request, 413, text="too large")

    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=_router)), pytest.raises(
        ToolError
    ) as excinfo:
        await backend.request("POST", "/statistics", json={"type": "Feature"}, retry_safe=True)

    message = str(excinfo.value)
    assert "simplify=0.0001" in message
    assert "simplify=0.001)" not in message
    assert "null geometry for 61%" in message
    await backend.aclose()


@pytest.mark.parametrize(
    "rescale,data_range,expected,why",
    [
        # Live ranges from api.cheias.pt, 2026-07-31. Containment is the only
        # question here: a wider-than-needed ramp is kept deliberately.
        ("0,100", (0.0, 20.21), True, "precondition: dim but correct and comparable"),
        ("0.05,0.5", (0.26, 0.47), True, "soil-moisture-daily"),
        ("-20,20", (-9.59, 17.62), True, "wind-u"),
        ("-3,3", (-1.57, 2.98), True, "sst-anomaly"),
        ("0,80", (133.44, 516.47), False, "precipitation-daily: entirely out of range"),
        ("0,500", (20.83, 524.52), False, "ivt: overflows the top"),
        ("97000,103000", (98405.62, 103209.62), False, "mslp: overflows the top"),
    ],
)
def test_rescale_covers_is_containment_only(rescale, data_range, expected, why):
    assert rescale_covers(rescale, data_range) is expected, why


@pytest.mark.parametrize(
    "rescale,data_range,expected,why",
    [
        # Same collection, three real items. Occupancy tracks how wet the day
        # was, which is the information the shared ramp exists to convey.
        ("0,80", (0.0, 9.0), True, "2026-02-15: 11% — a dry day"),
        ("0,80", (0.63, 16.15), True, "2026-02-08: 19%"),
        ("0,80", (2.5, 34.85), False, "2026-02-01: 40% — a wet day"),
        ("0,100", (0.0, 20.21), True, "precondition: 20%"),
        ("-20,20", (-9.59, 17.62), False, "wind-u: 68%"),
        ("5,5", (5.0, 5.0), False, "zero-width ramp is not low contrast, it is invalid"),
        (None, (1.0, 2.0), False, "nothing to measure"),
        ("0,80", None, False, "nothing to measure"),
    ],
)
def test_rescale_low_contrast_is_advisory(rescale, data_range, expected, why):
    """Flags dim tiles without changing them.

    Overriding here would rescale each item to its own percentiles, so
    2026-02-15 (a dry day, 11% of the ramp) and 2026-02-01 (a wet day, 40%)
    would render identically and a reader comparing them would conclude the
    drought was a flood. Worse than low contrast, so it reports instead.
    """
    assert rescale_low_contrast(rescale, data_range) is expected, why
