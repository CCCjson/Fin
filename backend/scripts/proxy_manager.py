"""
[兼容 shim] 快代理 IP 管理器 —— 实现已迁至 net/proxy_manager.py。

保留本模块只为不破坏历史 import（realtime.py / daily_updater.py / web_searcher.py
等通过 sys.path 注入 scripts 后 `from proxy_manager import ProxyManager`）。
新代码请直接 `from net import ProxyManager`。
"""
import sys
from pathlib import Path

# 确保 backend/ 在 sys.path 上，才能 import net 包
_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from net.proxy_manager import (  # noqa: E402,F401
    ProxyManager,
    ProxyInfo,
    PROXY_RAW_RESPONSE_DIR,
)

__all__ = ["ProxyManager", "ProxyInfo", "PROXY_RAW_RESPONSE_DIR"]


# ============ CLI 入口（保留原 python scripts/proxy_manager.py [fetch|status]）============

if __name__ == "__main__":
    from loguru import logger

    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <level>{message}</level>",
    )

    manager = ProxyManager()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"

    if cmd == "fetch":
        proxy = manager.fetch_one_proxy()
        if proxy:
            logger.info(f"获取成功: {proxy.ip}:{proxy.port}")
            logger.info(f"代理 URL: {proxy.url}")
            logger.info(f"过期时间: {proxy.expire_at}")
        else:
            logger.error("获取失败")
    elif cmd == "status":
        manager.print_status()
    else:
        print(f"未知命令: {cmd}")
        print("用法: python proxy_manager.py [fetch|status]")
