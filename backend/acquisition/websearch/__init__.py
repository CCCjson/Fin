"""
websearch — 系统的「真·联网层」（移植 Scrapper 的搜索+读正文原语，无状态库形态）。

3 个原语，统一返回 {title, url, snippet, text, source}：
- web_search(query, max_results, site)  — DuckDuckGo 通用搜索
- sec_search(query, max_results, forms) — SEC EDGAR 全文检索
- read_url(url, max_chars, pdf_pages)   — 抓网页/PDF → 干净正文

外加一条专用出网原语（返回纯文本，非统一结构）：
- fetch_pdf_text(url, max_pages, headers) — curl_cffi 直连抓 PDF 直链抽前 N 页
  （给 cninfo 法定披露 / dfcfw 研报全文这类「已探明无盾、直连即取」的 PDF 用；
  把 curl_cffi 出网收进 acquisition，ingest source 不再自持 curl_cffi）

一鱼两吃：被 agents/tools/knowledge_tools.py 包成 @tool 给 MoneyBill；被各引擎直接 import 共享。
"""
from acquisition.websearch.ddg import web_search
from acquisition.websearch.sec_edgar import sec_search
from acquisition.websearch.fetch import read_url, fetch_pdf_text

__all__ = ["web_search", "sec_search", "read_url", "fetch_pdf_text"]
