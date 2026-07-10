"""章节成稿器 —— 一个报告章节 → 一段 Markdown 正文（流式）。

13.2 把 266 秒的全量报告拆成五个可单独调用的章节后，这里是引擎层的唯一成稿口。
它与旧的 `generator.generate_multi_stream` 的区别：

- **不做章节间链式上下文**。旧路径把前章结论摘要前置到后章 prompt（`_build_selective_context`）；
  拆开后各章独立成稿，一致性由 MoneyBill 撰写纵览时统一口径。各章 prompt 里仍有
  `_section_market_context(data)` 这层数据级共同语境兜底。
- **不写 AnalysisReport、不拼 final_content**。成稿即流给用户。
- **认 cancel_event**。旧路径收了却从不读，用户一关页面照样把十次调用烧完。

Ch8（买入推荐）内部的批次链式保留：后批次要看到前批次推荐了谁，才不会重复推。

产出的 NDJSON 事件沿用既有词汇，`agents/subagents/base.py::relay_markdown` 直接消费：
    collecting {message}    — 章节进度（→ 主聊天流的 agent_progress）
    chunk      {content}    — 正文增量
    done       {token_count, section, chars, cancelled}
    error      {message}    — 某章失败；其余章仍会继续，故 relay 端只在「全无产出」时判失败
"""
import json
import threading
from collections.abc import Generator

from loguru import logger

from llm_client import build_client, stream_text
from report_engine import chapter_utils
from report_engine.prompt_builder import ChapterCall, ReportPromptBuilder

SECTION_TITLES = {
    "market": "市场总览与板块热点",
    "news": "新闻深度分析与舆情研判",
    "positions": "持仓诊断",
    "strategy": "上期回顾与策略表现",
    "picks": "买入推荐",
}


def _event(event: str, **fields) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n"


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def write_section(
    section: str,
    data: dict,
    *,
    report_type: str = "weekly",
    model: str,
    cancel_event: threading.Event | None = None,
) -> Generator[str, None, None]:
    """把一个章节写成 Markdown，逐字 yield NDJSON 事件行。"""
    builder = ReportPromptBuilder()
    try:
        calls = builder.build_section_calls(section, data, report_type)
    except ValueError as e:
        yield _event("error", message=str(e))
        yield _event("done", token_count=0, section=section, chars=0, cancelled=False)
        return

    client = build_client()
    total_tokens = 0
    total_chars = 0
    # Ch8 批次链式：累积已成稿的推荐正文，供后批次去重
    ch8_accumulated = ""

    for idx, call in enumerate(calls):
        if _cancelled(cancel_event):
            logger.info(f"[{section}] 已取消，跳过剩余 {len(calls) - idx} 次调用")
            break

        yield _event("collecting", message=call.label)

        try:
            emitted = yield from _run_one_call(
                call, client=client, model=model, cancel_event=cancel_event,
                ch8_accumulated=ch8_accumulated,
            )
        except Exception as e:  # noqa: BLE001 — 单章失败不该带走同一工具里的其他章
            logger.exception(f"[{section}] 第{call.chapter}章生成失败")
            yield _event("error", message=f"第{call.chapter}章生成失败：{e}")
            continue

        total_tokens += emitted["tokens"]
        total_chars += len(emitted["text"])
        if call.chapter == 8 and emitted["text"]:
            ch8_accumulated += ("\n\n" if ch8_accumulated else "") + emitted["text"]
        if emitted["cancelled"]:
            break

    yield _event("done", token_count=total_tokens, section=section,
                 chars=total_chars, cancelled=_cancelled(cancel_event))


def _run_one_call(
    call: ChapterCall, *, client, model: str,
    cancel_event: threading.Event | None, ch8_accumulated: str,
) -> Generator[str, None, dict]:
    """跑一次章节 LLM 调用，流式 yield chunk。返回 {text, tokens, cancelled}。

    text 是**归一化后**的正文（Ch8 续批已剥掉重复的 `## 8.` 标题）；chunk 则按原样
    流给用户——与旧路径逐字一致（旧路径也是先流原文、后剥标题再入库）。
    """
    user_prompt = call.user_prompt

    # Ch8 续批：注入前批次已推荐标的，防止重复分析（旧 generator.py Bug#6）
    if call.chapter == 8 and call.batch_data and not call.batch_data.get("is_first", True) and ch8_accumulated:
        prev_summary = chapter_utils.extract_per_stock_ops(ch8_accumulated, "前批次买入推荐")
        if prev_summary:
            user_prompt = ("【⚠️ 前批次已推荐标的，本批请勿重复分析 ⚠️】\n"
                           + prev_summary + "\n\n" + user_prompt)

    messages = [
        {"role": "system", "content": call.system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    text_parts: list[str] = []
    tokens = 0
    cancelled = False
    for ev in stream_text(messages, model=model, temperature=call.temperature,
                          client=client, cancel_event=cancel_event):
        if ev["type"] == "text":
            text_parts.append(ev["content"])
            yield _event("chunk", content=ev["content"])
        else:
            tokens = ev["tokens"]
            cancelled = ev["cancelled"]

    output = "".join(text_parts)
    if call.chapter == 8 and call.batch_data and not call.batch_data.get("is_first", True):
        output = chapter_utils.strip_duplicate_ch8_heading(output)

    # Ch8 覆盖度校验 + 一次补充调用（旧 generator.py 的同名逻辑）
    if call.chapter == 8 and call.batch_data and not cancelled:
        missing = chapter_utils.validate_ch8_output(output, call.batch_data.get("stocks", []))
        if missing:
            logger.warning(f"Ch8 batch {call.batch_data.get('batch_index', '?')} "
                           f"遗漏 {len(missing)} 只标的: {[s.get('symbol') for s in missing]}，发起补充调用")
            supplement, sup_tokens = _supplement_missing(
                client, model, call, missing, cancel_event)
            tokens += sup_tokens
            if supplement:
                output += "\n\n" + supplement
                yield _event("chunk", content="\n\n" + supplement)

    return {"text": output, "tokens": tokens, "cancelled": cancelled}


def _supplement_missing(
    client, model: str, call: ChapterCall, missing_stocks: list,
    cancel_event: threading.Event | None,
) -> tuple[str, int]:
    """对遗漏的标的发一次补充调用（非流式：全量 drain stream_text 取终值）。"""
    prompt = ReportPromptBuilder.build_ch8_supplement_prompt(missing_stocks)
    messages = [
        {"role": "system", "content": call.system_prompt},
        {"role": "user", "content": prompt},
    ]
    try:
        for ev in stream_text(messages, model=model, temperature=call.temperature,
                              client=client, cancel_event=cancel_event):
            if ev["type"] == "done":
                logger.info(f"Ch8 补充调用完成 | {len(ev['content'])} 字符")
                return ev["content"], ev["tokens"]
    except Exception as e:  # noqa: BLE001 — 补充失败不该毁掉已成稿的本批
        logger.error(f"Ch8 补充调用失败: {e}")
    return "", 0
