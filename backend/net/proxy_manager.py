"""
快代理 IP 按需管理器（迁自 scripts/proxy_manager.py）。

功能：
- 从快代理 API 每次获取 1 个代理 IP，拿到即用
- IP 失效后立即请求新 IP，主链接额度用完自动切备用

与旧版的区别（本次统一网络模块的核心修复）：
- 取代理这步改用 make_domestic_session()（trust_env=False），**不再依赖 Clash**。
  旧版裸 requests.get 会继承 .env 的 HTTP_PROXY=127.0.0.1:7897，Clash 关时连
  取快代理 IP 都失败（proxy-notes line 104 的待办，本次落地）。
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from net.session import make_domestic_session

# 加载 .env（本文件在 backend/net/ 下，parent.parent 即 backend/）
load_dotenv(Path(__file__).parent.parent / ".env")

# 保存原始响应的目录
PROXY_RAW_RESPONSE_DIR = Path(__file__).parent.parent / "scripts" / "proxy_raw_responses"


@dataclass
class ProxyInfo:
    """单个代理 IP 信息"""
    ip: str
    port: int
    expire_at: str  # ISO 格式过期时间
    username: str = ""
    password: str = ""
    protocol: str = "http"

    @property
    def url(self) -> str:
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.ip}:{self.port}"
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def is_expired(self) -> bool:
        try:
            expire_time = datetime.fromisoformat(self.expire_at)
            return datetime.now() > expire_time
        except (ValueError, TypeError):
            return True

    def to_requests_proxies(self) -> dict[str, str]:
        """转为 requests 的 proxies 参数格式"""
        proxy_url = self.url
        return {
            "http": proxy_url,
            "https": proxy_url,
        }

    def to_env_url(self) -> str:
        """转为可写入 HTTP(S)_PROXY env 的单一 URL（给 domestic_akshare 注入用）"""
        return self.url


class ProxyManager:
    """按需代理 IP 管理器 — 每次从 API 获取 1 个 IP"""

    def __init__(self) -> None:
        self.api_url: str = os.getenv("kuaidaili_api", "")
        self.api_url_backup: str = os.getenv("kuaidaili_api_backup", "")
        self._using_backup: bool = False  # 是否已切到备用链接
        self.current_proxy: ProxyInfo | None = None
        self.fetch_count: int = 0  # 累计请求 API 次数
        self.fail_count: int = 0   # 累计失效 IP 次数

    def fetch_one_proxy(self) -> ProxyInfo | None:
        """
        从快代理 API 获取 1 个代理 IP

        Returns:
            ProxyInfo 或 None（获取失败）
        """
        if not self.api_url:
            logger.error("未配置 kuaidaili_api，请检查 .env 文件")
            return None

        try:
            # trust_env=False 绕开系统 Clash：访问的是国内 dps.kdlapi.com，
            # 本不该走 Clash；这样 Clash 关时也能取到快代理 IP。
            session = make_domestic_session()
            response = session.get(self.api_url, timeout=10)
            response.raise_for_status()

            raw_data = response.json()
            self.fetch_count += 1

            # 保存原始响应
            self._save_raw_response(raw_data)

            # 检查是否链接额度用完（快代理返回 code != 0 表示异常）
            if isinstance(raw_data, dict) and raw_data.get("code", 0) != 0:
                msg = raw_data.get("msg", "未知错误")
                logger.warning(f"快代理 API 返回错误: {msg}")
                # 尝试切换到备用链接
                if self._switch_to_backup():
                    return self.fetch_one_proxy()  # 用新链接重试
                return None

            # 解析
            proxy = self._parse_response(raw_data)
            if proxy:
                self.current_proxy = proxy
                tag = "[备用]" if self._using_backup else ""
                logger.info(f"获取新代理 IP{tag}: {proxy.ip}:{proxy.port} (第 {self.fetch_count} 次请求)")
                return proxy

            logger.warning("API 返回数据中未解析到有效 IP")
            return None

        except json.JSONDecodeError as e:
            logger.error(f"API 返回非 JSON 格式: {e}")
            return None
        except Exception as e:  # noqa: BLE001 — 网络类异常统一切备用重试
            logger.error(f"调用快代理 API 失败: {e}")
            if self._switch_to_backup():
                return self.fetch_one_proxy()
            return None

    def _switch_to_backup(self) -> bool:
        """切换到备用快代理链接，返回是否成功切换"""
        if self._using_backup or not self.api_url_backup:
            return False
        self._using_backup = True
        self.api_url = self.api_url_backup
        logger.info("主链接额度用完，已切换到备用快代理链接")
        return True

    def _parse_response(self, data: Any) -> ProxyInfo | None:
        """
        解析 API 响应，提取单个 IP

        预期格式:
        {"code": 0, "data": {"proxy_list": ["ip:port"], ...}}
        """
        ip_list = None

        if isinstance(data, dict):
            inner = data.get("data", data)
            if isinstance(inner, dict):
                ip_list = (
                    inner.get("proxy_list")
                    or inner.get("list")
                    or inner.get("proxies")
                    or inner.get("items")
                )
            elif isinstance(inner, list):
                ip_list = inner

            if ip_list is None:
                ip_list = data.get("list") or data.get("proxies")
        elif isinstance(data, list):
            ip_list = data

        if not ip_list or not isinstance(ip_list, list):
            logger.warning(f"无法识别的 API 响应结构: {data}")
            return None

        # 取第一个（只请求了 1 个）
        item = ip_list[0]
        return self._parse_single_proxy(item)

    def _parse_single_proxy(self, item: Any) -> ProxyInfo | None:
        """解析单个代理 IP 条目（支持 ip:port 和 ip:port:username:password 格式）"""
        if isinstance(item, str):
            parts = item.strip().split(":")
            if len(parts) == 4:
                # ip:port:username:password
                return ProxyInfo(
                    ip=parts[0],
                    port=int(parts[1]),
                    username=parts[2],
                    password=parts[3],
                    expire_at=self._default_expire_at(),
                )
            elif len(parts) >= 2:
                # ip:port
                return ProxyInfo(
                    ip=parts[0],
                    port=int(parts[1]),
                    expire_at=self._default_expire_at(),
                )
            return None

        if isinstance(item, dict):
            ip = item.get("ip") or item.get("IP") or item.get("host")
            port = item.get("port") or item.get("Port") or item.get("PORT")

            if not ip or not port:
                return None

            expire_raw = (
                item.get("expire_time")
                or item.get("expire_at")
                or item.get("expireTime")
                or item.get("deadline")
            )
            if expire_raw:
                expire_at = self._normalize_expire_time(expire_raw)
            else:
                expire_at = self._default_expire_at()

            return ProxyInfo(
                ip=str(ip),
                port=int(port),
                expire_at=expire_at,
            )

        return None

    def _default_expire_at(self) -> str:
        """默认过期时间：5 分钟后"""
        return (datetime.now() + timedelta(minutes=5)).isoformat()

    def _normalize_expire_time(self, raw: Any) -> str:
        """统一过期时间为 ISO 格式"""
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw).isoformat()

        raw = str(raw).strip()

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).isoformat()
            except ValueError:
                continue

        try:
            seconds = int(raw)
            if seconds < 100000:
                return (datetime.now() + timedelta(seconds=seconds)).isoformat()
        except ValueError:
            pass

        return self._default_expire_at()

    def get_proxy(self) -> ProxyInfo | None:
        """
        获取一个可用代理

        如果当前代理可用则返回，否则请求新的。
        """
        # 当前代理仍可用
        if self.current_proxy and not self.current_proxy.is_expired:
            return self.current_proxy

        # 请求新 IP
        return self.fetch_one_proxy()

    def switch_proxy(self) -> ProxyInfo | None:
        """
        立即切换到新代理（当前 IP 失效时调用）
        """
        if self.current_proxy:
            self.fail_count += 1
            logger.info(f"代理 {self.current_proxy.ip}:{self.current_proxy.port} 已失效 "
                        f"(累计失效: {self.fail_count})")

        self.current_proxy = None
        return self.fetch_one_proxy()

    def _save_raw_response(self, data: Any) -> None:
        """保存 API 原始响应（默认关闭；PROXY_SAVE_RAW=1 时启用，
        代理池并发换 IP 场景下每次取 IP 都写盘太吵）"""
        if os.getenv("PROXY_SAVE_RAW", "0") != "1":
            return
        PROXY_RAW_RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = PROXY_RAW_RESPONSE_DIR / f"kuaidaili_{timestamp}.json"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.debug(f"原始响应已保存: {filepath}")

    def print_status(self) -> None:
        """打印状态"""
        logger.info(f"{'=' * 40}")
        logger.info("代理管理器状态:")
        logger.info(f"  API 请求次数: {self.fetch_count}")
        logger.info(f"  IP 失效次数: {self.fail_count}")
        if self.current_proxy:
            logger.info(f"  当前代理: {self.current_proxy.ip}:{self.current_proxy.port}")
            logger.info(f"  过期时间: {self.current_proxy.expire_at}")
            logger.info(f"  已过期: {'是' if self.current_proxy.is_expired else '否'}")
        else:
            logger.info("  当前代理: 无")
        logger.info(f"{'=' * 40}")


# ============ CLI 入口 ============
# 排查快代理额度/连通性用：`conda run -n quant python -m net.proxy_manager [fetch|status]`
# （原先住在 scripts/proxy_manager.py 的兼容 shim 里，随 shim 一起搬过来。）
if __name__ == "__main__":
    import sys

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
        print("用法: python -m net.proxy_manager [fetch|status]")
