"""
XGBoost 涨跌分类模型
- 输入：当日特征向量
- 输出：涨跌概率
"""
import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import xgboost as xgb
from loguru import logger
from sklearn.metrics import accuracy_score, roc_auc_score

from prediction_engine.models.base import BasePredictor


class XGBoostClassifier(BasePredictor):
    """XGBoost 涨跌方向分类器"""

    def __init__(self, n_estimators: int = 200, max_depth: int = 6,
                 learning_rate: float = 0.05, subsample: float = 0.8,
                 colsample_bytree: float = 0.8):
        super().__init__("XGBoost")
        self.params = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
        }
        self.model: Optional[xgb.XGBClassifier] = None

    # ──────── 训练 ────────

    def train(self, df: pd.DataFrame, feature_cols: list[str],
              forward_days: int) -> Dict[str, float]:
        """
        训练 XGBoost 分类模型。
        标签：未来 forward_days 天收益率 > 0 → 1 (UP)，否则 → 0 (DOWN)

        Returns:
            验证集指标 {accuracy, auc}
        """
        logger.info(f"XGBoost 训练开始: {len(df)} 行, {len(feature_cols)} 特征, forward={forward_days}")

        X = df[feature_cols].values.astype(np.float32)
        closes = df["close"].values

        # 构建标签：forward_days 天后的收益率是否 > 0
        future_return = pd.Series(closes).shift(-forward_days) / pd.Series(closes) - 1
        labels = (future_return > 0).astype(int).values

        # 去掉末尾 NaN
        valid_mask = ~np.isnan(future_return.values)
        X = X[valid_mask]
        labels = labels[valid_mask]

        if len(X) < 50:
            raise ValueError(f"有效样本不足（{len(X)}），无法训练")

        # Walk-forward 拆分（80/20）
        split = int(len(X) * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = labels[:split], labels[split:]

        # 训练
        self.model = xgb.XGBClassifier(
            objective="binary:logistic",
            eval_metric="logloss",
            use_label_encoder=False,
            verbosity=0,
            **self.params,
        )
        self.model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        # 验证集指标
        val_proba = self.model.predict_proba(X_val)[:, 1]
        val_pred = (val_proba > 0.5).astype(int)
        accuracy = float(accuracy_score(y_val, val_pred))
        try:
            auc = float(roc_auc_score(y_val, val_proba))
        except ValueError:
            auc = 0.5  # 全为同一类时 AUC 无法计算

        self.val_metrics = {"accuracy": round(accuracy, 4), "auc": round(auc, 4)}
        self.is_trained = True

        logger.success(f"XGBoost 训练完成: accuracy={accuracy:.4f}, auc={auc:.4f}")
        return self.val_metrics

    # ──────── 预测 ────────

    def predict(self, df: pd.DataFrame, feature_cols: list[str],
                forward_days: int) -> Dict[str, Any]:
        """
        预测涨跌方向。

        Returns:
            {
                "direction": "UP" | "DOWN",
                "probability_up": float,
                "accuracy": float
            }
        """
        if not self.is_trained or self.model is None:
            raise RuntimeError("模型未训练，请先调用 train()")

        # 取最后一行特征
        X = df[feature_cols].values[-1:].astype(np.float32)
        prob_up = float(self.model.predict_proba(X)[0, 1])
        direction = "UP" if prob_up > 0.5 else "DOWN"

        return {
            "direction": direction,
            "probability_up": round(prob_up, 4),
            "accuracy": self.val_metrics.get("accuracy", 0),
        }

    # ──────── 持久化 ────────

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.model.save_model(str(path / "xgb_model.json"))
        meta = {**self.params, "val_metrics": self.val_metrics}
        (path / "xgb_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        logger.info(f"XGBoost 模型已保存: {path}")

    def load(self, path: Path) -> None:
        meta = json.loads((path / "xgb_meta.json").read_text())
        self.val_metrics = meta.get("val_metrics", {})
        self.model = xgb.XGBClassifier()
        self.model.load_model(str(path / "xgb_model.json"))
        self.is_trained = True
        logger.info(f"XGBoost 模型已加载: {path}")
