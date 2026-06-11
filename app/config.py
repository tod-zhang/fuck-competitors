"""Runtime configuration. Override any field via env vars prefixed with FC_ (or a .env file)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_url: str = "sqlite:///./data/app.db"
    default_interval_hours: int = 24
    user_agent: str = "FuckCompetitors/0.1 (+https://github.com/cowseal/fuck-competitors)"
    request_timeout: int = 20
    respect_robots: bool = True    # obey robots.txt; turn off only if a target's robots wrongly blocks its sitemap
    crawl_delay_seconds: float = 1.0   # min spacing between requests to the same host (robots Crawl-delay can raise it)
    block_cooldown_seconds: int = 900  # after a 403 / 429-without-Retry-After, skip that host for this long
    max_sitemap_urls: int = 50_000
    snapshot_retention: int = 10  # detailed-monitoring snapshots kept per page
    detailed_max_pages: int = 500  # cap pages content-diffed per detailed crawl (safety for huge sites)
    write_batch: int = 200  # commit crawl writes every N rows so the write lock is released often

    # MCP server (app/mcp_server.py)
    mcp_transport: str = "stdio"   # "http" to serve over a URL (e.g. for remote AI clients)
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 9528
    mcp_token: str = ""            # if set, require "Authorization: Bearer <token>" — use when exposed publicly

    model_config = SettingsConfigDict(env_prefix="FC_", env_file=".env", extra="ignore")


settings = Settings()
