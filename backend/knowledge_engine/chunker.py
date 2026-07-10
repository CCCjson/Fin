"""
文本切片 — 按 token 数切，优先段落/章节边界再滑窗，避免跨段语义割裂。

token 计数用 embedding 模型自带的 tokenizer（只加载 tokenizer，很轻，不碰 2GB 权重）。
"""
import os
import re
import threading
from typing import List, Dict, Optional

from knowledge_engine.config import get_embed_model, get_chunk_tokens, get_chunk_overlap

# transformers 首次 import 前强制 HF 离线 + 同步代理 env（见 embedding.py 说明）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
from net import apply_proxy_env as _apply_proxy_env
_apply_proxy_env()

_tokenizer = None
_tok_lock = threading.Lock()


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer
    with _tok_lock:
        if _tokenizer is None:
            import os
            from transformers import AutoTokenizer
            name = get_embed_model()
            try:
                _tokenizer = AutoTokenizer.from_pretrained(name)
            except Exception:
                # HF 校验走死代理失败 → 退离线缓存（tokenizer 已随模型缓存）
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                _tokenizer = AutoTokenizer.from_pretrained(name)
    return _tokenizer


def count_tokens(text: str) -> int:
    return len(_get_tokenizer().encode(text, add_special_tokens=False))


def _split_paragraphs(text: str) -> List[str]:
    """按空行/换行切段，去空白。"""
    parts = re.split(r"\n\s*\n|\r\n\r\n", text)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out


def _window_split_text(para: str, size: int, overlap: int) -> List[str]:
    """
    对超长段落做带 overlap 的滑窗切，返回**原文切片**。
    用 tokenizer 的 offset_mapping 按 token 窗口定位字符边界，切原文字符串——
    绝不 tok.decode（decode 会把某些中文标点还原成 <unk>，污染正文）。
    """
    tok = _get_tokenizer()
    try:
        enc = tok(para, add_special_tokens=False, return_offsets_mapping=True)
        offsets = enc["offset_mapping"]
    except Exception:
        # 慢 tokenizer 无 offset_mapping → 退回字符等分（近似）
        offsets = None
    if not offsets:
        step_c = max(1, (size - overlap) * 2)             # 粗估 2 字符/token
        win_c = max(step_c, size * 2)
        return [para[i:i + win_c] for i in range(0, len(para), step_c)] or [para]

    n = len(offsets)
    step = max(1, size - overlap)
    out, i = [], 0
    while i < n:
        j = min(i + size, n)
        start_char = offsets[i][0]
        end_char = offsets[j - 1][1]
        seg = para[start_char:end_char].strip()
        if seg:
            out.append(seg)
        if j >= n:
            break
        i += step
    return out


def chunk_text(text: str, *, chunk_tokens: Optional[int] = None,
               overlap: Optional[int] = None) -> List[Dict]:
    """
    把长文切成 [{seq, text, token_count}]。
    策略：先按段落贪心打包到 ≤chunk_tokens；单段超长则 token 滑窗切。
    """
    if not text or not text.strip():
        return []
    size = chunk_tokens or get_chunk_tokens()
    ov = overlap if overlap is not None else get_chunk_overlap()
    tok = _get_tokenizer()

    chunks: List[Dict] = []

    def emit(s: str):
        s = s.strip()
        if s:
            chunks.append({"seq": len(chunks), "text": s, "token_count": count_tokens(s)})

    buf = ""
    buf_tokens = 0
    for para in _split_paragraphs(text):
        p_tokens = count_tokens(para)
        if p_tokens > size:
            # 先冲掉缓冲，再对超长段滑窗
            if buf:
                emit(buf)
                buf, buf_tokens = "", 0
            for seg in _window_split_text(para, size, ov):
                emit(seg)                                  # 已是原文切片，无 <unk>
            continue
        if buf_tokens + p_tokens <= size:
            buf = f"{buf}\n\n{para}" if buf else para
            buf_tokens += p_tokens
        else:
            emit(buf)
            buf, buf_tokens = para, p_tokens
    if buf:
        emit(buf)
    return chunks
