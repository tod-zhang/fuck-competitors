"""Thin clients for the paid / AI services behind the content feed.

- DataForSEO Labs: organic competitors of my domain, keywords a page ranks for, related keywords.
- Embeddings: any OpenAI-compatible /embeddings endpoint (default OpenRouter + qwen3-embedding-8b).
- Chat: any OpenAI-compatible /chat/completions endpoint (default DeepSeek), or the Claude API.

Every client raises NotConfigured when its key is missing, so the UI can say so instead of erroring.
"""
from __future__ import annotations

import math

import httpx

from .config import settings

DFS_BASE = "https://api.dataforseo.com/v3/dataforseo_labs/google"


# Settings that can be filled in from the UI (设置). A value saved there wins over the env / .env.
UI_KEYS = ("dataforseo_login", "dataforseo_password", "dataforseo_location_code", "dataforseo_language_code",
           "embedding_base_url", "embedding_api_key", "embedding_model", "embedding_providers",
           "llm_provider", "llm_base_url", "llm_api_key", "llm_model")
SECRET_KEYS = ("dataforseo_password", "embedding_api_key", "llm_api_key")


def cfg(key: str) -> str:
    from sqlmodel import Session

    from .db import engine
    from .models import AppSetting

    with Session(engine) as s:
        row = s.get(AppSetting, key)
    if row is not None and row.value.strip():
        return row.value.strip()
    return str(getattr(settings, key, "") or "")


class NotConfigured(Exception):
    pass


class ApiError(Exception):
    pass


# ---------------------------------------------------------------- DataForSEO

def dataforseo_ready() -> bool:
    return bool(cfg("dataforseo_login") and cfg("dataforseo_password"))


def _dfs(endpoint: str, task: dict) -> dict:
    """POST one task to a DataForSEO Labs live endpoint and return its first result (or {})."""
    if not dataforseo_ready():
        raise NotConfigured("还没填 DataForSEO 账号，到「设置」里填")
    task = {
        "location_code": int(cfg("dataforseo_location_code") or 2840),
        "language_code": cfg("dataforseo_language_code"),
        **task,
    }
    resp = httpx.post(
        f"{DFS_BASE}/{endpoint}/live",
        json=[task],
        auth=(cfg("dataforseo_login"), cfg("dataforseo_password")),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    t = (data.get("tasks") or [{}])[0]
    if t.get("status_code") != 20000:
        raise ApiError(f"DataForSEO: {t.get('status_message') or data.get('status_message')}")
    return (t.get("result") or [{}])[0] or {}


def _kw_row(kd: dict) -> dict:
    info = kd.get("keyword_info") or {}
    props = kd.get("keyword_properties") or {}
    intent = (kd.get("search_intent_info") or {}).get("main_intent")
    return {
        "keyword": kd.get("keyword"),
        "volume": info.get("search_volume"),
        "kd": props.get("keyword_difficulty"),
        "cpc": info.get("cpc"),
        "intent": intent,
    }


def organic_competitors(domain: str, limit: int = 30, market: dict | None = None) -> list[dict]:
    result = _dfs("competitors_domain", {"target": domain, "limit": limit, "exclude_top_domains": True, **(market or {})})
    out = []
    for it in result.get("items") or []:
        organic = (it.get("metrics") or {}).get("organic") or {}
        out.append({
            "domain": it.get("domain"),
            "intersections": it.get("intersections") or 0,
            "avg_position": round(it.get("avg_position") or 0, 1),
            "etv": round(organic.get("etv") or 0),
            "keywords": organic.get("count") or 0,
        })
    return out


def ranked_keywords(page_url: str, limit: int = 20, market: dict | None = None) -> list[dict]:
    """Keywords this exact page ranks for (target must be a full URL with https://)."""
    result = _dfs("ranked_keywords", {
        "target": page_url,
        "limit": limit,
        "order_by": ["keyword_data.keyword_info.search_volume,desc"],
        **(market or {}),
    })
    rows = []
    for it in result.get("items") or []:
        row = _kw_row(it.get("keyword_data") or {})
        serp = (it.get("ranked_serp_element") or {}).get("serp_item") or {}
        row["position"] = serp.get("rank_group")
        rows.append(row)
    return rows


def related_keywords(seed: str, limit: int = 15, market: dict | None = None) -> tuple[dict | None, list[dict]]:
    """(seed keyword's own data, related keywords) — one call returns both."""
    result = _dfs("related_keywords", {
        "keyword": seed,
        "depth": 1,
        "limit": limit,
        "order_by": ["keyword_data.keyword_info.search_volume,desc"],
        **(market or {}),
    })
    seed_row = None
    if result.get("seed_keyword_data"):
        seed_row = _kw_row({"keyword": seed, **result["seed_keyword_data"]})
    rows = []
    for it in result.get("items") or []:
        kd = it.get("keyword_data") or {}
        if kd.get("keyword") == seed:
            seed_row = seed_row or _kw_row(kd)
            continue
        rows.append(_kw_row(kd))
    return seed_row, rows


# ---------------------------------------------------------------- embeddings

def embedding_ready() -> bool:
    return bool(cfg("embedding_api_key"))


def embed(texts: list[str], chunk: int = 32) -> list[list[float]]:
    if not embedding_ready():
        raise NotConfigured("还没填嵌入接口的 API Key，到「设置」里填")
    vecs: list[list[float]] = []
    for i in range(0, len(texts), chunk):
        vecs.extend(_embed_request(texts[i:i + chunk]))
    return vecs


def _embed_request(texts: list[str]) -> list[list[float]]:
    body: dict = {"model": cfg("embedding_model"), "input": texts}
    order = [p.strip() for p in cfg("embedding_providers").split(",") if p.strip()]
    if order:
        body["provider"] = {"order": order, "allow_fallbacks": True}
    headers = {"Authorization": f"Bearer {cfg('embedding_api_key')}"}
    url = cfg("embedding_base_url").rstrip("/") + "/embeddings"
    resp = httpx.post(url, json=body, headers=headers, timeout=90)
    if resp.status_code >= 400 and "provider" in body:
        del body["provider"]  # provider routing can be rejected; retry without it
        resp = httpx.post(url, json=body, headers=headers, timeout=90)
    if resp.status_code >= 400:
        raise ApiError(f"嵌入接口出错 {resp.status_code}: {resp.text[:200]}")
    items = resp.json()["data"]
    if all("index" in d for d in items):
        items = sorted(items, key=lambda d: d["index"])
    return [d["embedding"] for d in items]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- chat

def llm_ready() -> bool:
    return bool(cfg("llm_api_key"))


CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"
CLAUDE_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


def llm_model() -> str:
    model = cfg("llm_model")
    if cfg("llm_provider") == "anthropic" and not model.startswith("claude-"):
        return CLAUDE_DEFAULT_MODEL  # e.g. the DeepSeek default still filled in after switching to Claude
    return model


def chat(system: str, user: str, max_tokens: int = 1500) -> str:
    if not llm_ready():
        raise NotConfigured("还没填大模型的 API Key，到「设置」里填")
    if cfg("llm_provider") == "anthropic":
        return _chat_claude(system, user)
    resp = httpx.post(
        cfg("llm_base_url").rstrip("/") + "/chat/completions",
        json={
            "model": cfg("llm_model"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        },
        headers={"Authorization": f"Bearer {cfg('llm_api_key')}"},
        timeout=120,
    )
    if resp.status_code >= 400:
        raise ApiError(f"大模型接口出错 {resp.status_code}: {resp.text[:200]}")
    content = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    if not content:  # reasoning models can spend the whole token budget thinking
        raise ApiError(f"大模型没有返回内容，换一个非推理模型试试（当前 {llm_model()}）")
    return content


def _chat_claude(system: str, user: str) -> str:
    """The Claude Messages API. Thinking is on for current models, so max_tokens leaves it room;
    a refusal on one of the newest models is retried server-side on a fallback model."""
    import anthropic

    model = llm_model()
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if model in CLAUDE_FALLBACK_MODELS else {}
    client = anthropic.Anthropic(api_key=cfg("llm_api_key"), timeout=300)
    try:
        resp = client.beta.messages.create(model=model, max_tokens=16000, system=system,
                                           messages=[{"role": "user", "content": user}], **extra)
    except anthropic.APIStatusError as exc:
        raise ApiError(f"Claude 接口出错 {exc.status_code}: {exc.message[:200]}") from exc
    except anthropic.APIConnectionError as exc:
        raise ApiError(f"连不上 Claude 接口：{exc}") from exc
    if resp.stop_reason == "refusal":
        raise ApiError("Claude 拒绝回答这个请求")
    content = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not content:
        raise ApiError(f"Claude 没有返回内容（stop_reason: {resp.stop_reason}）")
    return content


# ---------------------------------------------------------------- connection tests (设置 → 测试)

def test_service(service: str) -> str:
    """One cheap call per service; returns a short human-readable result or raises."""
    if service == "dataforseo":
        if not dataforseo_ready():
            raise NotConfigured("先填 DataForSEO 的账号和密码")
        resp = httpx.get("https://api.dataforseo.com/v3/appendix/user_data",
                         auth=(cfg("dataforseo_login"), cfg("dataforseo_password")), timeout=30)
        if resp.status_code == 401:
            raise ApiError("账号或密码不对")
        resp.raise_for_status()
        money = (((resp.json().get("tasks") or [{}])[0].get("result") or [{}])[0] or {}).get("money") or {}
        return f"连接成功，余额 ${money.get('balance', 0):.2f}"
    if service == "embedding":
        vec = embed(["ping"])[0]
        return f"连接成功，{cfg('embedding_model')} 返回 {len(vec)} 维向量"
    if service == "llm":
        reply = chat("Reply with the single word OK.", "ping", max_tokens=200)
        return f"连接成功，{llm_model()} 回复：{reply[:40]}"
    raise ValueError(service)
