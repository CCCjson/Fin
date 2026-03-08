"""
东方财富企业级爬虫框架

功能：
- 请求限速 + 指数退避重试
- 随机 UA 和浏览器指纹
- Session/Cookie 自动维护
- 动态 Token 刷新
- 自适应限流检测
"""

import time
import random
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from loguru import logger


class ProxyTimeoutError(Exception):
    """代理超时/失效异常，通知上层立即切换 IP"""
    pass


@dataclass
class CrawlerConfig:
    """爬虫配置"""
    min_delay: float = 2.0          # 最小请求间隔
    max_delay: float = 5.0          # 最大请求间隔
    max_retries: int = 3            # 最大重试次数
    retry_delay: float = 10.0       # 重试基础延迟
    timeout: int = 30               # 请求超时
    rate_limit_pause: float = 60.0  # 被限流后暂停时间


class BrowserFingerprint:
    """浏览器指纹生成器"""

    USER_AGENTS = [
        # Chrome Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        # Chrome Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        # Firefox
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        # Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        # Edge
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ]

    ACCEPT_LANGUAGES = [
        "zh-CN,zh;q=0.9,en;q=0.8",
        "zh-CN,zh;q=0.9",
        "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    ]

    ACCEPT_ENCODINGS = [
        "gzip, deflate, br",
        "gzip, deflate",
        "gzip, deflate, br, zstd",
    ]

    @classmethod
    def generate(cls) -> Dict[str, str]:
        """生成随机浏览器指纹"""
        ua = random.choice(cls.USER_AGENTS)

        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": random.choice(cls.ACCEPT_LANGUAGES),
            # 移除 Accept-Encoding，让 requests 自动处理压缩
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        # 根据 UA 添加对应的浏览器特征
        if "Chrome" in ua:
            headers["Sec-Ch-Ua"] = '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
            headers["Sec-Ch-Ua-Mobile"] = "?0"
            headers["Sec-Ch-Ua-Platform"] = '"Windows"' if "Windows" in ua else '"macOS"'
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-site"

        return headers


class TokenManager:
    """东方财富 Token 管理器"""

    def __init__(self):
        self.token = None
        self.token_expire = 0
        self.session = None

    def get_token(self, session: requests.Session) -> str:
        """获取或刷新 Token"""
        # 东方财富的 token 是固定的，但我们可以动态生成一些参数
        # ut 参数是一个固定值，用于 API 认证
        self.token = "7eea3edcaed734bea9cbfc24409ed989"
        return self.token

    def generate_request_id(self) -> str:
        """生成请求 ID（模拟浏览器行为）"""
        timestamp = str(int(time.time() * 1000))
        random_str = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
        return hashlib.md5(f"{timestamp}{random_str}".encode()).hexdigest()[:16]


class RateLimiter:
    """自适应限速器"""

    def __init__(self, min_delay: float, max_delay: float):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.current_delay = min_delay
        self.last_request_time = 0
        self.consecutive_success = 0
        self.consecutive_fail = 0

    def wait(self):
        """等待适当时间"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.current_delay:
            sleep_time = self.current_delay - elapsed
            # 添加随机抖动
            jitter = random.uniform(-0.3, 0.5)
            sleep_time = max(0.1, sleep_time + jitter)
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def on_success(self):
        """请求成功回调"""
        self.consecutive_success += 1
        self.consecutive_fail = 0

        # 连续成功 10 次，稍微加快速度
        if self.consecutive_success >= 10:
            self.current_delay = max(self.min_delay, self.current_delay * 0.9)
            self.consecutive_success = 0

    def on_failure(self, is_rate_limit: bool = False):
        """请求失败回调"""
        self.consecutive_fail += 1
        self.consecutive_success = 0

        if is_rate_limit:
            # 被限流，大幅增加延迟
            self.current_delay = min(self.max_delay * 2, self.current_delay * 2)
        else:
            # 普通失败，小幅增加延迟
            self.current_delay = min(self.max_delay, self.current_delay * 1.2)

    def reset(self):
        """重置限速器"""
        self.current_delay = self.min_delay
        self.consecutive_success = 0
        self.consecutive_fail = 0


# 上海指数代码集合（代码与深市股票重叠，需要硬编码区分）
SH_INDEX_CODES = {
    "000001", "000002", "000003", "000010", "000015", "000016",
    "000300", "000688", "000852", "000905", "000906", "000985",
    "000986", "000991", "000992",
}


def resolve_eastmoney_market(code: str) -> int:
    """推断东财 secid 的市场编号 (0=SZ/BJ, 1=SH)"""
    if code.startswith("6"):               return 1   # 沪市股票
    if code.startswith(("51", "56", "58")): return 1   # 沪市 ETF
    if code in SH_INDEX_CODES:             return 1   # 沪市指数
    return 0  # 深市股票/ETF、北交所、深市指数(399xxx)


class EastMoneyCrawler:
    """东方财富爬虫"""

    # API 端点
    HIST_API = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    HOME_PAGE = "https://quote.eastmoney.com/"

    def __init__(self, config: CrawlerConfig = None):
        self.config = config or CrawlerConfig()
        self.session = self._create_session()
        self.token_manager = TokenManager()
        self.rate_limiter = RateLimiter(
            self.config.min_delay,
            self.config.max_delay
        )
        self.fingerprint = None
        self._refresh_fingerprint()

        # 统计
        self.stats = {
            "total_requests": 0,
            "success": 0,
            "failed": 0,
            "rate_limited": 0,
        }

        # 初始化：访问主页获取 Cookie（模拟真实用户行为）
        self._warm_up()

    def _create_session(self) -> requests.Session:
        """创建带重试机制的 Session"""
        session = requests.Session()

        # 配置重试策略
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20,
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 绕过系统代理（macOS Clash 等），只用显式传入的快代理
        session.trust_env = False

        return session

    def _refresh_fingerprint(self):
        """刷新浏览器指纹"""
        self.fingerprint = BrowserFingerprint.generate()
        # 不再直接更新 session.headers，改为每次请求时独立设置

    def _warm_up(self, proxies: Optional[Dict[str, str]] = None):
        """预热：访问主页获取 Cookie，模拟真实用户"""
        try:
            headers = self.fingerprint.copy()
            headers.update({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            })
            response = self.session.get(
                self.HOME_PAGE,
                headers=headers,
                timeout=10,
                allow_redirects=True,
                proxies=proxies,
            )
            logger.debug(f"预热完成，获得 {len(self.session.cookies)} 个 Cookie")
        except Exception as e:
            logger.warning(f"预热失败（不影响使用）: {e}")

    def _is_rate_limited(self, response: requests.Response = None, error: Exception = None) -> bool:
        """检测是否被限流"""
        if error:
            error_msg = str(error).lower()
            keywords = [
                "remotedisconnected", "connection aborted",
                "too many", "rate limit", "频率", "限制",
                "timeout", "timed out"
            ]
            return any(kw in error_msg for kw in keywords)

        if response:
            # 检查响应状态
            if response.status_code == 429:
                return True
            # 检查响应内容
            try:
                data = response.json()
                if data.get("code") in [-1, -2, 403]:
                    return True
            except:
                pass

        return False

    def _build_hist_params(self, code: str, market: int, start_date: str, end_date: str) -> Dict:
        """构建历史数据请求参数"""
        return {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": self.token_manager.get_token(self.session),
            "klt": "101",  # 日线
            "fqt": "1",    # 前复权
            "secid": f"{market}.{code}",
            "beg": start_date,
            "end": end_date,
            "_": str(int(time.time() * 1000)),  # 时间戳防缓存
        }

    def _get_request_headers(self) -> Dict[str, str]:
        """获取增强的请求头（模拟真实浏览器）"""
        headers = self.fingerprint.copy()
        headers.update({
            "Referer": "https://quote.eastmoney.com/",
            "Origin": "https://quote.eastmoney.com",
            "Host": "push2his.eastmoney.com",
            "DNT": "1",
        })
        return headers

    def fetch_stock_history(
        self,
        code: str,
        start_date: str,
        end_date: str,
        proxies: Optional[Dict[str, str]] = None,
        secid_market: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        获取股票历史数据

        Args:
            code: 股票代码（6位数字）
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            proxies: 代理配置，如 {"http": "http://ip:port", "https": "http://ip:port"}
            secid_market: 强制指定东财 secid 的市场编号 (0=SZ/BJ, 1=SH)。
                          不传则自动推断。

        Returns:
            数据字典或 None

        Raises:
            ProxyTimeoutError: 代理超时/连接失败，需要上层切换 IP
        """
        # 判断市场
        if secid_market is not None:
            market = secid_market
        else:
            market = resolve_eastmoney_market(code)

        params = self._build_hist_params(code, market, start_date, end_date)

        try:
            # 限速等待
            self.rate_limiter.wait()

            # 每 50 次请求刷新一次指纹
            if self.stats["total_requests"] % 50 == 0:
                self._refresh_fingerprint()

            self.stats["total_requests"] += 1

            # 发起请求
            headers = self._get_request_headers()
            response = self.session.get(
                self.HIST_API,
                params=params,
                headers=headers,
                timeout=self.config.timeout,
                proxies=proxies,
            )

            # 检查响应
            if response.status_code == 200:
                # 空响应 → 可能被限流，视为代理失效
                if not response.text or not response.text.strip():
                    logger.warning(f"响应内容为空，可能被限流")
                    self.rate_limiter.on_failure(is_rate_limit=True)
                    raise ProxyTimeoutError("响应内容为空")

                try:
                    data = response.json()
                except Exception as json_err:
                    logger.error(f"JSON 解析失败: {json_err}")
                    self.rate_limiter.on_failure()
                    raise ProxyTimeoutError(f"JSON 解析失败: {json_err}")

                if data.get("data") and data["data"].get("klines"):
                    self.rate_limiter.on_success()
                    self.stats["success"] += 1
                    return data["data"]

                # data 存在但 klines 为空列表 → 该日期范围内无数据（正常）
                if data.get("data") is not None:
                    self.rate_limiter.on_success()
                    return None

            # 被限流 → 需要换 IP
            if self._is_rate_limited(response=response):
                self.stats["rate_limited"] += 1
                self.rate_limiter.on_failure(is_rate_limit=True)
                raise ProxyTimeoutError(f"被限流 HTTP {response.status_code}")

            # 其他 HTTP 错误
            self.rate_limiter.on_failure()
            logger.warning(f"请求失败: HTTP {response.status_code}")
            return None

        except ProxyTimeoutError:
            # 直接抛给上层处理
            raise

        except Exception as e:
            self.stats["failed"] += 1
            self.rate_limiter.on_failure(is_rate_limit=self._is_rate_limited(error=e))
            logger.warning(f"请求异常: {e}")
            # 连接超时/断开等 → 通知上层换 IP
            raise ProxyTimeoutError(f"连接异常: {e}")

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            "success_rate": f"{self.stats['success'] / max(1, self.stats['total_requests']) * 100:.1f}%",
            "current_delay": f"{self.rate_limiter.current_delay:.1f}s",
        }

    def reset_session(self, proxies: Optional[Dict[str, str]] = None):
        """重置 Session（用于长时间运行后刷新）"""
        self.session.close()
        self.session = self._create_session()
        self._refresh_fingerprint()
        self._warm_up(proxies=proxies)  # 重新获取 Cookie
        self.rate_limiter.reset()


def parse_kline_data(data: Dict) -> list:
    """
    解析 K 线数据

    Returns:
        列表，每项为 dict: {date, open, close, high, low, volume, amount, ...}
    """
    if not data or not data.get("klines"):
        return []

    result = []
    for line in data["klines"]:
        parts = line.split(",")
        if len(parts) >= 11:
            result.append({
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]),
                "amplitude": float(parts[7]),      # 振幅
                "change_pct": float(parts[8]),     # 涨跌幅
                "change": float(parts[9]),         # 涨跌额
                "turnover": float(parts[10]),      # 换手率
            })

    return result


# 测试代码
if __name__ == "__main__":
    logger.add(lambda msg: print(msg, end=""), level="INFO")

    crawler = EastMoneyCrawler(CrawlerConfig(
        min_delay=2.0,
        max_delay=5.0,
        max_retries=3,
    ))

    # 测试获取平安银行
    print("测试获取 000001 平安银行...")
    data = crawler.fetch_stock_history("000001", "20250101", "20250205")

    if data:
        klines = parse_kline_data(data)
        print(f"成功! 获取 {len(klines)} 条数据")
        if klines:
            print(f"最新: {klines[-1]}")
    else:
        print("失败")

    print(f"\n统计: {crawler.get_stats()}")
