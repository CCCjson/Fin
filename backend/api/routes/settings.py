"""
系统设置 API —— .env 白名单配置的读取/保存（前端设置页用）。

薄封装：schema 定义、脱敏视图、校验落盘全在 services/settings_service.py，
route 与 MoneyBill 的 settings_tools 共用同一份逻辑（groups_view / apply_setting）。

⛔ 币安 API 密钥（BINANCE_API_KEY/SECRET）不在 SETTINGS_SCHEMA 内，此接口天然碰不到。
"""
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["设置"])


@router.get("/schema", summary="读取设置 schema + 当前值（敏感字段脱敏）")
async def get_settings_schema():
    """按 group 返回全部可配置项的渲染视图。敏感字段只回 is_set + 掩码 preview，不泄露真实值。"""
    try:
        from services.settings_service import groups_view
        return {"groups": groups_view()}
    except Exception as e:
        logger.error(f"读取设置 schema 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


class UpdateSettingBody(BaseModel):
    value: str


@router.put("/{key}", summary="保存单个配置项（白名单校验 + 落盘 .env）")
async def update_setting_ep(key: str, body: UpdateSettingBody):
    """白名单 + select 校验通过后写 .env 并热更新进程。未知 key / 非法值 → 400。"""
    try:
        from services.settings_service import SettingError, apply_setting
        try:
            result = apply_setting(key, body.value)
        except SettingError as e:      # 未知 key / 非法 select 值 / 敏感留空 → 400，不是 500
            raise HTTPException(status_code=400, detail=str(e)) from e
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存设置 {key} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
