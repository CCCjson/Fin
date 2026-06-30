"""
外置金融大脑 knowledge_engine — 知识摄入 + 向量检索(RAG) + alpha 提炼层。

刻意保持 import 轻量：不在包 import 时加载 embedding 重模型（懒加载，见 embedding.py）。
"""
