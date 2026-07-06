"""
knowledge_engine 工具 — 注册进 agents.REGISTRY，供 MoneyBill 调用。

从 knowledge_engine/tools.py 搬来（Phase 4 分层治理）：注册本该在工具层，
不该让引擎层反向 import agents.registry。这里全是薄适配器，真正的业务逻辑
（尤其 scrape 的侦查/取数/cookie 自愈编排）在 knowledge_engine.reverse_api。

P0：search_knowledge（检索本地已沉淀知识库，带引用）。
P1：web_search / sec_search / read_url（系统的真·联网层，移植自 Scrapper）。
"""
from typing import Optional

from loguru import logger

from agents.registry import tool
from knowledge_engine.reverse_api import scrape as _scrape


@tool(
    name="search_knowledge",
    description=(
        "从【外置金融大脑】本地知识库语义检索研报/论文/财报/内部数据片段，做研判、选股、"
        "答问时引用证据。返回最相关片段及出处编号。优先用它查已沉淀的知识；"
        "若库里没有再考虑联网搜索。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索问题或关键词"},
            "top_k": {"type": "integer", "description": "返回片段数，默认 5"},
            "source_type": {
                "type": "string",
                "enum": ["paper", "research", "financial", "internal", "news", "all"],
                "description": "限定来源类型，默认 all",
            },
        },
        "required": ["query"],
    },
    category="knowledge",
    group="knowledge_web",
)
def search_knowledge(query: str, top_k: int = 5, source_type: str = "all") -> dict:
    from knowledge_engine.retriever import Retriever
    res = Retriever.get_instance().search(query, top_k=top_k, source_type=source_type)
    out = {"summary": res["summary"]}
    if res.get("widget"):
        out["widget"] = res["widget"]
    return out


# ============ P1：真·联网层 ============

@tool(
    name="web_search",
    description=(
        "联网用 DuckDuckGo 通用搜索实时网页（新闻/资料/行情解读/论文）。"
        "本地 search_knowledge 查不到、或需要最新外部信息时再用。"
        "返回标题+摘要+链接；要读全文请把某个 url 交给 read_url。"
        "可用 site 参数定向站点（如 arxiv.org、sec.gov）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词"},
            "max_results": {"type": "integer", "description": "返回条数，默认 8"},
            "site": {"type": "string", "description": "可选，限定站点如 arxiv.org / sec.gov"},
        },
        "required": ["query"],
    },
    category="websearch",
    group="knowledge_web",
)
def web_search(query: str, max_results: int = 8, site: str = None) -> dict:
    from knowledge_engine.websearch import web_search as _ws
    res = _ws(query, max_results=max_results, site=site or None)
    items = [{"title": r["title"], "snippet": r["snippet"][:200], "url": r["url"]} for r in res]
    return {"summary": {"count": len(items), "results": items}}


@tool(
    name="sec_search",
    description=(
        "检索 SEC EDGAR 美股上市公司法定披露文件全文（10-K/20-F/8-K/S-1 等）。"
        "查美股公司财报/年报/重大事项时用。返回命中文件标题+链接；"
        "要读正文请把 url 交给 read_url。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "全文检索词（公司/主题/关键词）"},
            "max_results": {"type": "integer", "description": "返回条数，默认 10"},
            "forms": {"type": "string", "description": "可选，限定表单类型如 '10-K,20-F'"},
        },
        "required": ["query"],
    },
    category="websearch",
    group="knowledge_web",
)
def sec_search(query: str, max_results: int = 10, forms: str = None) -> dict:
    from knowledge_engine.websearch import sec_search as _ss
    res = _ss(query, max_results=max_results, forms=forms or None)
    filings = [{"title": r["title"], "url": r["url"]} for r in res]
    return {"summary": {"count": len(filings), "filings": filings}}


@tool(
    name="read_url",
    description=(
        "抓取一个网页或 PDF 的正文（自动去导航/页脚噪声）。"
        "配合 web_search/sec_search：先搜到链接，再用本工具读全文做研判。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要读取的网页或 PDF 链接"},
            "max_chars": {"type": "integer", "description": "正文截断字符数，默认 8000"},
        },
        "required": ["url"],
    },
    category="websearch",
    group="knowledge_web",
)
def read_url(url: str, max_chars: int = 8000) -> dict:
    from knowledge_engine.websearch import read_url as _ru
    doc = _ru(url, max_chars=max_chars)
    if not doc["text"]:
        from knowledge_engine.browser.diagnose import diagnose
        d = diagnose(url=url)  # 空正文 → 盾页/需cookie/非目标内容
        return {"summary": {"message": "无法读取该 URL 正文", "url": url,
                            "why": d["reason"], "how": d["suggestion"]}}
    return {"summary": {"title": doc["title"], "url": url,
                        "chars": len(doc["text"]), "text": doc["text"]}}


@tool(
    name="login_site",
    description=(
        "【人工登录】打开有头浏览器 + 目标站，让 Jason 手动登录一次，登录后自动抓取 cookie "
        "存进站点配置，然后关闭浏览器。用于：某站需要登录、或登录态彻底过期导致 fetch_api "
        "刷不出有效 cookie 时。登录需 Jason 在电脑前——调用前先告诉他要登哪个站。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要登录的站点 URL（登录页或首页均可）"},
            "timeout_s": {"type": "integer", "description": "最长等待登录秒数，默认 300"},
        },
        "required": ["url"],
    },
    category="websearch",
    group="knowledge_web",
)
def login_site(url: str, timeout_s: int = 300) -> dict:
    from knowledge_engine.config import get_playwright_enabled
    if not get_playwright_enabled():
        return {"summary": {"saved": False,
                            "message": "浏览器爬虫未启用：请在 .env 设 KNOWLEDGE_PLAYWRIGHT_ENABLED=true"}}
    from knowledge_engine.browser.launch import run_off_loop
    from knowledge_engine.browser.manual_login import manual_login
    try:
        r = run_off_loop(manual_login, url, timeout_s)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"login_site 失败：{e}")
        return {"summary": {"saved": False, "message": f"打开浏览器登录失败：{str(e)[:120]}"}}
    if not r.get("saved"):
        return {"summary": {"saved": False, "domain": r.get("domain"),
                            "message": "没抓到登录 cookie（可能没登录就关了窗口）"}}
    return {"summary": {"saved": True, "domain": r["domain"],
                        "cookie_count": r["cookie_count"],
                        "message": f"已抓取 {r['domain']} 登录 cookie（{r['cookie_count']} 个）并存配置，"
                                   f"现在可以对该站 scrape 取数了。"}}


@tool(
    name="scrape",
    description=(
        "【一句话拿网站数据·逆向爬取唯一入口】给一个网页 URL（或网站名）+ 想要什么数据，自动用浏览器"
        "侦查该站背后的真实数据接口并返回结构化数据。内部已把「侦查+取数+cookie 自愈」串成一步——"
        "**无需分步**。首次抓某站稍慢（开浏览器过盾+嗅探），之后同站秒回（读缓存配置直接 curl，不再开浏览器）。"
        "用户说「帮我从 X 网站拿 Y 数据」「爬一下这个页面的数据」时首选本工具。三种用法："
        "① 只给 url[+want]：自动侦查并按 want 猜最相关接口，附完整数据；"
        "② 同站换条件：把 params 传进来（如 {\"symbol\":\"SH600519\"}）精确复用，秒回不重侦查；"
        "③ 指定 endpoint（侦查返回里的接口名）取特定接口。需登录的站先用 login_site 人工登一次再 scrape。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标网页完整 URL（首选，最可靠），或网站域名/名称"},
            "want": {"type": "string", "description": "想要什么数据的自然语言描述，如「实时行情」「财务数据」「资金流」；给 endpoint 时可省"},
            "endpoint": {"type": "string", "description": "可选，精确指定接口名（侦查返回的 name）；省略则按 want 智能猜"},
            "params": {"type": "object", "description": "可选，查询参数覆盖（如 {\"symbol\":\"SH600519\"}）精确复用同站接口；省略用侦查时默认参数"},
        },
        "required": ["url"],
    },
    category="websearch",
    group="knowledge_web",
)
def scrape(url: str, want: str = None, endpoint: str = None, params: dict = None,
           session_id: Optional[str] = None,
           on_progress=None) -> dict:
    return _scrape(url, want=want, endpoint=endpoint, params=params,
                    session_id=session_id, on_progress=on_progress)


@tool(
    name="list_alpha_ideas",
    description=(
        "列出【外置金融大脑】从论文提炼出的可落地 alpha 策略思路（含因子逻辑、回测目标、得分）。"
        "用户问「最近挖到什么策略/alpha 想法」「论文提炼了哪些因子」时用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string",
                       "enum": ["proposed", "backtesting", "validated", "rejected", "all"],
                       "description": "按状态筛选，默认 all"},
            "limit": {"type": "integer", "description": "返回条数，默认 10"},
        },
    },
    category="knowledge",
    group="knowledge_web",
)
def list_alpha_ideas(status: str = "all", limit: int = 10) -> dict:
    from knowledge_engine.store import KnowledgeStore
    ideas = KnowledgeStore().list_ideas(status=None if status == "all" else status, limit=limit)
    items = [{
        "title": i.title, "hypothesis": i.hypothesis,
        "optimization_goal": i.optimization_goal, "status": i.status,
        "best_composite_score": i.best_composite_score,
    } for i in ideas]
    return {"summary": {"count": len(items), "ideas": items}}
