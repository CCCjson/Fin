"""
websearch — 系统的「真·联网层」（移植 Scrapper 的搜索+读正文原语，无状态库形态）。

3 个原语，统一返回 {title, url, snippet, text, source}：
- web_search(query, max_results, site)  — DuckDuckGo 通用搜索
- sec_search(query, max_results, forms) — SEC EDGAR 全文检索
- read_url(url, max_chars, pdf_pages)   — 抓网页/PDF → 干净正文

一鱼两吃：被 agents/tools/knowledge_tools.py 包成 @tool 给 MoneyBill；被各引擎直接 import 共享。
"""
from acquisition.websearch.ddg import web_search
from acquisition.websearch.sec_edgar import sec_search
from acquisition.websearch.fetch import read_url

__all__ = ["web_search", "sec_search", "read_url"]
