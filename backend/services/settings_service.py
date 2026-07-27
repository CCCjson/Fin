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
# 字段的 `type` 取值：text | password | select；sensitive=True 的字段不回传真实值。
# ⚠️ 别写成 `# type: ...` 开头 —— mypy 会把它当 PEP 484 类型注释去解析，
# 直接报 `Invalid syntax`，且**任何传递 import 到本模块的文件都跑不了 mypy**
# （运行时完全正常，所以这个坑能潜伏很久）。
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
        "group": "crypto",
        "title": "加密货币（币安现货）",
        "desc": ("加密模块的非敏感调优项。⚠️ 币安 API 密钥出于安全**不在此处配置**，"
                 "请手动改 backend/.env 的 BINANCE_API_KEY / BINANCE_API_SECRET。"
                 "风控（单仓/总仓/止损）与股票共用「AI 与风控」设置，此处不重复。"),
        "fields": [
            {"key": "CRYPTO_SCHEDULER_ENABLED", "label": "7×24 数据链开关", "type": "select",
             "options": ["true", "false"], "default": "true"},
            {"key": "CRYPTO_UPDATE_INTERVAL_MIN", "label": "数据刷新间隔（分钟）", "type": "text", "default": "30"},
            {"key": "CRYPTO_QUOTES", "label": "计价币白名单（逗号分隔）", "type": "text", "default": "USDT"},
            {"key": "CRYPTO_FOCUS", "label": "情报聚焦交易对（逗号分隔，留空=主流表）", "type": "text"},
            {"key": "CRYPTO_DAILY_MAX_LOOKBACK", "label": "增量回补窗口（天）", "type": "text", "default": "30"},
            {"key": "CRYPTO_AUTO_EARN_ENABLED", "label": "卖出后自动理财扫归", "type": "select",
             "options": ["true", "false"], "default": "true"},
            {"key": "CRYPTO_EARN_ASSETS", "label": "自动理财币种（逗号分隔）", "type": "text", "default": "USDT"},
            {"key": "CRYPTO_EARN_DUST_MIN", "label": "扫归最小金额（低于则不扫）", "type": "text", "default": "1"},
            {"key": "BINANCE_REST_BASE", "label": "币安行情基址（地域受限时切镜像）", "type": "text",
             "default": "https://api.binance.com"},
            {"key": "BINANCE_FAPI_BASE", "label": "币安合约基址（衍生品数据）", "type": "text",
             "default": "https://fapi.binance.com"},
            {"key": "BINANCE_TRADE_BASE", "label": "币安交易基址", "type": "text",
             "default": "https://api.binance.com"},
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


def groups_view() -> list[dict[str, Any]]:
    """整份 schema 的渲染视图（分组 + 每组字段的 field_view）。route 与 agent 工具共用。"""
    return [
        {
            "group": g["group"],
            "title": g["title"],
            "desc": g.get("desc"),
            "fields": [field_view(f) for f in g["fields"]],
        }
        for g in SETTINGS_SCHEMA
    ]


class SettingError(ValueError):
    """设置校验失败（未知 key / 非法 select 值 / 敏感字段留空）——业务性拒绝，非系统异常。"""


def apply_setting(key: str, value: str) -> dict[str, Any]:
    """校验并落盘单个配置项（白名单 + select 校验 + 敏感留空跳过），写 .env + 热更 os.environ。

    校验不过抛 `SettingError`（调用方翻成 400 / negative）。⚠️ 风控/资金只读键的额外
    黑名单在 agent 工具层（settings_tools._RISK_READONLY_KEYS）；这些键本就不在 schema，
    此处会当「未知配置项」直接拒绝。
    """
    from dotenv import set_key

    key = (key or "").strip()
    value = "" if value is None else str(value)
    field = FIELD_BY_KEY.get(key)
    if field is None:
        raise SettingError(f"未知配置项 {key}（只允许白名单内的 key）")
    if field["type"] == "select" and value not in field.get("options", []):
        raise SettingError(f"{key} 必须是 {field.get('options')} 之一，收到：{value}")
    if field.get("sensitive") and value == "":
        raise SettingError(f"{key} 留空视为不改动，已跳过")
    set_key(str(ENV_PATH), key, value)
    os.environ[key] = value
    return {"updated": key, "label": field["label"], "note": "已落盘并热更新。"}
