"""
系统设置服务 — .env 白名单配置的 schema / 读取视图 / 脱敏。

从 api/routes/settings.py 下沉而来：route 和 MoneyBill 工具（settings_tools）
共用这一份，route 只做 HTTP 封装。

设计：
- 字段清单只在 SETTINGS_SCHEMA 定义一次（白名单），前端按 schema 通用渲染。
- 只有白名单内的 key 能被读/写，杜绝任意 env 注入。
- 敏感字段（sensitive=True）绝不回传真实值，只给掩码预览 + is_set。
"""
import os
from pathlib import Path
from typing import Any

# backend/.env（本文件在 backend/services/ 下，parents[1] = backend）
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


# —— 配置字段白名单（分组）——
# type: text | password | select；sensitive=True 的字段不回传真实值。
SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {
        "group": "ai",
        "title": "AI 模型",
        "desc": "分档模型：真正出投资建议用最强档，信息加工用便宜档；Alpha Lab 单独配置。",
        "fields": [
            {"key": "OPENAI_BEST_MODEL", "label": "最强档模型（投资建议）", "type": "text", "default": "gpt-5.5"},
            {"key": "OPENAI_CHEAP_MODEL", "label": "便宜档模型（信息加工）", "type": "text", "default": "gpt-5.4-mini"},
            {"key": "ALPHA_LAB_PROVIDER", "label": "Alpha Lab Provider", "type": "select",
             "options": ["local", "openai", "claude"], "default": "claude"},
            {"key": "ALPHA_LAB_LOCAL_MODEL", "label": "本地模型名", "type": "text"},
            {"key": "ALPHA_LAB_LOCAL_BASE_URL", "label": "本地模型 Base URL", "type": "text"},
            {"key": "ALPHA_LAB_EXPLORE_MODEL", "label": "探索模型（OpenAI）", "type": "text"},
            {"key": "ALPHA_LAB_REFINE_MODEL", "label": "精炼模型（OpenAI）", "type": "text"},
            {"key": "ALPHA_LAB_CLAUDE_EXPLORE_MODEL", "label": "探索模型（Claude）", "type": "text"},
            {"key": "ALPHA_LAB_CLAUDE_REFINE_MODEL", "label": "精炼模型（Claude）", "type": "text"},
        ],
    },
    {
        "group": "network",
        "title": "网络与代理",
        "desc": "代理总开关 HTTP_PROXY_MODE：auto=探测 Clash 在则走、不在则直连；direct=强制直连；或直接填代理 URL。",
        "fields": [
            {"key": "HTTP_PROXY_MODE", "label": "代理模式（auto / direct / 代理URL）", "type": "text", "default": "auto"},
            {"key": "kuaidaili_api", "label": "快代理主链接", "type": "password", "sensitive": True},
            {"key": "kuaidaili_api_backup", "label": "快代理备用链接", "type": "password", "sensitive": True},
        ],
    },
    {
        "group": "secrets",
        "title": "API 密钥",
        "desc": "密钥类字段出于安全只显示是否已配置，留空表示不改动。",
        "fields": [
            {"key": "OPENAI_API_KEY", "label": "OpenAI API Key", "type": "password", "sensitive": True},
            {"key": "OPENAI_BASE_URL", "label": "OpenAI Base URL", "type": "text", "default": "https://api.openai.com/v1"},
            {"key": "ANTHROPIC_API_KEY", "label": "Claude API Key", "type": "password", "sensitive": True},
            {"key": "ANTHROPIC_BASE_URL", "label": "Claude Base URL", "type": "text"},
            {"key": "FINNHUB_API_KEY", "label": "Finnhub API Key", "type": "password", "sensitive": True},
            {"key": "TUSHARE_TOKEN", "label": "Tushare Token", "type": "password", "sensitive": True},
        ],
    },
]

# 展平成 {key: field} 便于校验
FIELD_BY_KEY: dict[str, dict[str, Any]] = {
    f["key"]: f for g in SETTINGS_SCHEMA for f in g["fields"]
}


def mask(value: str) -> str:
    """脱敏预览：只露首尾各 4 字符，中间用 … 代替。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "…" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def field_view(field: dict[str, Any]) -> dict[str, Any]:
    """把一个字段渲染成前端可用的视图（含当前值 / 脱敏预览）。"""
    key = field["key"]
    raw = os.getenv(key, field.get("default", ""))
    view: dict[str, Any] = {
        "key": key,
        "label": field["label"],
        "type": field["type"],
    }
    if "options" in field:
        view["options"] = field["options"]
    if field.get("sensitive"):
        view["sensitive"] = True
        view["is_set"] = bool(raw)
        view["preview"] = mask(raw)  # 绝不回传真实值
        view["value"] = ""            # 敏感字段前端始终从空开始，留空=不改
    else:
        view["value"] = raw
    return view
