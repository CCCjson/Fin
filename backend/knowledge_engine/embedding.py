"""
本地 embedding 单例 — 懒加载，复刻 news_engine/sentiment.py 的线程安全双检锁单例。

默认 BAAI/bge-m3（中英双语、维度 1024、检索友好、无需指令前缀）。
首次 encode 才加载模型（约 2GB 权重），不阻塞进程启动。

向量统一 normalize（L2 归一化）：vec0 默认 L2 距离，归一化后 L2 序 == cosine 序，
检索排序等价于余弦相似度。
"""
import os
import threading
from typing import List, Optional

import numpy as np
from loguru import logger

from knowledge_engine.config import (
    get_embed_model, get_embed_dim, get_embed_device, get_embed_batch_size,
)

# ★ transformers 首次 import 前**强制 HF 离线**：模型是预下载好的，加载走本地缓存、零网络，
# 根治「HF 联网校验(直连不通/死代理)把 httpx client 弄坏 → 缓存模型也加载不了」的坑。
# setdefault 保留逃生口：换新模型首下时 export HF_HUB_OFFLINE=0 即可联网下载。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
# 顺带在 import 前同步代理 env（direct 模式清残留死代理），供联网下载场景用。
from net_proxy import apply_proxy_env as _apply_proxy_env
_apply_proxy_env()


def _robust_load(model_name: str, device: str):
    """
    稳健加载 SentenceTransformer：默认 HF 离线(缓存)，mps 失败退 cpu。
    模型已缓存就零网络；若需联网首下，外部 export HF_HUB_OFFLINE=0。
    """
    from sentence_transformers import SentenceTransformer
    last_err = None
    for dev in [device, "cpu"]:
        try:
            return SentenceTransformer(model_name, device=dev)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"embedding 加载失败 device={dev}: {e}")
    raise RuntimeError(f"embedding 模型加载彻底失败：{last_err}")


class Embedder:
    """本地 embedding 模型（线程安全懒加载单例）。"""

    _instance: Optional["Embedder"] = None
    _init_lock = threading.Lock()

    def __init__(self) -> None:
        self._model = None
        self._load_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "Embedder":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _load(self):
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            model_name = get_embed_model()
            device = get_embed_device()
            logger.info(f"正在加载本地 embedding 模型 {model_name} (device={device})...")
            self._model = _robust_load(model_name, device)
            # 维度强校验：必须与 vec0 建表维度一致（兼容新旧版方法名）
            if hasattr(self._model, "get_embedding_dimension"):
                real_dim = self._model.get_embedding_dimension()
            else:
                real_dim = self._model.get_sentence_embedding_dimension()
            if real_dim != get_embed_dim():
                raise RuntimeError(
                    f"embedding 维度不一致：模型 {model_name} 输出 {real_dim} 维，"
                    f"但 KNOWLEDGE_EMBED_DIM={get_embed_dim()}。请同步 env 并重建 vec_chunks 表。"
                )
            logger.info(f"embedding 模型加载完成（维度 {real_dim}）")

    @property
    def dim(self) -> int:
        return get_embed_dim()

    def encode_passages(self, texts: List[str]) -> List[List[float]]:
        """编码文档片段（入库用）。返回归一化向量的 python list。"""
        self._load()
        vecs = self._model.encode(
            texts,
            batch_size=get_embed_batch_size(),
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vecs, dtype=np.float32).tolist()

    def encode_query(self, text: str) -> List[float]:
        """编码检索 query。bge-m3 无需指令前缀，与 passage 同构。"""
        self._load()
        vec = self._model.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )[0]
        return np.asarray(vec, dtype=np.float32).tolist()
