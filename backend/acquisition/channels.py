"""
出网通道 —— 「这次请求走哪条网络」提升为一等公民。

在此之前，通道是**隐式**的：国内路径靠调 `net.domestic_*` 表达、海外路径靠
`yfinance`/`finnhub` 偷偷读进程 env 里的 Clash 代理、直连则表现为「谁也没设
proxies」。三者混在一起的结果是没人说得清某一行代码到底从哪个出口出去——
铁律（国内失败只换 IP）也就没法在结构上强制。

现在三条通道显式命名，抓任何东西都必须先声明走哪条：

| 通道 | 出口 | 失败策略 |
|---|---|---|
| `DOMESTIC` | 快代理池（快代理 IP 轮换） | 只换 IP 重试，**任何场景禁止降级直连**（铁律） |
| `OVERSEAS` | 本地直连优先，不通走 Shadowrocket/Clash | 探测缓存，直连/代理二选一 |
| `DIRECT`   | 显式直连，不读任何代理 env | 无 |

`DIRECT` 是**显式点头**，不是「取不到代理就退化」。铁律禁止的正是后者。
"""
from enum import Enum
from typing import Any, Final

from net.overseas import get_scraper_proxy, resolve_overseas


class Channel(str, Enum):
    """出网通道。抓取器构造时必须显式声明。"""

    DOMESTIC = "domestic"
    OVERSEAS = "overseas"
    DIRECT = "direct"


# 强反爬源用的 TLS 指纹（chrome131 在当前 curl_cffi 有 TLS bug，统一 chrome120）
# Final 让 mypy 收窄成 Literal["chrome120"]，才对得上 curl_cffi 的 impersonate 签名
IMPERSONATE: Final = "chrome120"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def resolve_overseas_proxy() -> str | None:
    """海外通道本次该用的代理 URL，None = 直连。

    优先级：强反爬专用代理 `KNOWLEDGE_SCRAPER_PROXY` → 海外自适配 `resolve_overseas()`
    （先探本地能否直连海外，通就直连、不通走 Shadowrocket）。
    """
    return get_scraper_proxy() or resolve_overseas()


def make_session(channel: Channel, *, impersonate: bool = False) -> Any:
    """按通道造 session。

    仅 `OVERSEAS` / `DIRECT` 用得上——`DOMESTIC` 的 session 由 `net.domestic_*`
    在每一轮换 IP 时现造（每轮代理不同，session 不能复用）。

    Args:
        impersonate: 走 curl_cffi 伪装 TLS 指纹（强反爬源），否则 plain requests。
    """
    if channel is Channel.DOMESTIC:
        raise ValueError("DOMESTIC 通道的 session 由 net.domestic_* 逐轮创建，不要在这里造")

    proxy = None if channel is Channel.DIRECT else resolve_overseas_proxy()

    s: Any   # curl_cffi.Session 与 requests.Session 无共同基类，只在 duck-type 上一致
    if impersonate:
        from curl_cffi import requests as cffi
        s = cffi.Session(impersonate=IMPERSONATE, trust_env=False)
    else:
        import requests
        s = requests.Session()
        s.trust_env = False   # requests 这个足够禁用 env 代理

    # 显式设代理：有则走它，无则空串强制直连。
    # curl_cffi 的 trust_env=False 不够可靠（C 层仍读 env 代理），必须显式空串
    # 才能 100% 覆盖系统 ALL_PROXY/HTTPS_PROXY。
    s.proxies = {"http": proxy, "https": proxy} if proxy else {"http": "", "https": ""}
    return s
