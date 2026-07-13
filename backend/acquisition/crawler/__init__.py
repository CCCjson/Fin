"""抓取器三类：通用 / 逆向 / 探测。站点配置真源在 `sites/`。

三类**平级**，各自复用 net 原语，互不继承：
- `BaseCrawler`       通用：一次有上限的 HTTP GET（bounded + 通道 + 换 IP 重试）
- `ReverseApiCrawler` 逆向：按 `sites/` 配置 curl_cffi 直打已侦查好的接口
- `ApiDiscoverer`     探测：浏览器嗅探新站点接口结构，产出 `sites/` 配置
"""
from acquisition.crawler.base import BaseCrawler
from acquisition.crawler.discover import ApiDiscoverer
from acquisition.crawler.reverse import ReverseApiCrawler

__all__ = ["BaseCrawler", "ReverseApiCrawler", "ApiDiscoverer"]
