"""
C++ 数据管道 API 代理路由

将前端请求转发到 C++ 数据管道服务 (localhost:8003)。
前端只和 FastAPI(:8000) 通信，不直接访问 C++ 服务。
"""
import os
import asyncio
import requests
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from loguru import logger
from dotenv import load_dotenv

# 加载 .env（项目根目录）
load_dotenv(Path(__file__).parent.parent.parent / ".env")

router = APIRouter(prefix="/pipeline", tags=["C++数据管道"])

# C++ 数据管道服务地址
CPP_SERVICE_URL = "http://localhost:8003"
TIMEOUT = 30.0


# ── 请求模型 ──

class PipelineFetchRequest(BaseModel):
    symbols: List[str] = Field(default=[], description="股票代码列表，空=全部活跃A股")
    begin_date: str = Field(default="", description="开始日期 YYYYMMDD")
    end_date: str = Field(default="", description="结束日期 YYYYMMDD")
    thread_count: int = Field(default=4, description="线程数")
    batch_size: int = Field(default=500, description="批量写入大小")
    use_proxy: bool = Field(default=True, description="是否使用代理")
    switch_ip_every: int = Field(default=800, description="每N次请求换IP")


# ── 代理工具函数（同步 requests，在线程池中运行） ──

def _proxy_sync(method: str, path: str, body: dict = None) -> dict:
    """转发请求到 C++ 数据管道服务（同步版本，运行在线程池）"""
    url = f"{CPP_SERVICE_URL}{path}"
    try:
        if method == "GET":
            resp = requests.get(url, timeout=TIMEOUT)
        elif method == "POST":
            resp = requests.post(url, json=body or {}, timeout=TIMEOUT)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text or f"C++ 服务返回 {resp.status_code}"
            raise HTTPException(status_code=resp.status_code, detail=detail)

        return resp.json()
    except requests.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="C++ 数据管道服务未启动。请先运行: cd data_pipeline/build && ./pipeline_server"
        )
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="C++ 数据管道服务响应超时")


async def _proxy(method: str, path: str, body: dict = None) -> dict:
    """转发请求到 C++ 服务（线程池隔离）"""
    return await asyncio.to_thread(_proxy_sync, method, path, body)


# ── API 端点 ──

@router.get("/health")
async def health():
    """健康检查"""
    return await _proxy("GET", "/health")


@router.post("/fetch")
async def fetch(req: PipelineFetchRequest):
    """提交数据抓取任务"""
    body = req.model_dump()
    # 强制走快代理（Clash TUN 会劫持 eastmoney 直连请求导致失败）
    body["proxy_api_url"] = os.getenv("kuaidaili_api_backup", "")
    if not body["proxy_api_url"]:
        logger.warning("未配置 kuaidaili_api，将直连（可能因 Clash 而失败）")
    # 移除前端专用字段，C++ 不需要
    body.pop("use_proxy", None)
    result = await _proxy("POST", "/api/pipeline/fetch", body)
    logger.info(f"数据管道任务已提交: {result.get('task_id', 'unknown')}")
    return result


@router.get("/status/{task_id}")
async def status(task_id: str):
    """查询任务进度"""
    return await _proxy("GET", f"/api/pipeline/status/{task_id}")


@router.post("/stop/{task_id}")
async def stop(task_id: str):
    """停止任务"""
    return await _proxy("POST", f"/api/pipeline/stop/{task_id}")


@router.get("/stats")
async def stats():
    """全局吞吐量统计"""
    return await _proxy("GET", "/api/pipeline/stats")
