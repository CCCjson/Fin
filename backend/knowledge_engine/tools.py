"""
knowledge_engine 工具 — 注册进 agents.REGISTRY，供 MoneyBill 调用。

P0：search_knowledge（检索本地已沉淀知识库，带引用）。
P1：web_search / sec_search / read_url（系统的真·联网层，移植自 Scrapper）。

注册靠 @tool 装饰器副作用，需被 import 触发（见 agents/tools/__init__.py 的接线）。
"""
from agents.registry import tool


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
)
def read_url(url: str, max_chars: int = 8000) -> dict:
    from knowledge_engine.websearch import read_url as _ru
    doc = _ru(url, max_chars=max_chars)
    if not doc["text"]:
        return {"summary": f"无法读取该 URL 正文（可能反爬/无内容）：{url}"}
    return {"summary": {"title": doc["title"], "url": url,
                        "chars": len(doc["text"]), "text": doc["text"]}}


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
