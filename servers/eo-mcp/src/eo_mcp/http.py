"""HTTP plumbing shared by every tool: one retrying client per backend.

Error contract
==============

**Every failure raises** :class:`fastmcp.exceptions.ToolError`. No tool returns
an ``{"error": ..., "status": ...}`` dictionary.

The reason is a protocol detail with real consequences. A returned dictionary is
a *successful* tool result as far as MCP is concerned, so a model reading it has
no signal that anything went wrong and will happily narrate the contents as
fact. ``ToolError`` marks the result as an error, which both the client and the
model can see. Every message carries the status code, the method, the full
request URL, and the backend's own response body, so the failure is actionable
without a second round trip.

Four failure classes, all of them ``ToolError``:

1. **Invalid argument** — rejected before any HTTP call is made.
2. **Backend HTTP error** — any status >= 400 that survived the retry policy.
3. **Transport failure** — connect error or read timeout, after retries.
4. **Malformed payload** — a 200 response missing a key the tool needs, or a
   body that is not JSON. Previously these surfaced as a bare ``KeyError``.

The three workflow tools aggregate over many backend calls, and for those a
*per-item* failure is the tool's product rather than a tool failure. They report
per-item errors in band, alongside an explicit ``partial_failures`` count, and
raise only when they can produce no useful result at all. That boundary is
documented on each tool.

Retry policy
============

Retries fire on connect failures, 429, and 5xx — never on a 4xx, which will fail
identically however many times it is repeated. Only requests declared safe to
repeat are retried: GET/HEAD/OPTIONS by default, plus the read-only
``POST /statistics`` calls which opt in explicitly.

**A read timeout is not retried.** This is deliberate and was measured. A backend
that accepts the socket and then never answers costs a full ``request_timeout``
per attempt, so retrying turns the default 30s budget into ~92s of silence for a
single tool call — past the point where MCP clients give up and well past where a
user assumes the server is broken. It is also the least likely retry to succeed:
a backend hung on a slow query hangs the same way next time. A *connect* failure
is the opposite — it fails in ``connect_timeout`` (5s by default) and is exactly
the transient a retry fixes — so those are retried.

Backoff is exponential with full jitter, so that the workflow tools' concurrent
fan-out does not resynchronise into a thundering herd on the way back up. A
``Retry-After`` header, when present, wins over the computed delay.
"""

from __future__ import annotations

import asyncio
import json as _json
import random
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode

import httpx
from fastmcp.exceptions import ToolError

from .config import Settings

#: Statuses worth repeating: rate limiting and server-side faults.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: Methods that are safe to repeat without an explicit opt-in.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Transport failures worth repeating. Connect-side only: these fail within
#: ``connect_timeout`` and are the transient a retry actually fixes. Read and
#: write timeouts are excluded — see the retry-policy note in the module
#: docstring for the measurement behind that.
_RETRYABLE_TRANSPORT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
)

#: Statuses that mean "the request body was too large for the backend or an
#: intermediary". Cloudflare and nginx disagree about which one to send, and
#: both answer with HTML or plain text rather than JSON.
_OVERSIZE_STATUS = frozenset({403, 413})

#: Backend bodies go into error messages; keep them bounded.
_BODY_LIMIT = 500

#: Ceiling on a single backoff sleep, including a server-supplied Retry-After.
_MAX_BACKOFF = 10.0


def _truncate(text: str | None) -> str:
    if not text:
        return "(empty response body)"
    text = " ".join(text.split())
    if len(text) <= _BODY_LIMIT:
        return text
    return text[:_BODY_LIMIT] + f"… ({len(text)} bytes total)"


def _parse_retry_after(value: str | None) -> float | None:
    """Read a ``Retry-After`` delay in seconds, ignoring the HTTP-date form."""
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class HttpBackend:
    """A retrying, concurrency-bounded HTTP client for one backend.

    The underlying :class:`httpx.AsyncClient` is created on first use and closed
    by :meth:`aclose`. Instances are owned by a :class:`Backends` container whose
    lifetime is the server's, so there is no module-level state and no question
    of which settings a client was built from.
    """

    def __init__(self, name: str, base_url: str, settings: Settings, env_var: str) -> None:
        self.name = name
        self.base_url = base_url
        # Named in every failure message. Pointing EO_MCP_RASTER_URL at a tipg
        # endpoint produces a plain 404, and without the variable name a reader
        # has no way to tell a misconfiguration from a missing collection.
        self.env_var = env_var
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._gate = asyncio.Semaphore(settings.max_concurrency)

    # -- lifecycle ---------------------------------------------------------

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    headers = {}
                    if self._settings.auth_token:
                        headers["Authorization"] = f"Bearer {self._settings.auth_token}"
                    self._client = httpx.AsyncClient(
                        base_url=self.base_url,
                        timeout=httpx.Timeout(
                            connect=self._settings.connect_timeout,
                            read=self._settings.request_timeout,
                            write=self._settings.request_timeout,
                            pool=self._settings.connect_timeout,
                        ),
                        headers=headers,
                    )
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    # -- request -----------------------------------------------------------

    def url_for(self, path: str, params: Any = None) -> str:
        """The absolute URL a request would use, for error messages and links."""
        url = self.base_url + path
        if params:
            pairs = params.items() if isinstance(params, dict) else params
            url += "?" + urlencode(list(pairs))
        return url

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        retry_safe: bool | None = None,
    ) -> Any:
        """Perform a request, retrying transient failures, and return parsed JSON.

        Raises :class:`ToolError` on any failure. ``retry_safe`` overrides the
        method-based default; pass ``True`` for a POST that is semantically a
        read.
        """
        client = await self.client()
        method = method.upper()
        if retry_safe is None:
            retry_safe = method in _IDEMPOTENT_METHODS

        url = self.url_for(path, params)
        attempts = self._settings.max_retries + 1 if retry_safe else 1
        last_transport_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                async with self._gate:
                    response = await client.request(
                        method, path, params=params, json=json
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_transport_error = exc
                retryable = isinstance(exc, _RETRYABLE_TRANSPORT)
                if retryable and attempt < attempts:
                    await self._backoff(attempt, None)
                    continue
                if isinstance(exc, httpx.TimeoutException) and not retryable:
                    raise ToolError(
                        f"eo-mcp: {self.name} backend ({self.env_var}) accepted "
                        f"the connection but did not answer {method} {url} within "
                        f"{self._settings.request_timeout}s "
                        f"({type(exc).__name__}). Not retried: a backend hung on "
                        f"a slow query hangs the same way again, and retrying "
                        f"would multiply the wait. Narrow the request, or raise "
                        f"EO_MCP_REQUEST_TIMEOUT if the work is genuinely this "
                        f"slow."
                    ) from exc
                raise ToolError(
                    f"eo-mcp: {self.name} backend is unreachable — "
                    f"{method} {url} failed after {attempt} attempt(s) with "
                    f"{type(exc).__name__}: {exc}. Check that {self.env_var} "
                    f"names the right host and that the service is running."
                ) from exc

            status = response.status_code
            if status >= 400:
                if status in RETRYABLE_STATUS and attempt < attempts:
                    await self._backoff(
                        attempt, _parse_retry_after(response.headers.get("retry-after"))
                    )
                    continue
                if status in _OVERSIZE_STATUS and json is not None:
                    raise ToolError(self._oversize_message(method, url, status, json, response))
                raise ToolError(
                    f"eo-mcp: {self.name} backend returned HTTP {status} for "
                    f"{method} {url} — {_truncate(response.text)} "
                    f"(this backend is configured by {self.env_var})"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise ToolError(
                    f"eo-mcp: {self.name} backend returned HTTP {status} for "
                    f"{method} {url} but the body is not JSON — "
                    f"{_truncate(response.text)}"
                ) from exc

        # Unreachable: the loop either returns or raises on its last attempt.
        raise ToolError(  # pragma: no cover - defensive
            f"eo-mcp: {self.name} backend request {method} {url} exhausted "
            f"{attempts} attempt(s): {last_transport_error}"
        )

    def _oversize_message(
        self, method: str, url: str, status: int, body: Any, response: Any
    ) -> str:
        """Explain a rejected-because-too-big request in terms the caller can act on.

        The backend and the edge in front of it disagree about which status to
        use — nginx sends 413, Cloudflare sends 403 — and neither answers with
        JSON, so the raw body is a chunk of HTML or ``error code: 1010``. That
        tells a reader nothing. This names the size actually sent and the lever
        that reduces it.
        """
        try:
            sent = len(_json.dumps(body).encode())
            size = f"{sent:,} bytes"
        except (TypeError, ValueError):  # pragma: no cover - body is always JSON here
            size = "an unknown number of bytes"
        return (
            f"eo-mcp: {self.name} backend rejected {method} {url} with HTTP "
            f"{status} — the request body ({size}) is larger than the backend or "
            f"its proxy accepts. This happens with detailed flood polygons: the "
            f"geometry, not the request, is what is large. Fetch it with a "
            f"simplify tolerance — get_vector_features(..., simplify=0.0001) cuts "
            f"a coordinate-dense polygon by roughly 6x (~11 m on the ground, well "
            f"under one pixel of a 0.02-degree grid such as precipitation-daily). "
            f"Do not reach for a larger tolerance without checking the result: at "
            f"simplify=0.001, tipg returns null geometry for 61% of features in "
            f"public.flood_extent_emsr864 — measured 2026-07-31 — with HTTP 200 "
            f"and properties intact, so the loss is silent. Backend said: "
            f"{_truncate(response.text)}"
        )

    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            await asyncio.sleep(min(retry_after, _MAX_BACKOFF))
            return
        ceiling = min(self._settings.retry_backoff * (2 ** (attempt - 1)), _MAX_BACKOFF)
        if ceiling <= 0:
            return
        # Full jitter: concurrent callers that failed together must not retry
        # together.
        await asyncio.sleep(random.uniform(0, ceiling))


def require(payload: Any, key: str, *, source: str, url: str) -> Any:
    """Read ``key`` from a backend payload or raise a ``ToolError`` that says so.

    Turns an unexpected response shape into a message naming the missing key and
    the URL it came from, instead of a ``KeyError`` with no context.
    """
    if not isinstance(payload, dict) or key not in payload:
        found = (
            ", ".join(sorted(payload)) if isinstance(payload, dict) else type(payload).__name__
        )
        raise ToolError(
            f"eo-mcp: {source} response from {url} is missing the expected "
            f"{key!r} field. Got: {found or '(empty object)'}."
        )
    return payload[key]


class Backends:
    """The three backend clients, owned by one server instance.

    Created in :func:`eo_mcp.server.build_server` and closed by the server's
    lifespan. Clients are built lazily on first use so that tools remain
    callable in tests and embedded uses that never start a lifespan.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.raster = HttpBackend("raster", settings.raster_url, settings, "EO_MCP_RASTER_URL")
        self.vector = HttpBackend("vector", settings.vector_url, settings, "EO_MCP_VECTOR_URL")
        self.stac = HttpBackend("STAC", settings.stac_url, settings, "EO_MCP_STAC_URL")
        # STAC collection metadata is styling configuration: static for the life
        # of a server process. Caching it keeps get_tile_url to one round trip
        # per collection rather than one per call. ``None`` is cached too, so a
        # collection without render hints does not re-probe forever.
        self._render_defaults: dict[str, dict[str, Any]] = {}
        self._render_lock = asyncio.Lock()
        # Per-item data range, used to check that a collection-level rescale
        # actually fits the item it is about to be applied to. Cheap to fetch
        # (~650 bytes) and static, so one call per item per process.
        self._item_range: dict[tuple[str, str, str], tuple[float, float] | None] = {}
        self._item_range_lock = asyncio.Lock()

    def all(self) -> tuple[HttpBackend, ...]:
        return (self.raster, self.vector, self.stac)

    async def aclose(self) -> None:
        for backend in self.all():
            await backend.aclose()

    async def item_data_range(
        self, collection_id: str, item_id: str, asset: str
    ) -> tuple[float, float] | None:
        """The item's 2nd–98th percentile range, or ``None`` if unavailable.

        Percentiles rather than min/max: a single outlier pixel should not
        define the display stretch. Used only to sanity-check a collection-level
        rescale default — never a reason to fail a request.
        """
        key = (collection_id, item_id, asset)
        if key in self._item_range:
            return self._item_range[key]

        async with self._item_range_lock:
            if key in self._item_range:
                return self._item_range[key]
            data_range: tuple[float, float] | None = None
            try:
                payload = await self.raster.request(
                    "GET",
                    f"/collections/{collection_id}/items/{item_id}/statistics",
                    params={"assets": asset},
                )
            except ToolError:
                payload = None
            if isinstance(payload, dict) and payload:
                band = next(iter(payload.values()), None)
                if isinstance(band, dict):
                    lo = band.get("percentile_2", band.get("min"))
                    hi = band.get("percentile_98", band.get("max"))
                    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                        data_range = (float(lo), float(hi))
            self._item_range[key] = data_range
            return data_range

    async def render_defaults(self, collection_id: str) -> dict[str, Any]:
        """Render hints published by a STAC collection, or an empty dict.

        Reads the ``cheias:rescale`` and ``cheias:colormap`` properties that the
        cheias.pt deployment publishes at collection top level. A collection
        without them, or a STAC backend that is unreachable, yields ``{}`` —
        this is a convenience, never a reason to fail a tile request.
        """
        cached = self._render_defaults.get(collection_id)
        if cached is not None:
            return cached

        async with self._render_lock:
            cached = self._render_defaults.get(collection_id)
            if cached is not None:
                return cached
            try:
                payload = await self.stac.request(
                    "GET", f"/collections/{collection_id}"
                )
            except ToolError:
                payload = {}
            defaults: dict[str, Any] = {}
            if isinstance(payload, dict):
                rescale = _format_rescale(payload.get("cheias:rescale"))
                if rescale is not None:
                    defaults["rescale"] = rescale
                colormap = payload.get("cheias:colormap")
                if isinstance(colormap, str) and colormap:
                    defaults["colormap"] = colormap
            self._render_defaults[collection_id] = defaults
            return defaults


def _format_rescale(value: Any) -> str | None:
    """Turn a STAC ``cheias:rescale`` value into TiTiler's ``"min,max"`` form.

    The collections publish a two-element list of numbers; TiTiler's ``rescale``
    query parameter wants a comma-separated string. Anything else is ignored
    rather than guessed at.
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = value
        # bool is a subclass of int; [True, False] is not a rescale range.
        numeric = (int, float)
        if all(isinstance(v, numeric) and not isinstance(v, bool) for v in (lo, hi)):
            return f"{lo},{hi}"
    return None


#: Page size when enumerating collections. Both backends default to 10, which
#: silently truncates the listing; asking explicitly avoids reporting a subset
#: of the catalogue as if it were the whole thing.
COLLECTION_PAGE_SIZE = 100

#: Hard stop on pagination so a backend with a broken `next` link cannot spin.
MAX_COLLECTION_PAGES = 20


async def list_all_collections(backend: HttpBackend, params: dict[str, Any]) -> list[dict]:
    """Enumerate every collection, following pagination to the end.

    eoAPI's STAC and tipg both page ``/collections`` at ten by default and
    advertise the rest behind a ``next`` link. Reading only the first page drops
    collections without saying so — this catalogue has 11 STAC collections and
    an unpaginated read returns 10, silently omitting ``wind-v``.
    """
    collections: list[dict] = []
    offset = 0
    for page_number in range(MAX_COLLECTION_PAGES):
        request_params = {**params, "limit": COLLECTION_PAGE_SIZE, "offset": offset}
        page = await backend.request("GET", "/collections", params=request_params)
        if page_number == 0:
            # An unexpected payload must not read as "this catalogue is empty".
            require(
                page,
                "collections",
                source=f"{backend.name} collections",
                url=backend.url_for("/collections", request_params),
            )
        batch = page.get("collections") or [] if isinstance(page, dict) else []
        collections.extend(batch)
        links = page.get("links") or [] if isinstance(page, dict) else []
        has_next = any(
            isinstance(link, dict) and link.get("rel") == "next" for link in links
        )
        if not batch or not has_next:
            break
        offset += len(batch)
    return collections


def parse_rescale(value: str | None) -> tuple[float, float] | None:
    """Read TiTiler's ``"min,max"`` rescale form back into numbers."""
    if not value:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


#: Occupancy below which a collection ramp is worth remarking on: the tile will
#: render with visibly few tones. This is *advisory only* — see
#: :func:`rescale_low_contrast` for why it must not trigger an override.
LOW_CONTRAST_OCCUPANCY = 1 / 3


def rescale_covers(rescale: str | None, data_range: tuple[float, float] | None) -> bool:
    """Whether a rescale range spans the data it will be applied to.

    Containment, and deliberately nothing more. ``precipitation-daily`` publishes
    ``[0, 80]`` while its ``precipitation-storm-total`` item runs 100–582, so
    every pixel clamps to the top of the ramp and the tile renders as one flat
    colour with a 200 and no warning. That is unambiguously broken and is worth
    overriding.

    A ramp that is merely *wider* than the item's data is not broken, and must
    not be overridden — see :func:`rescale_low_contrast`.
    """
    bounds = parse_rescale(rescale)
    if bounds is None or data_range is None:
        # Nothing to check against; assume the curated value is intended.
        return True
    lo, hi = bounds
    return lo <= data_range[0] and hi >= data_range[1]


def rescale_low_contrast(
    rescale: str | None, data_range: tuple[float, float] | None
) -> bool:
    """Whether the data occupies so little of the ramp that contrast suffers.

    Advisory. It must not change the applied rescale, because a wide
    collection-level ramp is usually **the point**: it makes sibling items
    comparable to each other. Measured on ``precipitation-daily``, whose ramp is
    ``[0, 80]`` (2026-07-31):

    ==================================  ==========  =========
    item                                p2–p98      occupancy
    ==================================  ==========  =========
    precipitation-daily-2026-02-15      0.0–9.0     11%
    precipitation-daily-2026-02-08      0.6–16.2    19%
    precipitation-daily-2026-02-01      2.5–34.9    40%
    ==================================  ==========  =========

    A dry day occupying 11% of the ramp *should* render dim — that is exactly
    what makes it comparable to a wet one. Rescaling each item to its own
    percentiles would render the driest and wettest days identically, and a
    reader comparing two tiles would conclude the drought was a flood. That is a
    far worse failure than low contrast, and it is the failure this repository
    exists to prevent, so the caller is told rather than overruled.
    """
    bounds = parse_rescale(rescale)
    if bounds is None or data_range is None:
        return False
    ramp = bounds[1] - bounds[0]
    if ramp <= 0:
        return False
    return (data_range[1] - data_range[0]) / ramp < LOW_CONTRAST_OCCUPANCY


def backends_lifespan(backends: Backends):
    """Build a FastMCP lifespan that closes ``backends`` on server shutdown."""

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {"backends": backends}
        finally:
            await backends.aclose()

    return lifespan
