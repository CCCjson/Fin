"""
BaseCrawler —— 通用抓取原语，全仓一切出网 HTTP 的门面。

## 它把两条铁律合进同一条路径

1. **有上限下载**（`net.bounded_get`）：响应流式封顶，JSON/HTML 超 50MB 截断、
   大文件分块落盘。防的是「无上限 `resp.json()` 把后端撑到 22GB」那次事故。
2. **国内失败只换 IP**（`net._rotate`）：`Channel.DOMESTIC` 的失败一律换快代理 IP
   重试，取不到 IP 就抛 `ProxyExhaustedError`，**绝不静默降级直连**。

在此之前这两条各活各的：`bounded_get` 只在知识库栈（7 个入口），行情栈拿的是裸
`requests.Response`；而行情栈里又有三处手写重试循环绕过了铁律。合到一起以后，
「抓东西」这件事只有一个入口，两条规矩自动都在。

## 三类抓取器

`BaseCrawler` 是通用类。另两类（后续切片落地）继承它：
- `ReverseApiCrawler`：按 `sites/` 站点配置调用**已侦查好**的接口
- `ApiDiscoverer`：侦查**新站点**的接口结构，产出站点配置
"""
import json
from typing import Any

from loguru import logger

from acquisition.channels import USER_AGENT, Channel, make_session
from net.bounded import Capped, bounded_get
from net.domestic import domestic_bounded_get


class BaseCrawler:
    """一个抓取器 = 一条通道 + 一套超时/重试/请求头。

    Example:
        >>> em = BaseCrawler(channel=Channel.DOMESTIC, timeout=8)
        >>> data = em.get_json("https://push2.eastmoney.com/api/qt/ulist.np/get",
        ...                    params={"secids": "1.000001"})
    """

    def __init__(
        self,
        *,
        channel: Channel,
        timeout: int = 15,
        max_rounds: int = 5,
        prefer_direct: bool = False,
        impersonate: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            channel: 出网通道，见 `acquisition.channels.Channel`。必填，无默认——
                「这次从哪个出口出去」是调用方必须回答的问题。
            timeout: 单次请求超时（秒）。
            max_rounds: 仅 DOMESTIC：最多换几个 IP。
            prefer_direct: 仅 DOMESTIC：第 0 轮先试直连，失败再上快代理（低频接口
                省额度用）。这是**前进式**的显式直连，不是降级；一旦直连失败就必须
                走代理，代理取不到 IP 照样抛。默认 False——忘了传参不会偷偷直连。
            impersonate: 仅 OVERSEAS/DIRECT：用 curl_cffi 伪装 TLS 指纹（强反爬源）。
            headers: 每次请求附带的默认请求头。
        """
        if channel is Channel.DOMESTIC and impersonate:
            raise ValueError("DOMESTIC 通道不支持 impersonate（逐轮换 IP，session 由 net 层造）")
        self.channel = channel
        self.timeout = timeout
        self.max_rounds = max_rounds
        self.prefer_direct = prefer_direct
        self.impersonate = impersonate
        self.headers = {"User-Agent": USER_AGENT, **(headers or {})}

    # ── 核心：一次有上限的 GET ────────────────────────────────────────────

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        mode: str = "bytes",
        max_bytes: int | None = None,
        file_suffix: str = ".bin",
    ) -> Capped | None:
        """GET 一个 URL，响应封顶。

        Args:
            mode: 'bytes' 进内存（截断即 `truncated`）/ 'file' 分块落盘 /
                'auto' 按 content-type 判 PDF。
        Returns:
            `Capped`；失败返回 None。
        Raises:
            ProxyExhaustedError: 仅 DOMESTIC，配了快代理却取不到 IP / 换满仍全败。
        """
        merged = {**self.headers, **(headers or {})}

        if self.channel is Channel.DOMESTIC:
            return domestic_bounded_get(
                url,
                mode=mode,
                params=params,
                headers=merged,
                timeout=self.timeout,
                max_rounds=self.max_rounds,
                prefer_direct=self.prefer_direct,
                max_bytes=max_bytes,
                file_suffix=file_suffix,
            )

        session = make_session(self.channel, impersonate=self.impersonate)
        try:
            return bounded_get(
                session, url, mode=mode, params=params, headers=merged,
                timeout=self.timeout, max_bytes=max_bytes, file_suffix=file_suffix,
            )
        except Exception as e:  # noqa: BLE001  # 海外通道无 IP 可换，失败即失败
            logger.warning(f"{self.channel.value} GET 失败 {url}: {e}")
            return None

    def get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> Any | None:
        """GET 并解析 JSON。截断/超限的响应**不解析**（`usable_json` 为假），返回 None。"""
        cap = self.get(url, params=params, headers=headers, mode="bytes")
        if cap is None or cap.data is None:
            return None
        if not cap.usable_json:
            logger.warning(f"响应超上限（truncated={cap.truncated} too_large={cap.too_large}），"
                           f"JSON 不完整，拒绝解析：{url}")
            return None
        try:
            return json.loads(cap.data)
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning(f"JSON 解析失败 {url}: {e}")
            return None

    def download(self, url: str, *, suffix: str = ".bin",
                 params: dict | None = None) -> Capped | None:
        """下载大文件到临时文件（内存恒定）。`Capped.path` 用完请 `os.unlink`。"""
        return self.get(url, params=params, mode="file", file_suffix=suffix)
