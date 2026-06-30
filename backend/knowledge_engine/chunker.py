"""
文本切片 — 按 token 数切，优先段落/章节边界再滑窗，避免跨段语义割裂。

token 计数用 embedding 模型自带的 tokenizer（只加载 tokenizer，很轻，不碰 2GB 权重）。
"""
import re
import threading
from typing import List, Dict, Optional

from knowledge_engine.config import get_embed_model, get_chunk_tokens, get_chunk_overlap

# transformers 首次 import 前同步代理 env（见 embedding.py 说明）
from net_proxy import apply_proxy_env as _apply_proxy_env
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


def _window_split(tokens: List[int], size: int, overlap: int) -> List[List[int]]:
    """对超长段落做带 overlap 的滑窗切。"""
    step = max(1, size - overlap)
    windows = []
    i = 0
    while i < len(tokens):
        windows.append(tokens[i:i + size])
        if i + size >= len(tokens):
            break
        i += step
    return windows


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
            ids = tok.encode(para, add_special_tokens=False)
            for win in _window_split(ids, size, ov):
                emit(tok.decode(win))
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
