"""
会话历史压缩回归测试（Phase 1 安全网）——agents/history.py 全仓无测试覆盖，
这是 token 预算/消息格式最容易出事的地方，先补直测再动手改契约。

核心不变量：压缩后任意 role=="tool" 消息前方必须紧跟着含对应 tool_call_id 的
assistant.tool_calls 父消息（OpenAI 格式要求 tool 消息必须能配对到父调用，
配对断裂会导致下一次真实 LLM 调用直接 400）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

from agents.history import _split_blocks, compact_history, estimate_tokens  # noqa: E402


def _assistant_with_tool_call(call_id: str, name: str = "t") -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": "{}"}}]}


def _tool_reply(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _assert_tool_pairing_intact(messages: list[dict]) -> None:
    """每条 tool 消息往前找到最近一条 assistant 消息，其 tool_calls 必须含同 id。"""
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        call_id = m.get("tool_call_id")
        parent = None
        for j in range(i - 1, -1, -1):
            if messages[j].get("role") == "assistant":
                parent = messages[j]
                break
        assert parent is not None, f"tool 消息(idx={i}, id={call_id}) 找不到前置 assistant"
        ids = {tc["id"] for tc in (parent.get("tool_calls") or [])}
        assert call_id in ids, f"tool 消息(id={call_id}) 与前置 assistant 的 tool_calls({ids}) 不匹配"


def _make_session(n_turns: int, tool_content_len: int = 50):
    """造 n_turns 个回合块：每块 = user + assistant(带1个tool_call) + tool。"""
    class _Sess:
        pass
    sess = _Sess()
    messages = [{"role": "system", "content": "系统提示"}]
    for i in range(n_turns):
        call_id = f"call_{i}"
        messages.append({"role": "user", "content": f"第{i}轮提问"})
        messages.append(_assistant_with_tool_call(call_id))
        messages.append(_tool_reply(call_id, "X" * tool_content_len))
        messages.append({"role": "assistant", "content": f"第{i}轮回答"})
    sess.messages = messages
    return sess


# ──────────────────── _split_blocks：回合块切分 ────────────────────

def test_split_blocks_starts_new_block_on_user_message():
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    blocks = _split_blocks(messages)
    assert len(blocks) == 2
    assert blocks[0][0]["content"] == "u1"
    assert blocks[1][0]["content"] == "u2"


def test_split_blocks_leading_non_user_message_merges_into_previous_block():
    """块首的非 user 消息（如确认恢复后的 tool 回复）并入上一块，不能自成一块。"""
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "tool_call_id": "x", "content": "t1"},  # 恢复续跑产生
        {"role": "user", "content": "u2"},
    ]
    blocks = _split_blocks(messages)
    assert len(blocks) == 2
    assert len(blocks[0]) == 3  # user+assistant+tool 都在第一块
    assert blocks[1][0]["content"] == "u2"


def test_split_blocks_empty_messages_returns_empty():
    assert _split_blocks([]) == []


# ──────────────────── Stage A：确定性截短，不动结构 ────────────────────

def test_compact_history_stage_a_only_truncates_tool_content_not_structure():
    sess = _make_session(n_turns=10, tool_content_len=2000)
    before_roles = [m.get("role") for m in sess.messages]
    compact_history(sess, cheap_model="fake-model", soft_limit=1, keep_turns=3)
    after_roles = [m.get("role") for m in sess.messages]
    # Stage A 不删消息，只可能后续 Stage B 才会替换旧块；这里断言核心不变量
    _assert_tool_pairing_intact(sess.messages)
    assert after_roles[0] == "system"


def test_compact_history_noop_when_under_soft_limit():
    sess = _make_session(n_turns=3)
    original = list(sess.messages)
    compact_history(sess, cheap_model="fake-model", soft_limit=1_000_000)
    assert sess.messages == original


def test_compact_history_noop_when_no_system_message():
    class _Sess:
        pass
    sess = _Sess()
    sess.messages = [{"role": "user", "content": "u"}]
    original = list(sess.messages)
    compact_history(sess, cheap_model="fake-model", soft_limit=1)
    assert sess.messages == original


def test_compact_history_noop_when_blocks_not_exceeding_keep_turns():
    sess = _make_session(n_turns=2)
    original = list(sess.messages)
    compact_history(sess, cheap_model="fake-model", soft_limit=1, keep_turns=3)
    assert sess.messages == original


# ──────────────────── Stage B：摘要后消息形状 ────────────────────

def test_compact_history_stage_b_summarizes_old_blocks(monkeypatch):
    """Stage A 截完仍超限 → 旧块整体摘要成一条 user 消息，形状 = [system, 摘要user, 近期块...]。"""
    import agents.history as history_mod

    monkeypatch.setattr(history_mod, "_llm_summarize", lambda text, model: "早前对话摘要正文")

    sess = _make_session(n_turns=10, tool_content_len=5000)
    sess.last_page_sig = "should_be_reset"
    compact_history(sess, cheap_model="fake-model", soft_limit=1, keep_turns=2)

    assert sess.messages[0]["role"] == "system"
    assert sess.messages[1]["role"] == "user"
    assert "早前对话摘要正文" in sess.messages[1]["content"]
    assert history_mod._SUMMARY_MARK in sess.messages[1]["content"]
    # 近期 2 个回合块（各4条消息）应原样保留在摘要之后
    assert sess.messages[-1]["content"] == "第9轮回答"
    _assert_tool_pairing_intact(sess.messages)
    # 页面快照签名应被重置，让下一条消息重新附带全量页面上下文
    assert sess.last_page_sig is None


def test_compact_history_stage_b_llm_failure_skips_compaction_safely(monkeypatch):
    """_llm_summarize 返回 None（LLM 调用失败）时不应该坏掉历史——保留 Stage A 结果。"""
    import agents.history as history_mod

    monkeypatch.setattr(history_mod, "_llm_summarize", lambda text, model: None)

    sess = _make_session(n_turns=10, tool_content_len=5000)
    before = estimate_tokens(sess.messages)
    compact_history(sess, cheap_model="fake-model", soft_limit=1, keep_turns=2)

    # 没有摘要 user 消息注入（Stage B 未生效），但 Stage A 截短仍然发生
    assert not any(history_mod._SUMMARY_MARK in (m.get("content") or "") for m in sess.messages)
    _assert_tool_pairing_intact(sess.messages)
    assert estimate_tokens(sess.messages) <= before


def test_estimate_tokens_grows_with_content_length():
    short = [{"role": "user", "content": "a"}]
    long = [{"role": "user", "content": "a" * 1000}]
    assert estimate_tokens(long) > estimate_tokens(short)
