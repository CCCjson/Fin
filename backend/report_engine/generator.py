"""
报告生成器 — 调用OpenAI API流式生成分析报告

支持两种模式:
  1. generate_stream()       — 单次调用（向后兼容）
  2. generate_multi_stream() — 多次调用（每章独立 + 链式上下文）
"""
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Dict, Generator, List, TYPE_CHECKING
from loguru import logger
from dotenv import load_dotenv

from data_engine.storage.database import get_session
from data_engine.storage.models import AnalysisReport
from llm_config import get_best_model, normalize_chat_params
from report_engine.planner import CHAPTER_DEPS as _CHAPTER_DEPS

if TYPE_CHECKING:
    from report_engine.prompt_builder import CallSpec, ReportPromptBuilder

# 加载 .env（override=True 确保代理变量生效）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env", override=True)

# 章节依赖映射现在定义在 report_engine/planner.py（Plan-and-Execute 显式建模的
# 一部分），这里只是原样引用，行为不变。


class ReportGenerator:
    """调用LLM生成投资分析报告"""

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if not self.api_key:
            logger.warning("OPENAI_API_KEY 未配置，报告生成将失败")

    # ==================================================================
    # 多次调用流式生成（每章独立 + 链式上下文）
    # ==================================================================
    def generate_multi_stream(
        self,
        data: Dict,
        call_specs: "List[CallSpec]",
        prompt_builder: "ReportPromptBuilder",
        report_id: str,
        title: str,
        model: str = None,
        report_type: str = "weekly",
        period_start: str = None,
        period_end: str = None,
        data_snapshot: str = None,
    ) -> Generator[str, None, None]:
        """
        分多次调用LLM，每章独立生成，链式传递上下文

        Events:
            start             — 报告开始生成
            chapter_progress  — 当前正在生成的章节
            chunk             — Markdown内容片段
            chapter_complete  — 某章生成完毕
            done              — 生成完成
            error             — 出错
        """
        model = model or get_best_model()
        logger.info(f"多次调用报告生成 | model={model} | calls={len(call_specs)}")

        # yield start 事件
        yield self._event("start", report_id=report_id, title=title) + "\n"

        # 创建DB记录
        session = get_session()
        try:
            from datetime import date
            report = AnalysisReport(
                report_id=report_id,
                report_type=report_type,
                title=title,
                status="generating",
                model_used=model,
                period_start=date.fromisoformat(period_start) if period_start else None,
                period_end=date.fromisoformat(period_end) if period_end else None,
                data_snapshot=data_snapshot,
            )
            session.add(report)
            session.commit()
        except Exception as e:
            logger.error(f"创建报告记录失败: {e}")
            session.rollback()
            yield self._event("error", message=f"创建报告记录失败: {e}") + "\n"
            session.close()
            return

        total_token_count = 0
        start_time = time.time()
        # 按章节号存储输出（key = chapter_number）
        previous_outputs: Dict[int, str] = {}

        try:
            from llm_client import build_client
            client = build_client(base_url=self.base_url, api_key=self.api_key)

            for spec in call_specs:
                chapter_num = spec.chapters[0]

                # yield chapter_progress 事件（含 chapters 供前端切换）
                yield self._event(
                    "chapter_progress",
                    call_index=spec.call_index,
                    total_calls=len(call_specs),
                    chapters=spec.chapters,
                    label=spec.label,
                ) + "\n"

                # ── 单章 try/except：某章失败不中断整体报告 ──
                try:
                    # 构建 user_prompt
                    if spec.needs_previous_outputs:
                        user_prompt = prompt_builder.build_user_prompt_for_call(
                            spec, data, previous_outputs
                        )
                    else:
                        user_prompt = spec.user_prompt

                    # 为非 needs_previous_outputs 的调用 **前置** 精简前文上下文
                    # （前置而非追加：确保 GPT 在看到章节数据前先读到一致性约束）
                    if previous_outputs and not spec.needs_previous_outputs:
                        context = self._build_selective_context(previous_outputs, chapter_num)
                        if context:
                            user_prompt = context + "\n\n" + user_prompt

                    # Bug#6 — Ch8 多批次：注入前批次已推荐标的，防止后续批次重复分析
                    if chapter_num == 8 and spec.batch_data and not spec.batch_data.get("is_first", True):
                        prev_ch8 = previous_outputs.get(8, "")
                        if prev_ch8:
                            prev_summary = ReportGenerator._extract_per_stock_ops(prev_ch8, "前批次买入推荐")
                            if prev_summary:
                                user_prompt = (
                                    "【⚠️ 前批次已推荐标的，本批请勿重复分析 ⚠️】\n"
                                    + prev_summary
                                    + "\n\n"
                                    + user_prompt
                                )

                    logger.info(f"Call {spec.call_index}/{len(call_specs)} | "
                                f"Ch{chapter_num} | temp={spec.temperature} | max_tokens={spec.max_tokens}")

                    # OpenAI streaming 调用
                    call_content: List[str] = []
                    create_kwargs = dict(
                        model=model,
                        messages=[
                            {"role": "system", "content": spec.system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=spec.temperature,
                        stream=True,
                        stream_options={"include_usage": True},
                    )
                    if spec.max_tokens > 0:
                        create_kwargs["max_tokens"] = spec.max_tokens
                    stream = client.chat.completions.create(**normalize_chat_params(create_kwargs))

                    # Bug#5 — token 统计：用覆盖写入取最终值，再累加到总数
                    # （OpenAI 仅在最后一个 chunk 发送非空 usage，+=会导致同chunk内双计）
                    call_token = 0
                    for chunk in stream:
                        choice = chunk.choices[0] if chunk.choices else None
                        if choice and choice.delta and choice.delta.content:
                            text = choice.delta.content
                            call_content.append(text)
                            yield self._event("chunk", content=text) + "\n"

                        if chunk.usage:
                            call_token = chunk.usage.total_tokens  # 覆盖取最终值
                    total_token_count += call_token  # 每次调用结束后才累加

                    # 保存本次输出（按章节号，同章节追加）
                    call_output = "".join(call_content)

                    # ── Ch8 续批：剥除 GPT 可能重复输出的二级标题 ──
                    # 例如 "## 8. 重点买入标的深度分析" 或 "## 8、..." 等变体
                    if chapter_num == 8 and spec.batch_data and not spec.batch_data.get("is_first", True):
                        call_output = re.sub(
                            r'^\s*##\s+8[.、\s：:][^\n]*\n?', '', call_output, flags=re.MULTILINE
                        ).lstrip()

                    # ── Ch8 输出验证 + 自动重试 ──
                    if chapter_num == 8 and spec.batch_data:
                        missing = self._validate_ch8_output(call_output, spec.batch_data.get("stocks", []))
                        if missing:
                            logger.warning(f"Ch8 batch {spec.batch_data.get('batch_index', '?')} "
                                           f"遗漏 {len(missing)} 只标的: {[s.get('symbol') for s in missing]}，发起补充调用")
                            supplement = self._retry_missing_stocks(
                                client, model, spec, missing
                            )
                            if supplement:
                                call_output += "\n\n" + supplement
                                yield self._event("chunk", content="\n\n" + supplement) + "\n"

                    if chapter_num in previous_outputs:
                        previous_outputs[chapter_num] += "\n\n" + call_output
                    else:
                        previous_outputs[chapter_num] = call_output
                    logger.info(f"Call {spec.call_index} (Ch{chapter_num}) 完成 | "
                                f"{len(call_output)} 字符")

                except Exception as chapter_err:
                    # Bug#7 — 单章失败：写入占位符，Ch1 仍能感知该章缺失，继续生成其余章节
                    logger.error(f"Call {spec.call_index} (Ch{chapter_num}) 生成失败: {chapter_err}")
                    if chapter_num not in previous_outputs:
                        previous_outputs[chapter_num] = f"[第{chapter_num}章生成失败，已跳过: {chapter_err}]"

                # 仅在同章节的最后一个子调用结束时发送 chapter_complete
                is_last_for_chapter = True
                if spec.batch_data and not spec.batch_data.get("is_last", True):
                    is_last_for_chapter = False

                if is_last_for_chapter:
                    yield self._event(
                        "chapter_complete",
                        chapter_number=chapter_num,
                        call_index=spec.call_index,
                        total_calls=len(call_specs),
                    ) + "\n"

            # 按章节号顺序拼接最终内容（Ch1→Ch2→...→Ch9）
            final_content = "\n\n".join(
                previous_outputs[ch].strip()
                for ch in sorted(previous_outputs.keys())
                if previous_outputs[ch].strip()
            )

            generation_time = round(time.time() - start_time, 2)

            if total_token_count == 0:
                total_token_count = len(final_content) // 2

            # 更新DB
            report.content = final_content
            report.status = "completed"
            report.token_count = total_token_count
            report.generation_time_seconds = generation_time
            session.commit()

            yield self._event("done", report_id=report_id,
                              token_count=total_token_count,
                              generation_time=generation_time) + "\n"

        except Exception as e:
            logger.error(f"多次调用报告生成失败: {e}")
            report.status = "failed"
            report.error_message = str(e)
            session.commit()
            yield self._event("error", message=str(e)) + "\n"
        finally:
            session.close()

    # ------------------------------------------------------------------
    # 选择性上下文：只传相关章节的结论摘要
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_conclusion(text: str, max_chars: int = 500) -> str:
        """从章节全文中提取结论段（## 标题后到第一个 ### 之前的内容）"""
        text = text.strip()
        if not text:
            return ""
        if len(text) <= max_chars:
            return text

        # 跳过 ## 标题行，取到第一个 ### 之前
        lines = text.split("\n")
        conclusion_lines = []
        started = False
        for line in lines:
            if line.startswith("## "):
                started = True
                conclusion_lines.append(line)
                continue
            if started and line.startswith("### "):
                break
            conclusion_lines.append(line)

        result = "\n".join(conclusion_lines).strip()
        if len(result) > max_chars:
            result = result[:max_chars] + "..."
        return result if result else text[:max_chars] + "..."

    @staticmethod
    def _extract_per_stock_ops(text: str, chapter_label: str, max_stocks: int = 15) -> str:
        """
        从含个股 ### 子节的章节（如 Ch5 持仓诊断、Ch8 买入推荐）中，
        提取每只股票的「明日操作」结论，格式：
            【Ch5 持仓诊断 — 每只股票操作结论】
            - 金花股份(600080.SH): 📈 明日可加仓（8.80-8.95元）
            - 同益中(688722.SH): ✅ 继续持有，观察
        供后续章节（Ch8/Ch1）对照，确保操作建议不矛盾。
        """
        lines = text.split("\n")
        results = []
        current_stock = None
        # 「操作标记行」：出现这些词说明下一行或本行末尾是操作内容
        op_header_kws = ("明日操作", "操作建议", "操作：", "建议：")
        # 「操作内容行」：直接包含操作动作的行
        op_content_kws = ("✅", "📈", "📉", "🚨", "⚠️", "继续持有", "加仓", "减仓",
                          "止损", "清仓", "观察", "买入", "卖出", "持有")

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()

            # 识别个股标题 ###
            if stripped.startswith("### "):
                current_stock = stripped[4:].strip()
                i += 1
                continue

            if current_stock:
                # 情况1：「明日操作：📈 内容」在同一行（内容非空）
                for kw in op_header_kws:
                    if kw in stripped:
                        after = stripped[stripped.index(kw) + len(kw):].lstrip("：: ").strip()
                        if after and len(after) > 3:
                            clean = after.lstrip("-*>🔹•").strip()
                            results.append(f"  - {current_stock}: {clean[:120]}")
                            current_stock = None
                            break
                        else:
                            # 情况2：操作内容在下一行
                            j = i + 1
                            while j < len(lines) and j <= i + 3:
                                next_stripped = lines[j].strip()
                                if next_stripped and any(c in next_stripped for c in op_content_kws):
                                    clean = next_stripped.lstrip("-*>🔹•📈📉✅🚨⚠️").strip()
                                    results.append(f"  - {current_stock}: {clean[:120]}")
                                    current_stock = None
                                    break
                                elif next_stripped:
                                    # 非空但没有操作关键词，也直接用
                                    clean = next_stripped.lstrip("-*>🔹•").strip()
                                    if len(clean) > 5:
                                        results.append(f"  - {current_stock}: {clean[:120]}")
                                        current_stock = None
                                    break
                                j += 1
                        break
                else:
                    # 没有操作标记词，但行本身直接含操作内容关键词
                    if any(kw in stripped for kw in op_content_kws) and len(stripped) > 5:
                        clean = stripped.lstrip("-*>🔹•📈📉✅🚨⚠️").strip()
                        results.append(f"  - {current_stock}: {clean[:120]}")
                        current_stock = None

            if current_stock is None and len(results) >= max_stocks:
                break
            i += 1

        if not results:
            return ""

        header = f"【{chapter_label} — 各股操作结论（后续章节必须与此保持一致，不得矛盾）】"
        return header + "\n" + "\n".join(results)

    @staticmethod
    def _build_selective_context(previous_outputs: Dict[int, str], current_chapter: int) -> str:
        """根据章节依赖映射，只传相关章节的结论摘要。
        对含个股子节的章节（Ch5/Ch8），使用 _extract_per_stock_ops 提取逐股操作结论，
        避免 _extract_conclusion 截到空内容导致后续章节无法感知前文建议。
        """
        deps = _CHAPTER_DEPS.get(current_chapter, [])
        if not deps:
            return ""

        # 含个股操作建议的章节，需要用逐股提取而非截取导言
        # 注：Ch4 只做事实复盘不含操作建议，用 _extract_conclusion 提取即可
        PER_STOCK_CHAPTERS = {
            5: "第5章持仓诊断",
            8: "第8章买入推荐",
        }

        relevant = []
        for ch_num in deps:
            text = previous_outputs.get(ch_num, "").strip()
            if not text:
                continue
            if ch_num in PER_STOCK_CHAPTERS:
                summary = ReportGenerator._extract_per_stock_ops(text, PER_STOCK_CHAPTERS[ch_num])
                if summary:
                    relevant.append(summary)
            else:
                conclusion = ReportGenerator._extract_conclusion(text)
                if conclusion:
                    relevant.append(conclusion)

        if not relevant:
            return ""

        # Ch5 持仓诊断需要参考 Ch6 复盘的事实数据 + Ch3 新闻分析结论
        if current_chapter == 5:
            parts = [
                "=" * 50,
                "【⚠️ 一致性参考 — 第6章复盘 + 第3章新闻分析 ⚠️】",
                "第6章对上期推荐标的做了涨跌幅、止盈止损触发等事实复盘（第6章不含操作建议）。",
                "第3章对近期新闻做了深度分析和情绪研判，请结合新闻面来诊断持仓。",
                "请结合第6章的复盘结论和第3章的新闻分析来诊断用户持仓——",
                "如果某只持仓股在第6章中被复盘过，你的技术面判断应与复盘走势事实一致；",
                "如果第3章提到了某只持仓股的利好/利空消息，你的消息面分析应与之一致。",
                "=" * 50,
            ]
        else:
            parts = [
                "=" * 50,
                "【前文关键结论（请自然衔接上文的分析逻辑和用词风格）】",
                "=" * 50,
            ]
        for text in relevant:
            parts.append(text)
            parts.append("")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Ch8 输出验证 + 补充重试
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_ch8_output(output_text: str, batch_stocks: list) -> list:
        """检查 Ch8 输出是否覆盖了全部标的，返回缺失的股票列表"""
        missing = []
        for stock in batch_stocks:
            symbol = stock.get("symbol", "")
            if not symbol:
                continue
            # 同时检查完整代码(000001.SZ)、纯数字部分(000001)和股票名称
            # GPT 可能只写名称而省略代码，避免误判导致不必要的重试
            code_only = symbol.split(".")[0]
            name = stock.get("name", "")
            if (symbol not in output_text
                    and code_only not in output_text
                    and (not name or name not in output_text)):
                missing.append(stock)
        return missing

    def _retry_missing_stocks(self, client, model: str, spec, missing_stocks: list) -> str:
        """对遗漏的标的发起一次补充 API 调用（非 streaming，快速追加）

        构建与正常批次同等质量的数据（含技术指标、行情、基本面、新闻），
        确保补充分析也能引用具体指标数值。
        """
        from report_engine.prompt_builder import ReportPromptBuilder

        stock_list = "\n".join(
            f"- {s.get('symbol', '')} ({s.get('name', '')})"
            for s in missing_stocks
        )
        # 构建缺失标的的完整数据摘要（与 _section_buy_recommendations_batch 对齐）
        data_parts = []
        for stock in missing_stocks:
            industry_str = f" | {stock.get('industry', '')}" if stock.get('industry') else ""
            data_parts.append(f"--- {stock.get('symbol', '')} ({stock.get('name', '')}){industry_str} ---")
            data_parts.append(f"综合评分: {stock.get('composite_score', 'N/A')}/100")

            # 评分明细
            breakdown = stock.get("score_breakdown", {})
            if breakdown:
                bd_parts = []
                for dim, label in [("resonance", "共振"), ("risk_reward", "盈亏比"),
                                   ("quality", "信号质量"), ("volume", "量能"), ("timeliness", "时效")]:
                    info = breakdown.get(dim, {})
                    bd_parts.append(f"{label}{info.get('score', 'N/A')}({info.get('detail', '')})")
                data_parts.append(f"评分明细: {' | '.join(bd_parts)}")

            # 策略共振
            res_strats = stock.get("resonance_strategies", [])
            res_count = stock.get("resonance_count", 0)
            if res_count > 1:
                data_parts.append(f"策略共振: {res_count}个策略同时看好 ({', '.join(res_strats)})")

            data_parts.append(f"触发价格{stock.get('price', 'N/A')}, "
                              f"建议止损{stock.get('stop_loss', 'N/A')}, "
                              f"建议止盈{stock.get('take_profit', 'N/A')}")

            # 信号原因
            reasons = stock.get("reasons", [])
            if reasons:
                data_parts.append(f"信号原因: {ReportPromptBuilder._format_reasons(reasons)}")

            # 基本面（完整）
            fund = stock.get("fundamentals") or {}
            if isinstance(fund, dict) and fund:
                pe_ttm = fund.get('pe_ttm')
                pb = fund.get('pb')
                roe = fund.get('roe')
                mcap = fund.get('total_market_cap')
                fmcap = fund.get('float_market_cap')
                pe_str = f"PE(TTM){pe_ttm:.1f}" if pe_ttm is not None else "PE(TTM) N/A"
                pb_str = f"PB{pb:.2f}" if pb is not None else "PB N/A"
                roe_str = f"ROE{roe:.1f}%" if roe is not None else "ROE N/A"
                mcap_str = f"总市值{mcap/1e8:.0f}亿" if mcap else "总市值N/A"
                fmcap_str = f"流通{fmcap/1e8:.0f}亿" if fmcap else ""
                data_parts.append(f"基本面: {pe_str} | {pb_str} | {mcap_str} {fmcap_str} | {roe_str}")

            # 20日价格统计
            ps = stock.get("price_stats", {})
            if ps:
                data_parts.append(f"20日统计: 最高{ps.get('high_20d', 'N/A')}, "
                                  f"最低{ps.get('low_20d', 'N/A')}, "
                                  f"均价{ps.get('avg_20d', 'N/A')}, "
                                  f"最新收盘{ps.get('latest_close', 'N/A')}")

            # 近期行情
            quotes = stock.get("recent_quotes", [])
            if quotes:
                data_parts.append("近期行情:")
                for q in quotes:
                    data_parts.append(f"  {q['date']}: O{q['open']} H{q['high']} L{q['low']} "
                                      f"C{q['close']} V{q['volume']}")

            # 技术指标快照
            ind = stock.get("indicators")
            if ind:
                data_parts.append(ReportPromptBuilder._format_indicator_snapshot(ind))

            # 个股新闻
            news = stock.get("news", [])
            if news:
                data_parts.append("近期相关新闻:")
                for n in news:
                    data_parts.append(f"  - {n['title']} ({n.get('source', '')}, {n.get('published_at', '')[:10]})")
                    if n.get("content"):
                        data_parts.append(f"    摘要: {n['content']}")

            data_parts.append("")

        supplement_prompt = (
            f"你在上面的分析中遗漏了以下标的，请立即补充分析，"
            f"格式与前文一致（每只用 ### 三级标题）：\n\n"
            f"{chr(10).join(data_parts)}\n\n"
            f"⚠️ 必须分析的标的：\n{stock_list}"
        )

        try:
            response = client.chat.completions.create(
                **normalize_chat_params(dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": spec.system_prompt},
                        {"role": "user", "content": supplement_prompt},
                    ],
                    temperature=spec.temperature,
                ))
            )
            result = response.choices[0].message.content or ""
            logger.info(f"Ch8 补充调用完成 | {len(result)} 字符")
            return result
        except Exception as e:
            logger.error(f"Ch8 补充调用失败: {e}")
            return ""

    # ==================================================================
    # 单次调用流式生成（向后兼容）
    # ==================================================================
    def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        report_id: str,
        title: str,
        model: str = None,
        report_type: str = "weekly",
        period_start: str = None,
        period_end: str = None,
        data_snapshot: str = None,
    ) -> Generator[str, None, None]:
        """
        流式生成报告，yield NDJSON事件

        Events:
            start   — 报告开始生成
            chunk   — Markdown内容片段
            done    — 生成完成
            error   — 出错
        """
        model = model or get_best_model()
        logger.info(f"报告生成 | model={model} | api_key={self.api_key[:12]}... | base_url={self.base_url}")
        logger.info(f"代理 | HTTPS_PROXY={os.getenv('HTTPS_PROXY', '未设置')} | HTTP_PROXY={os.getenv('HTTP_PROXY', '未设置')}")

        # 生成起始事件
        yield self._event("start", report_id=report_id, title=title) + "\n"

        # 创建DB记录
        session = get_session()
        try:
            from datetime import date
            report = AnalysisReport(
                report_id=report_id,
                report_type=report_type,
                title=title,
                status="generating",
                model_used=model,
                period_start=date.fromisoformat(period_start) if period_start else None,
                period_end=date.fromisoformat(period_end) if period_end else None,
                data_snapshot=data_snapshot,
            )
            session.add(report)
            session.commit()
        except Exception as e:
            logger.error(f"创建报告记录失败: {e}")
            session.rollback()
            yield self._event("error", message=f"创建报告记录失败: {e}") + "\n"
            session.close()
            return

        # 调用OpenAI
        full_content = []
        token_count = 0
        start_time = time.time()

        try:
            from llm_client import build_client
            client = build_client(base_url=self.base_url, api_key=self.api_key)
            stream = client.chat.completions.create(
                **normalize_chat_params(dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=16384,
                    temperature=0.5,
                    stream=True,
                    stream_options={"include_usage": True},
                ))
            )

            for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if choice and choice.delta and choice.delta.content:
                    text = choice.delta.content
                    full_content.append(text)
                    yield self._event("chunk", content=text) + "\n"

                # 统计token
                if chunk.usage:
                    token_count = chunk.usage.total_tokens

            # 完成
            generation_time = round(time.time() - start_time, 2)
            content_str = "".join(full_content)

            # 如果没有拿到usage，估算token
            if token_count == 0:
                token_count = len(content_str) // 2  # 粗略估计

            # 更新DB
            report.content = content_str
            report.status = "completed"
            report.token_count = token_count
            report.generation_time_seconds = generation_time
            session.commit()

            yield self._event("done", report_id=report_id,
                              token_count=token_count,
                              generation_time=generation_time) + "\n"

        except Exception as e:
            logger.error(f"报告生成失败: {e}")
            report.status = "failed"
            report.error_message = str(e)
            session.commit()
            yield self._event("error", message=str(e)) + "\n"
        finally:
            session.close()

    @staticmethod
    def _event(event_type: str, **kwargs) -> str:
        data = {"event": event_type}
        data.update(kwargs)
        return json.dumps(data, ensure_ascii=False)
