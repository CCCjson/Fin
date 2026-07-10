"""
acquisition —— 数据获取层，全仓**唯一出网入口**。

引擎层（data_engine / knowledge_engine / news_engine / report_engine / …）只做
编排与存储，一切出网取数改调这里。分层见 `docs/CODING_STANDARDS.md` §0/§8：

    engines  →  acquisition  →  common / net
                （本层）        （代理池 / session / bounded 原语）

三类抓取器，职责三分：
- `BaseCrawler`         通用抓取（bounded + 通道策略 + 重试）
- `ReverseApiCrawler`   逆向类：按站点配置调用**已侦查好**的接口   [S6 落地]
- `ApiDiscoverer`       探测类：侦查**新站点**的接口结构           [S6 落地]

合法例外只有三类（§8.4）：localhost 内部服务（C++ 回测/撮合/MLX）、LLM API
（走 `llm_client`）、非 HTTP 协议数据源（pytdx TCP，包在 `markets/` 门面里）。
"""
from acquisition.channels import Channel
from acquisition.crawler.base import BaseCrawler

__all__ = ["Channel", "BaseCrawler"]
