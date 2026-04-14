from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    raster_url: str = "https://api.cheias.pt/raster"
    stac_url: str = "https://api.cheias.pt"
    vector_url: str = "https://api.cheias.pt/vector"
    auth_token: str = ""
    request_timeout: float = 30.0

    model_config = SettingsConfigDict(env_prefix="EO_MCP_", extra="ignore")
