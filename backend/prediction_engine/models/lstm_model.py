"""
LSTM 价格预测模型 (PyTorch)
- 输入：过去 window_size 天的特征序列
- 输出：未来 forward_days 天的收盘价
"""
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from loguru import logger
from sklearn.preprocessing import MinMaxScaler

from prediction_engine.models.base import BasePredictor


# ──────────────────── PyTorch 网络定义 ────────────────────

class _LSTMNet(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, num_layers: int,
                 output_dim: int, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, features)
        lstm_out, _ = self.lstm(x)            # (batch, seq_len, hidden)
        last_hidden = lstm_out[:, -1, :]      # 取最后一个时间步
        out = self.dropout(last_hidden)
        out = self.fc(out)                    # (batch, output_dim)
        return out


# ──────────────────── Predictor 封装 ────────────────────

class LSTMPredictor(BasePredictor):
    """LSTM 价格预测器"""

    def __init__(self, window_size: int = 60, hidden_dim: int = 128,
                 num_layers: int = 2, dropout: float = 0.2,
                 epochs: int = 50, lr: float = 0.001, batch_size: int = 32):
        super().__init__("LSTM")
        self.window_size = window_size
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dropout = dropout
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model: Optional[_LSTMNet] = None
        self.feature_scaler = MinMaxScaler()
        self.target_scaler = MinMaxScaler()

    # ──────── 训练 ────────

    def train(self, df: pd.DataFrame, feature_cols: list[str], forward_days: int,
              progress_cb: Optional[Callable] = None) -> Dict[str, float]:
        """
        训练 LSTM 模型。

        Args:
            df: 带特征的 DataFrame
            feature_cols: 特征列
            forward_days: 预测天数
            progress_cb: 可选的进度回调 (epoch, total_epochs, train_loss, val_loss)

        Returns:
            验证集指标 {mse, mae}
        """
        logger.info(f"LSTM 训练开始: {len(df)} 行, {len(feature_cols)} 特征, forward={forward_days}")

        features = df[feature_cols].values.astype(np.float32)
        closes = df["close"].values.astype(np.float32).reshape(-1, 1)

        # 归一化
        features_scaled = self.feature_scaler.fit_transform(features)
        closes_scaled = self.target_scaler.fit_transform(closes)

        # 滑动窗口构建样本
        X, y = self._make_sequences(features_scaled, closes_scaled, forward_days)
        if len(X) < 20:
            raise ValueError(f"样本数不足（{len(X)}），无法训练")

        # 训练/验证拆分（保持时序）
        split = int(len(X) * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        # 转 Tensor
        X_train_t = torch.FloatTensor(X_train).to(self.device)
        y_train_t = torch.FloatTensor(y_train).to(self.device)
        X_val_t = torch.FloatTensor(X_val).to(self.device)
        y_val_t = torch.FloatTensor(y_val).to(self.device)

        # 初始化网络
        input_dim = features_scaled.shape[1]
        self.model = _LSTMNet(input_dim, self.hidden_dim, self.num_layers,
                              forward_days, self.dropout).to(self.device)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr)
        criterion = nn.MSELoss()

        # 训练循环
        best_val_loss = float("inf")
        best_state = None

        for epoch in range(1, self.epochs + 1):
            self.model.train()
            # Mini-batch
            indices = torch.randperm(len(X_train_t))
            epoch_loss = 0.0
            n_batches = 0

            for start in range(0, len(X_train_t), self.batch_size):
                idx = indices[start:start + self.batch_size]
                batch_x = X_train_t[idx]
                batch_y = y_train_t[idx]

                optimizer.zero_grad()
                pred = self.model(batch_x)
                loss = criterion(pred, batch_y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()

                epoch_loss += loss.item()
                n_batches += 1

            train_loss = epoch_loss / max(n_batches, 1)

            # 验证
            self.model.eval()
            with torch.no_grad():
                val_pred = self.model(X_val_t)
                val_loss = criterion(val_pred, y_val_t).item()

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.cpu().clone() for k, v in self.model.state_dict().items()}

            if progress_cb:
                progress_cb(epoch, self.epochs, train_loss, val_loss)

            if epoch % 10 == 0 or epoch == 1:
                logger.info(f"  Epoch {epoch}/{self.epochs}  train_loss={train_loss:.6f}  val_loss={val_loss:.6f}")

        # 恢复最优参数
        if best_state:
            self.model.load_state_dict(best_state)
        self.model.eval()

        # 计算验证集指标（还原到原始尺度）
        with torch.no_grad():
            val_pred_scaled = self.model(X_val_t).cpu().numpy()

        val_pred_prices = self.target_scaler.inverse_transform(val_pred_scaled)
        val_true_prices = self.target_scaler.inverse_transform(y_val)

        mse = float(np.mean((val_pred_prices - val_true_prices) ** 2))
        mae = float(np.mean(np.abs(val_pred_prices - val_true_prices)))

        self.val_metrics = {"mse": round(mse, 4), "mae": round(mae, 4)}
        self.is_trained = True

        logger.success(f"LSTM 训练完成: val_mse={mse:.4f}, val_mae={mae:.4f}")
        return self.val_metrics

    # ──────── 预测 ────────

    def predict(self, df: pd.DataFrame, feature_cols: list[str], forward_days: int) -> Dict[str, Any]:
        """
        预测未来 forward_days 天的价格。

        Returns:
            {
                "predicted_prices": [float, ...],
                "predicted_return": float,
                "direction": "UP" | "DOWN",
                "mse": float
            }
        """
        if not self.is_trained or self.model is None:
            raise RuntimeError("模型未训练，请先调用 train()")

        features = df[feature_cols].values.astype(np.float32)
        features_scaled = self.feature_scaler.transform(features)

        # 取最后 window_size 行作为输入
        seq = features_scaled[-self.window_size:]
        x = torch.FloatTensor(seq).unsqueeze(0).to(self.device)

        self.model.eval()
        with torch.no_grad():
            pred_scaled = self.model(x).cpu().numpy()  # (1, forward_days)

        pred_prices = self.target_scaler.inverse_transform(pred_scaled)[0].tolist()
        pred_prices = [round(p, 2) for p in pred_prices]

        last_close = float(df["close"].iloc[-1])
        predicted_return = (pred_prices[-1] - last_close) / last_close
        direction = "UP" if predicted_return > 0 else "DOWN"

        return {
            "predicted_prices": pred_prices,
            "predicted_return": round(predicted_return, 4),
            "direction": direction,
            "mse": self.val_metrics.get("mse", 0),
        }

    # ──────── 序列构建 ────────

    def _make_sequences(self, features: np.ndarray, targets: np.ndarray,
                        forward_days: int):
        """滑动窗口生成 (X, y) 样本对"""
        X, y = [], []
        for i in range(self.window_size, len(features) - forward_days + 1):
            X.append(features[i - self.window_size:i])
            y.append(targets[i:i + forward_days].flatten())
        return np.array(X), np.array(y)

    # ──────── 持久化 ────────

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if self.model is not None:
            torch.save(self.model.state_dict(), path / "lstm_weights.pt")
        # 保存 scaler 和元数据
        import joblib
        joblib.dump(self.feature_scaler, path / "lstm_feature_scaler.pkl")
        joblib.dump(self.target_scaler, path / "lstm_target_scaler.pkl")
        meta = {
            "window_size": self.window_size,
            "hidden_dim": self.hidden_dim,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "val_metrics": self.val_metrics,
        }
        (path / "lstm_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        logger.info(f"LSTM 模型已保存: {path}")

    def load(self, path: Path) -> None:
        import joblib
        meta = json.loads((path / "lstm_meta.json").read_text())
        self.window_size = meta["window_size"]
        self.hidden_dim = meta["hidden_dim"]
        self.num_layers = meta["num_layers"]
        self.dropout = meta["dropout"]
        self.val_metrics = meta.get("val_metrics", {})

        self.feature_scaler = joblib.load(path / "lstm_feature_scaler.pkl")
        self.target_scaler = joblib.load(path / "lstm_target_scaler.pkl")

        # 重建网络（需要知道 input_dim）
        input_dim = self.feature_scaler.n_features_in_
        # forward_days 从权重文件推断
        state = torch.load(path / "lstm_weights.pt", map_location=self.device, weights_only=True)
        output_dim = state["fc.bias"].shape[0]

        self.model = _LSTMNet(input_dim, self.hidden_dim, self.num_layers,
                              output_dim, self.dropout).to(self.device)
        self.model.load_state_dict(state)
        self.model.eval()
        self.is_trained = True
        logger.info(f"LSTM 模型已加载: {path}")
