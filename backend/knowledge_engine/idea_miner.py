"""
idea_miner — 从新论文提炼可落地的 alpha 思路，喂回 Alpha Lab 回测。

链路：
0. discover_papers：web_search(site:arxiv.org) → read_url → 入库 source_type='paper'
1. mine_ideas：扫未提炼过的论文 → LLM(get_best_model) 提炼结构化 alpha 假设 → 存 AlphaIdea
2. run_backtest：把 idea.optimization_goal 喂 AlphaLabEngine.start_session 回测，回填得分

★ 回测开销大（一次 AlphaLab 迭代很烧 token）——**只在用户明确请求时**才调 run_backtest
（仅 API /knowledge/ideas/{id}/backtest 暴露）。绝无自动/定时/顺手回测；mine_ideas 只提炼存库，
不触发回测；也不给 MoneyBill 提供能自动触发回测的工具。
"""
import json
import re
from typing import List, Optional, Dict, Any

from loguru import logger

from knowledge_engine.store import KnowledgeStore
from knowledge_engine.ingest.web_source import ingest_papers
from llm_config import get_best_model, normalize_chat_params


_EXTRACT_SYSTEM = """你是量化策略研究员。给你一篇金融/量化论文的正文，请提炼出「可落地的 alpha 假设」。
只输出一个 JSON 对象（不要任何额外文字、不要 markdown 代码块），字段：
{
  "title": "一句话策略名",
  "hypothesis": "核心因子逻辑（一两句话，说清用什么信号、预期方向）",
  "factor_definition": "因子/信号的可计算定义，用自然语言+伪公式描述，要能落到日线数据上计算",
  "optimization_goal": "给回测引擎的自然语言目标，形如：构造基于XX因子的多空/择时策略，最大化夏普，控制回撤<20%",
  "rationale": "为什么这个因子可能有 alpha（论文依据+市场逻辑）",
  "suggested_symbols": ["适用标的代码，A股用如 600519.SH/000001.SZ，没特定标的就给空数组"]
}
若论文与可交易的量化因子无关，title 返回空字符串。"""


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出里抠出 JSON 对象（容忍 ```json 包裹/前后噪声）。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = m.group(1) if m else None
    if raw is None:
        s, e = text.find("{"), text.rfind("}")
        raw = text[s:e + 1] if s != -1 and e > s else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


class IdeaMiner:
    def __init__(self) -> None:
        self.store = KnowledgeStore()

    # ---------- 0. 发现论文 ----------
    def discover_papers(self, queries: List[str], max_docs: int = 10) -> Dict[str, Any]:
        return ingest_papers(queries, max_docs=max_docs)

    # ---------- 1. 提炼 alpha ----------
    def _extract_alpha(self, doc_text: str) -> Optional[dict]:
        from llm_client import build_client
        client = build_client()
        messages = [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": f"论文正文（节选）：\n\n{doc_text[:6000]}"},
        ]
        resp = client.chat.completions.create(**normalize_chat_params(dict(
            model=get_best_model(), messages=messages,
            temperature=0.4, max_tokens=1200,
        )))
        return _extract_json(resp.choices[0].message.content or "")

    def mine_ideas(self, limit: int = 5) -> Dict[str, Any]:
        """扫未提炼过的论文，逐篇 LLM 提炼成 AlphaIdea。"""
        doc_ids = self.store.doc_ids_without_ideas(source_type="paper", limit=limit)
        if not doc_ids:
            return {"mined": 0, "skipped": 0, "ideas": [], "note": "没有待提炼的论文"}
        docs = self.store.get_documents(doc_ids)
        mined, skipped, ideas = 0, 0, []
        for doc_id in doc_ids:
            doc = docs.get(doc_id)
            if not doc or not doc.full_text:
                skipped += 1
                continue
            try:
                data = self._extract_alpha(doc.full_text)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"提炼失败 doc_id={doc_id}: {e}")
                skipped += 1
                continue
            if not data or not (data.get("title") or "").strip():
                skipped += 1
                continue
            idea_id = self.store.add_idea(
                source_doc_id=doc_id,
                title=data.get("title", "")[:300],
                hypothesis=data.get("hypothesis", ""),
                factor_definition=data.get("factor_definition", ""),
                optimization_goal=data.get("optimization_goal", ""),
                rationale=data.get("rationale", ""),
                suggested_symbols=data.get("suggested_symbols") or [],
            )
            mined += 1
            ideas.append({"idea_id": idea_id, "title": data.get("title"), "source_doc_id": doc_id})
            logger.info(f"提炼出 alpha idea: {data.get('title')}")
        return {"mined": mined, "skipped": skipped, "ideas": ideas}

    # ---------- 2. 回测验证 ----------
    def run_backtest(self, idea_id: str, *, data_start: str, data_end: str,
                     symbols: Optional[List[str]] = None, max_iterations: int = 8) -> Dict[str, Any]:
        """把 idea.optimization_goal 喂 AlphaLabEngine 回测，回填 session_id + 得分。"""
        idea = self.store.get_idea(idea_id)
        if not idea:
            return {"ok": False, "error": "idea 不存在"}

        target_symbols = symbols or (json.loads(idea.suggested_symbols or "[]") or ["000001.SZ"])
        self.store.update_idea(idea_id, status="backtesting")

        from alpha_lab.engine import AlphaLabEngine
        engine = AlphaLabEngine()
        session_id, best_score = None, None
        try:
            for line in engine.start_session(
                target_symbols=target_symbols,
                optimization_goal=idea.optimization_goal or idea.hypothesis,
                data_start=data_start, data_end=data_end,
                max_iterations=max_iterations,
            ):
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("session_id") and not session_id:
                    session_id = ev["session_id"]
                # 抓最优分（兼容多种字段名）
                for k in ("best_composite_score", "composite_score", "best_score"):
                    if isinstance(ev.get(k), (int, float)):
                        best_score = ev[k]
        except Exception as e:  # noqa: BLE001
            logger.exception(f"回测失败 idea={idea_id}: {e}")
            self.store.update_idea(idea_id, status="proposed")
            return {"ok": False, "error": str(e)}

        status = "validated" if (best_score or 0) >= 0.5 else "rejected"
        self.store.update_idea(idea_id, status=status,
                               alpha_lab_session_id=session_id, best_composite_score=best_score)
        return {"ok": True, "idea_id": idea_id, "session_id": session_id,
                "best_composite_score": best_score, "status": status}
