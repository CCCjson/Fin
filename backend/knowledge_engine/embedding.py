"""
本地 embedding 单例 — 懒加载，复刻 news_engine/sentiment.py 的线程安全双检锁单例。

默认 BAAI/bge-m3（中英双语、维度 1024、检索友好、无需指令前缀）。
首次 encode 才加载模型（约 2GB 权重），不阻塞进程启动。

向量统一 normalize（L2 归一化）：vec0 默认 L2 距离，归一化后 L2 序 == cosine 序，
检索排序等价于余弦相似度。
"""
import gc
import os
import threading
import time
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


def _idle_ttl() -> int:
    """模型空闲多少秒后自动卸载释放 ~2GB 内存；0/负数 = 关闭卸载（常驻）。"""
    try:
        return int(os.getenv("KNOWLEDGE_EMBED_IDLE_TTL", "1800"))
    except ValueError:
        return 1800


class Embedder:
    """本地 embedding 模型（线程安全懒加载单例 + 闲时自动卸载）。

    模型 ~2GB 权重常驻太奢侈（16GB Mac），空闲超 KNOWLEDGE_EMBED_IDLE_TTL（默认
    1800s）由守望线程卸载，下次 encode 自动重载（约 5~15s）。encode 持有的是锁内
    取出的本地引用，卸载不影响在途编码。
    """

    _instance: Optional["Embedder"] = None
    _init_lock = threading.Lock()

    def __init__(self) -> None:
        self._model = None
        self._load_lock = threading.Lock()
        self._last_used = 0.0
        self._reaper_started = False

    @classmethod
    def get_instance(cls) -> "Embedder":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _acquire_model(self):
        """取模型引用并续闲时钟（锁内，卸载线程与之互斥）。没加载就现场加载。"""
        with self._load_lock:
            if self._model is None:
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
                    self._model = None
                    raise RuntimeError(
                        f"embedding 维度不一致：模型 {model_name} 输出 {real_dim} 维，"
                        f"但 KNOWLEDGE_EMBED_DIM={get_embed_dim()}。请同步 env 并重建 vec_chunks 表。"
                    )
                logger.info(f"embedding 模型加载完成（维度 {real_dim}）")
            self._last_used = time.time()
            self._maybe_start_reaper()
            return self._model

    # ── 闲时卸载 ──
    def _maybe_start_reaper(self) -> None:
        """守望线程（daemon），首次加载后启动一次。TTL≤0 不启（常驻）。"""
        if self._reaper_started or _idle_ttl() <= 0:
            return
        self._reaper_started = True
        threading.Thread(target=self._reaper_loop, name="embed-idle-unload",
                         daemon=True).start()

    def _unload_if_idle(self) -> bool:
        """空闲超 TTL 则卸载。返回是否真的卸载了（供守望线程决定要不要做重量清理）。"""
        ttl = _idle_ttl()
        if ttl <= 0:
            return False
        with self._load_lock:
            if self._model is None or time.time() - self._last_used < ttl:
                return False
            logger.info(f"embedding 模型空闲超 {ttl}s，卸载释放内存（下次使用自动重载）")
            self._model = None
        # 锁外做重量清理：断掉权重引用 + 清 mps 缓存，把 ~2GB 还给系统
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except Exception:  # noqa: BLE001 — 清缓存失败不影响功能
            pass
        return True

    def _reaper_loop(self) -> None:
        while True:
            time.sleep(60)
            try:
                self._unload_if_idle()
            except Exception as e:  # noqa: BLE001 — 守望线程绝不能死
                logger.debug(f"embedding 闲时卸载检查异常（忽略）：{str(e)[:80]}")

    @property
    def dim(self) -> int:
        return get_embed_dim()

    def encode_passages(self, texts: List[str]) -> List[List[float]]:
        """编码文档片段（入库用）。返回归一化向量的 python list。"""
        model = self._acquire_model()
        vecs = model.encode(
            texts,
            batch_size=get_embed_batch_size(),
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        self._last_used = time.time()   # 长批次编码完成后续钟，防编码中被判空闲
        return np.asarray(vecs, dtype=np.float32).tolist()

    def encode_query(self, text: str) -> List[float]:
        """编码检索 query。bge-m3 无需指令前缀，与 passage 同构。"""
        model = self._acquire_model()
        vec = model.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )[0]
        self._last_used = time.time()
        return np.asarray(vec, dtype=np.float32).tolist()
