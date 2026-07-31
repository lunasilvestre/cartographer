"""Configuration validation.

A typo in a .env file should fail at startup with a message naming the variable,
not three tool calls later as a confusing connection error.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from eo_mcp.config import ConfigurationError, Settings


def test_defaults_point_at_the_public_deployment():
    settings = Settings()
    assert settings.raster_url == "https://api.cheias.pt/raster"
    assert settings.stac_url == "https://api.cheias.pt"
    assert settings.vector_url == "https://api.cheias.pt/vector"


@pytest.mark.parametrize("field", ["raster_url", "stac_url", "vector_url"])
def test_empty_url_is_rejected_and_names_the_env_var(field: str):
    with pytest.raises(ValidationError, match=f"EO_MCP_{field.upper()} is empty"):
        Settings(**{field: ""})


@pytest.mark.parametrize("field", ["raster_url", "stac_url", "vector_url"])
def test_whitespace_only_url_is_rejected(field: str):
    with pytest.raises(ValidationError, match="is empty"):
        Settings(**{field: "   "})


def test_url_without_scheme_is_rejected():
    with pytest.raises(ValidationError, match="EO_MCP_RASTER_URL.*no URL scheme"):
        Settings(raster_url="api.cheias.pt/raster")


def test_url_with_unsupported_scheme_is_rejected():
    with pytest.raises(ValidationError, match="EO_MCP_VECTOR_URL.*not.*supported"):
        Settings(vector_url="ftp://api.cheias.pt/vector")


def test_url_without_host_is_rejected():
    with pytest.raises(ValidationError, match="EO_MCP_STAC_URL.*no host"):
        Settings(stac_url="https:///raster")


def test_url_with_query_string_is_rejected():
    with pytest.raises(ValidationError, match="must not carry a query string"):
        Settings(raster_url="https://api.cheias.pt/raster?token=abc")


def test_trailing_slash_is_normalised_not_rejected():
    """Both spellings name the same backend; rejecting one would be pedantry."""
    assert Settings(raster_url="https://api.cheias.pt/raster/").raster_url == (
        "https://api.cheias.pt/raster"
    )
    assert Settings(raster_url="https://api.cheias.pt///").raster_url == "https://api.cheias.pt"


def test_http_is_allowed_and_never_rewritten():
    """People run local backends. Plaintext is a choice, not a mistake."""
    settings = Settings(raster_url="http://localhost:8000/raster")
    assert settings.raster_url == "http://localhost:8000/raster"


def test_surrounding_whitespace_is_trimmed():
    assert Settings(vector_url="  https://host/vector  ").vector_url == "https://host/vector"


@pytest.mark.parametrize("field", ["connect_timeout", "request_timeout"])
@pytest.mark.parametrize("value", [0, -1.5])
def test_non_positive_timeout_is_rejected(field: str, value: float):
    with pytest.raises(ValidationError, match=f"EO_MCP_{field.upper()}"):
        Settings(**{field: value})


def test_negative_max_retries_is_rejected():
    with pytest.raises(ValidationError, match="EO_MCP_MAX_RETRIES is -1"):
        Settings(max_retries=-1)


def test_zero_max_retries_is_allowed():
    assert Settings(max_retries=0).max_retries == 0


def test_negative_retry_backoff_is_rejected():
    with pytest.raises(ValidationError, match="EO_MCP_RETRY_BACKOFF"):
        Settings(retry_backoff=-0.1)


def test_zero_max_concurrency_is_rejected():
    with pytest.raises(ValidationError, match="EO_MCP_MAX_CONCURRENCY is 0"):
        Settings(max_concurrency=0)


# ---------------------------------------------------------------------------
# Settings.load
# ---------------------------------------------------------------------------


def test_load_reads_the_environment(monkeypatch):
    monkeypatch.setenv("EO_MCP_RASTER_URL", "https://elsewhere.test/raster/")
    monkeypatch.setenv("EO_MCP_MAX_RETRIES", "4")

    settings = Settings.load()

    assert settings.raster_url == "https://elsewhere.test/raster"
    assert settings.max_retries == 4


def test_load_raises_a_readable_error(monkeypatch):
    monkeypatch.setenv("EO_MCP_RASTER_URL", "not-a-url")

    with pytest.raises(ConfigurationError) as excinfo:
        Settings.load()

    message = str(excinfo.value)
    assert "eo-mcp configuration is invalid" in message
    assert "EO_MCP_RASTER_URL" in message
    # The pydantic scaffolding is gone.
    assert "Value error," not in message


def test_load_reports_every_offending_variable_at_once(monkeypatch):
    monkeypatch.setenv("EO_MCP_RASTER_URL", "")
    monkeypatch.setenv("EO_MCP_VECTOR_URL", "ftp://nope")

    with pytest.raises(ConfigurationError) as excinfo:
        Settings.load()

    message = str(excinfo.value)
    assert "EO_MCP_RASTER_URL" in message
    assert "EO_MCP_VECTOR_URL" in message


def test_load_names_the_env_var_for_a_non_numeric_value(monkeypatch):
    """Type errors come from pydantic, not our validators, and still get named."""
    monkeypatch.setenv("EO_MCP_REQUEST_TIMEOUT", "thirty")

    with pytest.raises(ConfigurationError, match="EO_MCP_REQUEST_TIMEOUT"):
        Settings.load()


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------


def test_dotenv_file_is_read(tmp_path, monkeypatch):
    """`.env.example` tells the reader to copy it to `.env`. That must work.

    pydantic-settings only opens a dotenv when `env_file` is set. Without it the
    instruction in the README silently does nothing and the user keeps querying
    the demo backend while believing they are pointed at their own deployment.
    """
    (tmp_path / ".env").write_text(
        "EO_MCP_RASTER_URL=https://from-dotenv.example/raster\n"
        "EO_MCP_MAX_RETRIES=7\n"
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings.load()

    assert settings.raster_url == "https://from-dotenv.example/raster"
    assert settings.max_retries == 7


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EO_MCP_RASTER_URL=https://from-dotenv.example/raster\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EO_MCP_RASTER_URL", "https://from-env.example/raster")

    assert Settings.load().raster_url == "https://from-env.example/raster"


def test_dotenv_values_are_validated_too(tmp_path, monkeypatch):
    """A typo in the file the user actually edits must be caught."""
    (tmp_path / ".env").write_text("EO_MCP_VECTOR_URL=ftp://nope\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigurationError, match="EO_MCP_VECTOR_URL"):
        Settings.load()


def test_absent_dotenv_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert Settings.load().raster_url == "https://api.cheias.pt/raster"
