"""13.2-3 行为基线：section_writer 的编排。

fake 掉 stream_text，不打真实 LLM。锁：
  - 事件词汇与 relay_markdown 的消费口对齐（collecting / chunk / done / error）
  - 多章节工具（market=Ch2+Ch4）逐章成稿，token 求和
  - cancel_event 在章节边界与流内部都生效（旧路径完全不认它）
  - 单章失败不带走同一工具里的其他章
  - Ch8 批次链式去重 + 覆盖度校验 + 一次补充调用
"""
import json
import threading

import pytest

from report_engine import section_writer

pytestmark = pytest.mark.baseline


def _parse(lines):
    return [json.loads(x) for x in lines]


def _by_event(events, name):
    return [e for e in events if e["event"] == name]


class _FakeLLM:
    """记录每次调用的 messages；按 chapter 顺序吐预设文本。"""

    def __init__(self, replies=None, tokens=100, raise_on_call=None):
        self.calls: list[dict] = []
        self.replies = replies or {}
        self.tokens = tokens
        self.raise_on_call = raise_on_call
        self.cancel_after_chunks = None

    def __call__(self, messages, *, model, temperature, client=None, cancel_event=None):
        n = len(self.calls)
        self.calls.append({"messages": messages, "temperature": temperature,
                           "user": messages[1]["content"], "system": messages[0]["content"]})
        if self.raise_on_call is not None and n == self.raise_on_call:
            raise RuntimeError("上游炸了")

        text = self.replies.get(n, f"正文{n}")
        emitted = 0
        for piece in text:
            if cancel_event is not None and cancel_event.is_set():
                yield {"type": "done", "content": text[:emitted], "tokens": self.tokens,
                       "prompt_tokens": 0, "completion_tokens": 0, "cancelled": True}
                return
            yield {"type": "text", "content": piece}
            emitted += 1
            if self.cancel_after_chunks is not None and emitted >= self.cancel_after_chunks:
                cancel_event.set()
        yield {"type": "done", "content": text, "tokens": self.tokens,
               "prompt_tokens": 0, "completion_tokens": 0, "cancelled": False}


@pytest.fixture
def fake_llm(monkeypatch):
    llm = _FakeLLM()
    monkeypatch.setattr(section_writer, "stream_text", llm)
    monkeypatch.setattr(section_writer, "build_client", lambda: object())
    return llm


# ── 事件契约 ──────────────────────────────────────────────────────────────

def test_market_section_writes_two_chapters(fake_llm, report_data_weekly):
    fake_llm.replies = {0: "AB", 1: "CD"}
    events = _parse(section_writer.write_section(
        "market", report_data_weekly, model="m"))

    assert [e["message"] for e in _by_event(events, "collecting")] == [
        "正在生成第2章（市场总览与情绪研判）...",
        "正在生成第4章（板块热点与北向资金）...",
    ]
    assert "".join(e["content"] for e in _by_event(events, "chunk")) == "ABCD"

    done = events[-1]
    assert done["event"] == "done"
    assert done["section"] == "market"
    assert done["token_count"] == 200          # 两章求和
    assert done["chars"] == 4
    assert done["cancelled"] is False


def test_single_chapter_sections(fake_llm, report_data_weekly):
    for section in ("news", "positions"):
        fake_llm.calls.clear()
        events = _parse(section_writer.write_section(section, report_data_weekly, model="m"))
        assert len(_by_event(events, "collecting")) == 1
        assert len(fake_llm.calls) == 1
        assert events[-1]["section"] == section


def test_strategy_section_covers_ch6_and_ch7(fake_llm, report_data_weekly):
    events = _parse(section_writer.write_section("strategy", report_data_weekly, model="m"))
    labels = [e["message"] for e in _by_event(events, "collecting")]
    assert "第6章" in labels[0] and "第7章" in labels[1]
    # 温度沿用旧路径：Ch6/Ch7 都是 0.4
    assert [c["temperature"] for c in fake_llm.calls] == [0.4, 0.4]


def test_unknown_section_yields_error_then_done(fake_llm, report_data_weekly):
    events = _parse(section_writer.write_section("nope", report_data_weekly, model="m"))
    assert events[0]["event"] == "error"
    assert events[-1]["event"] == "done"
    assert fake_llm.calls == []


# ── cancel（旧路径完全没有的能力）────────────────────────────────────────

def test_cancel_before_start_makes_no_llm_call(fake_llm, report_data_weekly):
    cancel = threading.Event()
    cancel.set()
    events = _parse(section_writer.write_section(
        "market", report_data_weekly, model="m", cancel_event=cancel))
    assert fake_llm.calls == []
    assert events[-1]["event"] == "done"
    assert events[-1]["cancelled"] is True


def test_cancel_mid_stream_stops_before_next_chapter(fake_llm, report_data_weekly):
    """Ch2 流到一半断连 → 不该再去打 Ch4。"""
    cancel = threading.Event()
    fake_llm.replies = {0: "ABCDEF", 1: "应该永远不出现"}
    fake_llm.cancel_after_chunks = 2

    events = _parse(section_writer.write_section(
        "market", report_data_weekly, model="m", cancel_event=cancel))

    assert len(fake_llm.calls) == 1, "取消后不该发起第二章调用"
    body = "".join(e["content"] for e in _by_event(events, "chunk"))
    assert body == "AB"
    assert events[-1]["cancelled"] is True


# ── 单章失败隔离 ──────────────────────────────────────────────────────────

def test_one_failed_chapter_does_not_kill_the_sibling(fake_llm, report_data_weekly):
    fake_llm.raise_on_call = 0            # Ch2 炸
    fake_llm.replies = {1: "板块正文"}
    events = _parse(section_writer.write_section("market", report_data_weekly, model="m"))

    errors = _by_event(events, "error")
    assert len(errors) == 1 and "第2章生成失败" in errors[0]["message"]
    assert "".join(e["content"] for e in _by_event(events, "chunk")) == "板块正文"
    assert events[-1]["event"] == "done"
    assert events[-1]["token_count"] == 100   # 只有成功那章计入


# ── Ch8 批次链式 ──────────────────────────────────────────────────────────

def _full_batch_reply(stocks, with_ops: bool) -> str:
    """一批标的的"合格"输出：提到每只标的（免触发补充调用）。

    with_ops=True 时带 ### 个股小节与「明日操作」行，好让 extract_per_stock_ops
    能提炼出可注入下一批的结论摘要。
    """
    if not with_ops:
        return " ".join(s["symbol"] for s in stocks)
    return "\n".join(
        f"### {s['name']}({s['symbol']})\n明日操作：📈 可低吸建仓\n" for s in stocks)


def test_picks_batches_are_chained_for_dedup(fake_llm, report_data_weekly):
    """后批次必须看到前批次推荐了谁，否则会重复推荐（旧 Bug#6）。"""
    recs = report_data_weekly["top_stocks"]["buy_recommendations"]
    assert len(recs) > 4, "冻结快照应有多批"
    batch0 = recs[:4]
    fake_llm.replies = {0: _full_batch_reply(batch0, with_ops=True)}

    _parse(section_writer.write_section("picks", report_data_weekly, model="m"))

    assert len(fake_llm.calls) >= 2
    second_user = fake_llm.calls[1]["user"]      # batch0 全覆盖 → 无补充调用插队
    assert "【⚠️ 前批次已推荐标的，本批请勿重复分析 ⚠️】" in second_user
    assert batch0[0]["symbol"] in second_user
    # 首批 prompt 里不该有这段注入
    assert "前批次已推荐标的" not in fake_llm.calls[0]["user"]


def test_first_ch8_batch_has_no_dedup_injection(fake_llm, report_data_weekly):
    """首批产出里提不出个股操作结论时，下一批不该被塞一段空注入。"""
    recs = report_data_weekly["top_stocks"]["buy_recommendations"]
    fake_llm.replies = {0: _full_batch_reply(recs[:4], with_ops=False)}
    _parse(section_writer.write_section("picks", report_data_weekly, model="m"))
    assert "前批次已推荐标的" not in fake_llm.calls[1]["user"]


def test_ch8_missing_stock_triggers_one_supplement_call(fake_llm, report_data_weekly):
    """Ch8 输出漏了标的 → 补充调用；补充文本进 chunk 且计 token。"""
    recs = report_data_weekly["top_stocks"]["buy_recommendations"]
    n_batches = -(-len(recs) // 4)
    # 每批都空输出 → 每批都判定「全部遗漏」→ 每批各补一次
    fake_llm.replies = {i: "" for i in range(n_batches * 2)}
    fake_llm.replies.update({i: "补充分析" for i in range(1, n_batches * 2, 2)})

    events = _parse(section_writer.write_section("picks", report_data_weekly, model="m"))

    # 主调用 + 补充调用 交替
    assert len(fake_llm.calls) == n_batches * 2
    supplement_prompts = [c["user"] for c in fake_llm.calls[1::2]]
    assert all("你在上面的分析中遗漏了以下标的" in p for p in supplement_prompts)
    assert "".join(e["content"] for e in _by_event(events, "chunk")).count("补充分析") == n_batches
    assert events[-1]["token_count"] == n_batches * 2 * 100


def test_ch8_no_supplement_when_all_stocks_covered(fake_llm, report_data_weekly):
    recs = report_data_weekly["top_stocks"]["buy_recommendations"]
    n_batches = -(-len(recs) // 4)
    # 每批输出都提到本批全部标的 → 不触发补充
    batches = [recs[i:i + 4] for i in range(0, len(recs), 4)]
    fake_llm.replies = {i: " ".join(s["symbol"] for s in b) for i, b in enumerate(batches)}

    _parse(section_writer.write_section("picks", report_data_weekly, model="m"))
    assert len(fake_llm.calls) == n_batches


def test_ch8_continuation_heading_stripped_from_accumulated_not_from_chunk(
        fake_llm, report_data_weekly):
    """旧路径也是先把原文流给用户、再剥标题入库。chunk 保留原样，累积正文剥掉。"""
    recs = report_data_weekly["top_stocks"]["buy_recommendations"]
    batches = [recs[i:i + 4] for i in range(0, len(recs), 4)]
    covered = [" ".join(s["symbol"] for s in b) for b in batches]
    fake_llm.replies = {0: covered[0],
                        1: "## 8. 重点买入标的深度分析\n" + covered[1]}

    events = _parse(section_writer.write_section("picks", report_data_weekly, model="m"))
    body = "".join(e["content"] for e in _by_event(events, "chunk"))
    assert "## 8. 重点买入标的深度分析" in body, "chunk 应逐字透传原文"

    if len(batches) > 2:
        third_user = fake_llm.calls[2]["user"]
        assert "## 8. 重点买入标的深度分析" not in third_user
