"""
acquisition —— 数据获取层，全仓**唯一出网入口**。

引擎层（data_engine / knowledge_engine / news_engine / report_engine / …）只做
编排与存储，一切出网取数改调这里。分层见 `docs/CODING_STANDARDS.md` §0/§8：

    engines  →  acquisition  →  common / net
                （本层）        （代理池 / session / bounded 原语）

三类抓取器**平级**，职责三分，各自复用 net 原语，互不继承（13.4-2 S6 已落地）：
- `BaseCrawler`         通用抓取（bounded + 通道策略 + 换 IP 重试）
- `ReverseApiCrawler`   逆向类：按 `crawler/sites/` 配置 curl_cffi 直打已侦查好的接口
- `ApiDiscoverer`       探测类：浏览器嗅探新站点接口结构，产出 `crawler/sites/` 配置

合法例外只有三类（§8.4）：localhost 内部服务（C++ 回测/撮合/MLX）、LLM API
（走 `llm_client`）、非 HTTP 协议数据源（pytdx TCP，包在 `markets/` 门面里）。
"""
from acquisition.channels import Channel
from acquisition.crawler.base import BaseCrawler
from acquisition.crawler.discover import ApiDiscoverer
from acquisition.crawler.reverse import ReverseApiCrawler

__all__ = ["Channel", "BaseCrawler", "ReverseApiCrawler", "ApiDiscoverer"]
