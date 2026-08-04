"""
系统设置类工具 —— 包 services/settings_service.py 的白名单读写。

get_settings：只读，敏感字段只给掩码预览（复用 _field_view，绝不回传真实密钥）。
update_setting：改单个配置项，需二次确认（requires_confirmation=True + preview_fn），
复用路由的白名单/select 校验与 set_key 双写逻辑，杜绝任意 env 注入。

未知 key / 风控只读键 / 非法 select 值 都是正常的业务性拒绝（business_result=negative），
不是工具执行异常。
"""
from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

# 风控/资金相关键对模型永久只读 —— 风控是门，不是可协商的同事。
# 当前白名单本就不含这些键，此黑名单是防未来 SETTINGS_SCHEMA 扩充时
# 风控键静默变成模型可写的第二道防线。改风控只能去设置页手动操作。
_RISK_READONLY_KEYS = frozenset({
    "max_position_pct", "max_total_position_pct", "max_daily_loss_pct",
    "stop_loss_pct", "take_profit_pct", "total_capital",
    # 🔒 分市场本金（S5 03c）：**本金就是风控参数** —— 它决定每笔下多大、
    #    日亏 3% 的基数是多少。漏了这四个，AI 就能自己改本金了。
    "capital_a_share", "capital_hk_stock", "capital_us_stock", "capital_crypto",
})


class GetSettingsArgs(BaseModel):
    pass


@tool(
    name="get_settings",
    description="读取当前系统配置（AI 模型档位、代理模式、各类 API 密钥）。密钥只显示是否已配置+掩码，不泄露真实值。",
    args_model=GetSettingsArgs,
    category="settings",
    group="system",
)
def get_settings() -> ToolEnvelope:
    from services.settings_service import SETTINGS_SCHEMA, field_view as _field_view
    groups = [
        {
            "group": g["group"],
            "title": g["title"],
            "fields": [_field_view(f) for f in g["fields"]],
        }
        for g in SETTINGS_SCHEMA
    ]
    return ToolEnvelope(data={"groups": groups})


def _preview_setting(args: dict) -> dict:
    """确认前预览：显示将改哪个 key、新值（敏感字段掩码），并预检白名单/select。"""
    from services.settings_service import FIELD_BY_KEY as _FIELD_BY_KEY, mask as _mask
    key = (args.get("key") or "").strip()
    value = "" if args.get("value") is None else str(args.get("value"))
    if key in _RISK_READONLY_KEYS:
        return {"error": f"{key} 是风控/资金参数，MoneyBill 无权修改，请 Jason 在设置页手动调整"}
    field = _FIELD_BY_KEY.get(key)
    if field is None:
        return {"error": f"未知配置项：{key}（只允许白名单内的 key）"}
    if field["type"] == "select" and value not in field.get("options", []):
        return {"error": f"{key} 的值必须是 {field.get('options')} 之一，收到：{value}"}
    shown = _mask(value) if field.get("sensitive") else value
    return {
        "key": key,
        "label": field["label"],
        "new_value": shown,
        "sensitive": bool(field.get("sensitive")),
        "note": "落盘 .env + 热更新运行进程；大部分改动即时生效，个别需重启后端。",
    }


class UpdateSettingArgs(BaseModel):
    key: str = Field(..., min_length=1, description="配置项 key，如 OPENAI_BEST_MODEL / HTTP_PROXY_MODE")
    value: str = Field(..., description="新值")


@tool(
    name="update_setting",
    description="修改单个系统配置项（如切换 AI 模型档、改代理模式、填某个 API Key）。会先让 Jason 二次确认。",
    args_model=UpdateSettingArgs,
    category="settings",
    group="system",
    requires_confirmation=True,
    preview_fn=_preview_setting,
)
def update_setting(key: str, value: str) -> ToolEnvelope:
    from services.settings_service import apply_setting, SettingError
    key = (key or "").strip()
    # 风控/资金键对模型永久只读（第二道防线，这些键本就不在 schema）
    if key in _RISK_READONLY_KEYS:
        return ToolEnvelope(business_result="negative",
                             message=f"{key} 是风控/资金参数，对 MoneyBill 只读，请让 Jason 在设置页手动调整")
    try:
        result = apply_setting(key, value)
    except SettingError as e:
        return ToolEnvelope(business_result="negative", message=str(e))
    return ToolEnvelope(data=result)
