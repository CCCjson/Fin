"""
券商研报源（元数据/要点）— akshare 东财研报中心 → 知识文档（source_type=research）。

先探站结论（2026-07）：东财研报中心元数据（标题/机构/评级/目标价/盈利预测）经
akshare `stock_research_report_em` 免费直连可取；全文 PDF (pdf.dfcfw.com) 有 JS 反爬盾，
但 curl_cffi(chrome120指纹) + referer 头即可绕过，不需要 headless 浏览器。
→ 2026-07 全量补齐初版：全市场直接开全文模式（full_text=True 默认），分批跑 + 盯失败率。
→ 2026-07 提速改造：默认改为**两阶段**——全市场主链路默认 full_text=False（仅元数据要点，
  快、几乎不吃 CPU），把最重的 PDF 全文解析（fitz）从主循环摘掉；需要全文时显式传
  full_text=True，或跑完元数据后走 upgrade_existing_to_full_text 按需补齐（doc_id 幂等）。

⚠️ 国内源必须 Clash-无关：改走 net.domestic.domestic_akshare 包一层（快代理轮换+
直连兜底）。不能只靠 apply_proxy_env()——那个仅在判定为直连模式时清代理 env，
Clash 存活时不会纠正 .env 里可能配错端口的残留代理值，仍会 ProxyError（已现场验证）。
"""
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import pandas as pd
from loguru import logger

from knowledge_engine.ingest import IngestPipeline
from knowledge_engine.store import KnowledgeStore, make_doc_id


def _parse_date(s: str) -> Optional[datetime]:
    """'YYYY-MM-DD' 前10位解析，脏日期/空值返回 None，不让摄入因此失败。"""
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _fmt_num(v) -> str:
    try:
        f = float(v)
        return f"{f:.2f}" if f else "—"
    except (ValueError, TypeError):
        return "—"


def _report_to_text(row) -> str:
    """把一条研报元数据拼成自然语言要点（便于语义检索 + LLM 引用）。"""
    name = row.get("股票简称", "")
    code = row.get("股票代码", "")
    org = row.get("机构", "")
    rating = row.get("东财评级", "")
    date = str(row.get("日期", ""))[:10]
    title = row.get("报告名称", "")
    industry = row.get("行业", "")

    parts = [f"{org} {date} 研报《{title}》：对 {name}（{code}）给出「{rating}」评级。"]
    # 盈利预测（东财给的 2026-2028 EPS/PE）
    fc = []
    for y in ("2026", "2027", "2028"):
        eps = row.get(f"{y}-盈利预测-收益")
        pe = row.get(f"{y}-盈利预测-市盈率")
        if eps is not None or pe is not None:
            fc.append(f"{y}年 预测EPS {_fmt_num(eps)}元 / PE {_fmt_num(pe)}")
    if fc:
        parts.append("盈利预测：" + "；".join(fc) + "。")
    if industry:
        parts.append(f"所属行业：{industry}。")
    return " ".join(parts)


def _fetch_pdf_text(url: str, max_pages: int = 20) -> str:
    """
    取东财研报全文 PDF 正文。先探站结论：`pdf.dfcfw.com` 的"JS 盾"用
    curl_cffi(chrome120 指纹) + referer 头即可过，**不用浏览器**，很快。
    """
    if not url:
        return ""
    try:
        from curl_cffi import requests as cffi
        import fitz
        s = cffi.Session(impersonate="chrome120")
        r = s.get(url, headers={"referer": "https://data.eastmoney.com/"},
                  timeout=30, proxies={"http": "", "https": ""})
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            return ""
        doc = fitz.open(stream=r.content, filetype="pdf")
        n = min(max_pages, doc.page_count)
        text = "\n".join(doc[i].get_text() for i in range(n))
        doc.close()
        return text
    except Exception as e:  # noqa: BLE001
        logger.debug(f"研报 PDF 抽全文失败 {url}: {e}")
        return ""


def ingest_research_reports(
    symbols: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit_per_symbol: int = 60,
    full_text: bool = False,
) -> Dict[str, Any]:
    """
    摄入一批股票的券商研报。
    symbols: 股票代码列表，支持 '000001' 或 '000001.SZ'（自动去后缀）。
    start_date/end_date: 'YYYYMMDD'，限定研报发布日期窗口（东财接口本身不支持按日期查询，
        硬编码拉全部历史，这里客户端过滤）。默认 start_date=今天-4年，end_date=今天。
    limit_per_symbol: 时间窗口过滤后再截断的安全阀（防止高覆盖度股票拖爆单次调用），默认 60。
    full_text: False=仅元数据要点（快、省 CPU，默认，两阶段第一阶段）；True=拉 PDF 全文入库
        （多片切嵌，慢，吃 CPU）。全市场覆盖建议先默认跑元数据，再对需要的股票走
        upgrade_existing_to_full_text 补全文。
    """
    import akshare as ak
    from net.domestic import domestic_akshare

    today = datetime.now()
    start_dt = _parse_date(start_date) or (today - timedelta(days=365 * 4))
    end_dt = _parse_date(end_date) or today

    pipeline = IngestPipeline()
    ingested = skipped = failed = 0

    for sym in symbols:
        code = sym.split(".")[0]
        try:
            df = domestic_akshare(ak.stock_research_report_em, symbol=code)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"研报列表拉取失败 {sym}: {e}")
            failed += 1
            continue
        if df is None or df.empty:
            continue

        df = df.copy()
        df["_日期dt"] = pd.to_datetime(df["日期"], errors="coerce")
        df = df[(df["_日期dt"] >= start_dt) & (df["_日期dt"] <= end_dt)]
        df = df.sort_values("_日期dt", ascending=False)
        if df.empty:
            continue

        rows = df.head(limit_per_symbol).to_dict("records")
        for row in rows:
            summary = _report_to_text(row)                # 元数据要点（机构/评级/预测）
            pdf = row.get("报告PDF链接") or ""
            # 唯一键：优先 PDF 链接（每篇唯一），否则 代码:机构:日期:标题
            native_id = pdf or f"{code}:{row.get('机构','')}:{str(row.get('日期',''))[:10]}:{row.get('报告名称','')}"

            # 全文模式：要点做导语 + PDF 正文；正常多片切。失败退回仅要点。
            body = _fetch_pdf_text(pdf) if (full_text and pdf) else ""
            if body:
                text = f"{summary}\n\n【研报全文】\n{body}"
                pre_chunked = None                        # 交给 chunk_text 多片切
            else:
                text = summary
                pre_chunked = [{"seq": 0, "text": summary, "token_count": None}]

            res = pipeline.ingest_text(
                source_type="research",
                title=f"{row.get('机构','')}《{row.get('报告名称','')}》",
                text=text,
                url=pdf,
                native_id=native_id,
                published_at=_parse_date(row.get("日期")),
                language="zh",
                metadata={"symbol": code, "name": row.get("股票简称", ""),
                          "org": row.get("机构", ""), "rating": row.get("东财评级", ""),
                          "date": str(row.get("日期", ""))[:10], "industry": row.get("行业", ""),
                          "has_full_text": bool(body)},
                pre_chunked=pre_chunked,
            )
            if res["is_new"]:
                ingested += 1
            else:
                skipped += 1
        logger.info(f"研报摄入 {sym}: 处理 {len(rows)} 篇（full_text={full_text}）")

    logger.info(f"券商研报要点摄入完成：新增 {ingested}，跳过 {skipped}，失败 {failed}")
    return {"ingested": ingested, "skipped": skipped, "failed": failed}


def upgrade_existing_to_full_text(symbols: List[str]) -> Dict[str, Any]:
    """
    把已存在的、仅有元数据要点的研报文档升级成全文（不受 doc_id 固化限制）。

    典型场景：早期探站/小规模验证时用 full_text=False 摄入过的少数股票，现在全市场
    批次统一用 full_text=True 跑，这些股票的旧文档因 doc_id 已存在会被批量任务跳过，
    永远停留在"仅元数据"——这个函数专门补齐这批历史欠账，跟全市场覆盖对齐。
    已经是全文的/库里不存在的/没有PDF链接的文档跳过，不重复处理。
    """
    import akshare as ak
    from net.domestic import domestic_akshare

    store = KnowledgeStore()
    pipeline = IngestPipeline()
    upgraded = skipped = failed = 0

    for sym in symbols:
        code = sym.split(".")[0]
        try:
            df = domestic_akshare(ak.stock_research_report_em, symbol=code)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"研报列表拉取失败 {sym}: {e}")
            failed += 1
            continue
        if df is None or df.empty:
            continue

        for row in df.to_dict("records"):
            pdf = row.get("报告PDF链接") or ""
            native_id = pdf or f"{code}:{row.get('机构','')}:{str(row.get('日期',''))[:10]}:{row.get('报告名称','')}"
            doc_id = make_doc_id("research", native_id)
            if not store.doc_exists(doc_id):
                continue  # 库里没有这篇，交给正常摄入路径处理，不在这里新建

            doc = store.get_documents([doc_id]).get(doc_id)
            if not doc:
                continue
            meta = json.loads(doc.doc_metadata) if doc.doc_metadata else {}
            if meta.get("has_full_text") or not pdf:
                skipped += 1
                continue

            body = _fetch_pdf_text(pdf)
            if not body:
                failed += 1
                logger.debug(f"研报全文升级失败（PDF抓取为空）：{sym} {row.get('报告名称','')}")
                continue

            summary = _report_to_text(row)
            text = f"{summary}\n\n【研报全文】\n{body}"
            meta["has_full_text"] = True
            res = pipeline.reingest_text(doc_id=doc_id, text=text, metadata=meta)
            if res["status"] == "embedded":
                upgraded += 1
            else:
                failed += 1
        logger.info(f"研报全文升级扫描 {sym} 完成")

    logger.info(f"研报全文升级完成：升级 {upgraded}，跳过 {skipped}，失败 {failed}")
    return {"upgraded": upgraded, "skipped": skipped, "failed": failed}
