"""Runtime configuration. Override any field via env vars prefixed with FC_ (or a .env file)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    db_url: str = "sqlite:///./data/app.db"
    default_interval_hours: int = 24
    user_agent: str = "FuckCompetitors/0.1 (+https://github.com/tod-zhang/fuck-competitors)"
    request_timeout: int = 20
    respect_robots: bool = True    # obey robots.txt; turn off only if a target's robots wrongly blocks its sitemap
    crawl_delay_seconds: float = 1.0   # min spacing between requests to the same host (robots Crawl-delay can raise it)
    block_cooldown_seconds: int = 900  # after a 403 / 429-without-Retry-After, skip that host for this long
    max_sitemap_urls: int = 50_000
    snapshot_retention: int = 10  # detailed-monitoring snapshots kept per page
    detailed_max_pages: int = 500  # cap pages content-diffed per detailed crawl (safety for huge sites)
    write_batch: int = 200  # commit crawl writes every N rows so the write lock is released often
    demo_mode: bool = False  # read-only showcase: fake data reseeded daily, no crawling, all writes rejected

    # MCP server (app/mcp_server.py)
    mcp_transport: str = "stdio"   # "http" to serve over a URL (e.g. for remote AI clients)
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 9528
    mcp_token: str = ""            # if set, require "Authorization: Bearer <token>" — use when exposed publicly

    # Content feed + AI features (app/feed.py, app/integrations.py). Each one is off until its key is set.
    my_domain: str = ""            # your own site, for competitor suggestions (editable in the feed view)
    my_sitemap_url: str = ""       # your blog's sitemap, for "closest post on my blog" (editable in the feed view)
    my_site_max_pages: int = 300   # cap on how many of your own posts get fetched + embedded
    feed_seed_days: int = 30       # first crawl seeds the feed with pages whose <lastmod> is this recent
    feed_enrich_batch: int = 30    # titles fetched per check for new feed items (basic-monitoring competitors)
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    dataforseo_location_code: int = 2840   # 2840 = United States
    dataforseo_language_code: str = "en"
    embedding_base_url: str = "https://openrouter.ai/api/v1"
    embedding_api_key: str = ""
    embedding_model: str = "qwen/qwen3-embedding-8b"
    embedding_providers: str = ""  # optional OpenRouter provider order, comma-separated (e.g. "nebius,deepinfra")
    llm_base_url: str = "https://api.deepseek.com/v1"   # any OpenAI-compatible chat API
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    model_config = SettingsConfigDict(env_prefix="FC_", env_file=".env", extra="ignore")


settings = Settings()
