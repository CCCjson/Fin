"""
knowledge_engine 工具 — 注册进 agents.REGISTRY，供 MoneyBill 调用。

从 knowledge_engine/tools.py 搬来（Phase 4 分层治理）：注册本该在工具层，
不该让引擎层反向 import agents.registry。这里全是薄适配器，真正的业务逻辑
（尤其 scrape 的侦查/取数/cookie 自愈编排）在 acquisition.crawler.reverse。

P0：search_knowledge（检索本地已沉淀知识库，带引用）。
P1：web_search / sec_search / read_url（系统的真·联网层，移植自 Scrapper）。

域8 收口：7 个工具全部走 args_model（校验+schema 单一数据源）+ ToolEnvelope
（技术态 ok / 业务态 business_result 正交），入参 top_k/max_results 按词典改 limit。
"""
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field

from acquisition.crawler.reverse import scrape as _scrape
from agents.registry import tool
from agents.tool_envelope import ErrorCode, ToolEnvelope


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(..., min_length=1, description="检索问题或关键词")
    limit: int = Field(5, ge=1, le=50, description="返回片段数，默认 5")
    source_type: str = Field(
        "all", description="限定来源类型（paper/research/financial/internal/news/all），默认 all")


@tool(
    name="search_knowledge",
    description=(
        "从【外置金融大脑】本地知识库语义检索研报/论文/财报/内部数据片段，做研判、选股、"
        "答问时引用证据。返回最相关片段及出处编号。优先用它查已沉淀的知识；"
        "若库里没有再考虑联网搜索。"
    ),
    args_model=SearchKnowledgeArgs,
    category="knowledge",
    group="knowledge_web",
)
def search_knowledge(query: str, limit: int = 5, source_type: str = "all") -> ToolEnvelope:
    from knowledge_engine.retriever import Retriever
    res = Retriever.get_instance().search(query, top_k=limit, source_type=source_type)
    return ToolEnvelope(data=res["summary"], widget=res.get("widget"))


# ============ P1：真·联网层 ============

class WebSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, description="搜索词")
    limit: int = Field(8, ge=1, le=50, description="返回条数，默认 8")
    site: Optional[str] = Field(None, description="可选，限定站点如 arxiv.org / sec.gov")


@tool(
    name="web_search",
    description=(
        "联网用 DuckDuckGo 通用搜索实时网页（新闻/资料/行情解读/论文）。"
        "本地 search_knowledge 查不到、或需要最新外部信息时再用。"
        "返回标题+摘要+链接；要读全文请把某个 url 交给 read_url。"
        "可用 site 参数定向站点（如 arxiv.org、sec.gov）。"
    ),
    args_model=WebSearchArgs,
    category="websearch",
    group="knowledge_web",
)
def web_search(query: str, limit: int = 8, site: Optional[str] = None) -> ToolEnvelope:
    from acquisition.websearch import web_search as _ws
    res = _ws(query, max_results=limit, site=site or None)
    items = [{"title": r["title"], "snippet": r["snippet"][:200], "url": r["url"]} for r in res]
    if not items:
        return ToolEnvelope(business_result="negative",
                            message=f"「{query}」没搜到网页结果")
    return ToolEnvelope(data={"count": len(items), "results": items})


class SecSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, description="全文检索词（公司/主题/关键词）")
    limit: int = Field(10, ge=1, le=50, description="返回条数，默认 10")
    forms: Optional[str] = Field(None, description="可选，限定表单类型如 '10-K,20-F'")


@tool(
    name="sec_search",
    description=(
        "检索 SEC EDGAR 美股上市公司法定披露文件全文（10-K/20-F/8-K/S-1 等）。"
        "查美股公司财报/年报/重大事项时用。返回命中文件标题+链接；"
        "要读正文请把 url 交给 read_url。"
    ),
    args_model=SecSearchArgs,
    category="websearch",
    group="knowledge_web",
)
def sec_search(query: str, limit: int = 10, forms: Optional[str] = None) -> ToolEnvelope:
    from acquisition.websearch import sec_search as _ss
    res = _ss(query, max_results=limit, forms=forms or None)
    filings = [{"title": r["title"], "url": r["url"]} for r in res]
    if not filings:
        return ToolEnvelope(business_result="negative",
                            message=f"SEC EDGAR 没检索到「{query}」相关披露文件")
    return ToolEnvelope(data={"count": len(filings), "filings": filings})


class ReadUrlArgs(BaseModel):
    url: str = Field(..., min_length=1, description="要读取的网页或 PDF 链接")
    max_chars: int = Field(8000, ge=1, le=100000, description="正文截断字符数，默认 8000")


@tool(
    name="read_url",
    description=(
        "抓取一个网页或 PDF 的正文（自动去导航/页脚噪声）。"
        "配合 web_search/sec_search：先搜到链接，再用本工具读全文做研判。"
    ),
    args_model=ReadUrlArgs,
    category="websearch",
    group="knowledge_web",
)
def read_url(url: str, max_chars: int = 8000) -> ToolEnvelope:
    from acquisition.websearch import read_url as _ru
    doc = _ru(url, max_chars=max_chars)
    if not doc["text"]:
        from acquisition.browser.diagnose import diagnose
        d = diagnose(url=url)  # 空正文 → 盾页/需cookie/非目标内容
        return ToolEnvelope(
            business_result="negative",
            data={"message": "无法读取该 URL 正文", "url": url,
                  "why": d["reason"], "how": d["suggestion"]})
    return ToolEnvelope(data={"title": doc["title"], "url": url,
                              "chars": len(doc["text"]), "text": doc["text"]})


class LoginSiteArgs(BaseModel):
    url: str = Field(..., min_length=1, description="要登录的站点 URL（登录页或首页均可）")
    timeout_s: int = Field(300, ge=1, le=3600, description="最长等待登录秒数，默认 300")


@tool(
    name="login_site",
    description=(
        "【人工登录】打开有头浏览器 + 目标站，让 Jason 手动登录一次，登录后自动抓取 cookie "
        "存进站点配置，然后关闭浏览器。用于：某站需要登录、或登录态彻底过期导致 fetch_api "
        "刷不出有效 cookie 时。登录需 Jason 在电脑前——调用前先告诉他要登哪个站。"
    ),
    args_model=LoginSiteArgs,
    category="websearch",
    group="knowledge_web",
)
def login_site(url: str, timeout_s: int = 300) -> ToolEnvelope:
    from acquisition.config import get_playwright_enabled
    if not get_playwright_enabled():
        return ToolEnvelope(
            business_result="negative",
            message="浏览器爬虫未启用：请在 .env 设 KNOWLEDGE_PLAYWRIGHT_ENABLED=true")
    from acquisition.browser.launch import run_off_loop
    from acquisition.browser.manual_login import manual_login
    try:
        r = run_off_loop(manual_login, url, timeout_s)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"login_site 失败：{e}")
        return ToolEnvelope(
            ok=False, error_code=ErrorCode.INTERNAL_ERROR,
            error_detail={"exception_type": type(e).__name__, "message": str(e)},
            message=f"打开浏览器登录失败：{str(e)[:120]}")
    if not r.get("saved"):
        return ToolEnvelope(
            business_result="negative",
            data={"saved": False, "domain": r.get("domain")},
            message="没抓到登录 cookie（可能没登录就关了窗口）")
    return ToolEnvelope(
        data={"saved": True, "domain": r["domain"], "cookie_count": r["cookie_count"]},
        message=f"已抓取 {r['domain']} 登录 cookie（{r['cookie_count']} 个）并存配置，"
                f"现在可以对该站 scrape 取数了。")


class ScrapeArgs(BaseModel):
    url: str = Field(..., min_length=1,
                     description="目标网页完整 URL（首选，最可靠），或网站域名/名称")
    want: Optional[str] = Field(
        None, description="想要什么数据的自然语言描述，如「实时行情」「财务数据」「资金流」；给 endpoint 时可省")
    endpoint: Optional[str] = Field(
        None, description="可选，精确指定接口名（侦查返回的 name）；省略则按 want 智能猜")
    params: Optional[dict] = Field(
        None, description="可选，查询参数覆盖（如 {\"symbol\":\"SH600519\"}）精确复用同站接口；省略用侦查时默认参数")


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
    args_model=ScrapeArgs,
    category="websearch",
    group="knowledge_web",
)
def scrape(url: str, want: Optional[str] = None, endpoint: Optional[str] = None,
           params: Optional[dict] = None,
           session_id: Optional[str] = None,
           on_progress=None) -> ToolEnvelope:
    raw = _scrape(url, want=want, endpoint=endpoint, params=params,
                  session_id=session_id, on_progress=on_progress)
    # _scrape 统一返回 {"summary": {"count": N, ...}, "data"?: ...}；count==0 = 没侦查到/取数失败
    count = (raw.get("summary") or {}).get("count") if isinstance(raw, dict) else None
    if count == 0:
        return ToolEnvelope(business_result="negative", data=raw)
    return ToolEnvelope(data=raw)


class ListAlphaIdeasArgs(BaseModel):
    status: str = Field(
        "all", description="按状态筛选（proposed/backtesting/validated/rejected/all），默认 all")
    limit: int = Field(10, ge=1, le=100, description="返回条数，默认 10")


@tool(
    name="list_alpha_ideas",
    description=(
        "列出【外置金融大脑】从论文提炼出的可落地 alpha 策略思路（含因子逻辑、回测目标、得分）。"
        "用户问「最近挖到什么策略/alpha 想法」「论文提炼了哪些因子」时用。"
    ),
    args_model=ListAlphaIdeasArgs,
    category="knowledge",
    group="knowledge_web",
)
def list_alpha_ideas(status: str = "all", limit: int = 10) -> ToolEnvelope:
    from knowledge_engine.store import KnowledgeStore
    ideas = KnowledgeStore().list_ideas(status=None if status == "all" else status, limit=limit)
    items = [{
        "title": i.title, "hypothesis": i.hypothesis,
        "optimization_goal": i.optimization_goal, "status": i.status,
        "best_composite_score": i.best_composite_score,
    } for i in ideas]
    if not items:
        return ToolEnvelope(business_result="negative",
                            message="知识库里还没有已提炼的 alpha 策略思路")
    return ToolEnvelope(data={"count": len(items), "ideas": items})
