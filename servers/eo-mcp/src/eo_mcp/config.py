"""Runtime configuration, validated at construction.

Every setting is read from an ``EO_MCP_``-prefixed environment variable. The
validators here exist so that a typo in a ``.env`` file fails immediately with a
message naming the offending variable, rather than surfacing later as a
confusing connection error in the middle of a tool call.

A ``.env`` file in the working directory is read, so the "copy ``.env.example``
to ``.env``" instruction in the README actually works. Real environment
variables win over the file.

Two deliberate choices:

* **Trailing slashes are normalised away, not rejected.** ``https://host/raster``
  and ``https://host/raster/`` are the same backend, and rejecting one of them
  would be pedantry.
* **``http://`` is allowed without complaint.** People run local TiTiler and tipg
  instances. The scheme is never silently rewritten to ``https://`` — if you ask
  for plaintext you get plaintext.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_URL_FIELDS = ("raster_url", "stac_url", "vector_url")


class ConfigurationError(RuntimeError):
    """Raised by :meth:`Settings.load` when the environment is misconfigured."""


def _env_var(field_name: str) -> str:
    return f"EO_MCP_{field_name.upper()}"


class Settings(BaseSettings):
    """Backend endpoints and HTTP behaviour.

    The three URL settings are base URLs: the tools append paths to them. The
    timeout is split so that a slow zonal-statistics query is not killed by a
    budget tuned for establishing a TCP connection.
    """

    raster_url: str = "https://api.cheias.pt/raster"
    stac_url: str = "https://api.cheias.pt"
    vector_url: str = "https://api.cheias.pt/vector"
    auth_token: str = ""

    # Time allowed to establish a connection. Short: a backend that is not
    # answering its socket is not going to start answering in 30 seconds.
    connect_timeout: float = 5.0
    # Time allowed for the backend to produce a response body. Generous:
    # zonal statistics over a large polygon is genuinely slow work.
    request_timeout: float = 30.0

    # Retries *after* the first attempt, so the default is three attempts total.
    max_retries: int = 2
    # Base for exponential backoff, in seconds.
    retry_backoff: float = 0.5
    # Maximum in-flight requests per backend. Bounds the fan-out of the
    # workflow tools so a retry storm cannot become a thundering herd.
    max_concurrency: int = 5

    model_config = SettingsConfigDict(
        env_prefix="EO_MCP_",
        extra="ignore",
        # Without this pydantic-settings never opens a dotenv, and the
        # "copy .env.example to .env" instruction in the README silently does
        # nothing — the user keeps querying the demo backend believing they
        # are pointed at their own.
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @field_validator(*_URL_FIELDS)
    @classmethod
    def _validate_url(cls, value: str, info) -> str:
        env = _env_var(info.field_name)
        raw = value.strip()
        if not raw:
            raise ValueError(
                f"{env} is empty. Set it to a base URL, for example "
                f"https://api.cheias.pt/raster"
            )

        parts = urlsplit(raw)
        if not parts.scheme:
            raise ValueError(
                f"{env} is {value!r}, which has no URL scheme. Prefix it with "
                f"https:// (or http:// for a local backend)."
            )
        if parts.scheme not in ("http", "https"):
            raise ValueError(
                f"{env} is {value!r}, whose scheme {parts.scheme!r} is not "
                f"supported. Use http:// or https://."
            )
        if not parts.netloc:
            raise ValueError(
                f"{env} is {value!r}, which has no host. Expected something "
                f"like https://api.cheias.pt/raster."
            )
        if parts.query or parts.fragment:
            raise ValueError(
                f"{env} is {value!r}. A base URL must not carry a query string "
                f"or a fragment; the tools append their own."
            )

        # Normalise the trailing slash away so path joining is unambiguous.
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

    @field_validator("connect_timeout", "request_timeout")
    @classmethod
    def _validate_timeout(cls, value: float, info) -> float:
        if value <= 0:
            raise ValueError(
                f"{_env_var(info.field_name)} is {value}, but a timeout must be "
                f"greater than zero."
            )
        return value

    @field_validator("max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                f"EO_MCP_MAX_RETRIES is {value}, but it must be zero or more. "
                f"Use 0 to disable retries."
            )
        return value

    @field_validator("retry_backoff")
    @classmethod
    def _validate_retry_backoff(cls, value: float) -> float:
        if value < 0:
            raise ValueError(
                f"EO_MCP_RETRY_BACKOFF is {value}, but it must be zero or more. "
                f"Use 0 to retry without waiting."
            )
        return value

    @field_validator("max_concurrency")
    @classmethod
    def _validate_max_concurrency(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                f"EO_MCP_MAX_CONCURRENCY is {value}, but at least one request "
                f"must be allowed in flight."
            )
        return value

    @classmethod
    def load(cls, **overrides) -> Settings:
        """Build settings from the environment, or raise a readable error.

        Pydantic's own :class:`ValidationError` is accurate but reads like a
        stack trace. This flattens it into something a person can act on
        without knowing what pydantic is.
        """
        try:
            return cls(**overrides)
        except ValidationError as exc:
            problems = []
            for err in exc.errors():
                field = str(err["loc"][0]) if err["loc"] else "?"
                message = err["msg"].removeprefix("Value error, ")
                # Validators already name their env var; bare pydantic errors
                # (a non-numeric timeout, say) do not.
                if _env_var(field) in message:
                    problems.append(message)
                else:
                    problems.append(f"{_env_var(field)}: {message}")
            joined = "\n  - ".join(problems)
            raise ConfigurationError(
                f"eo-mcp configuration is invalid:\n  - {joined}"
            ) from exc
