#!/usr/bin/env python3
"""
远程 GPU 预测模型训练脚本（部署到远程服务器）

LSTM + XGBoost 集成训练，通过 stdout 输出 NDJSON 事件供 Mac 端实时显示。

用法:
    python predict_train.py --symbol 600519.SH --period 2y --forward_days 5

需要远程服务器安装: torch, xgboost, pandas, numpy, scikit-learn, akshare

事件格式 (stdout NDJSON):
    {"event": "train_start", "total_iters": 100, "config": {...}}
    {"event": "train_step", "iter": 10, "train_loss": 0.0012, "it_sec": 5.2}
    {"event": "val_step", "iter": 10, "val_loss": 0.0015}
    {"event": "progress", "stage": "lstm", "progress": 0.5, "message": "..."}
    {"event": "train_complete", "success": true, "result": {...}}
"""
import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.preprocessing import MinMaxScaler


# ==================== NDJSON 输出 ====================

def emit(event: str, **data):
    """输出一个 NDJSON 事件到 stdout"""
    print(json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)


# ==================== 数据获取 ====================

def fetch_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取股票历史数据"""
    emit("progress", stage="data", progress=0.05, message=f"获取 {symbol} 历史数据...")

    import akshare as ak

    # 判断市场
    if symbol.endswith((".SH", ".SZ")):
        code = symbol.split(".")[0]
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date.replace("-", ""),
                                end_date=end_date.replace("-", ""),
                                adjust="qfq")
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume",
        })
    else:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_date, end=end_date)
        df = df.reset_index()
        df = df.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })

    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    emit("progress", stage="data", progress=0.10, message=f"获取到 {len(df)} 条数据")
    return df


# ==================== 特征工程 ====================

def build_features(df: pd.DataFrame) -> tuple:
    """构建特征矩阵，返回 (df_with_features, feature_columns)"""
    emit("progress", stage="features", progress=0.12, message="计算特征工程...")

    feat = df.copy()
    c = feat["close"]

    # 均线
    for n in [5, 10, 20, 60]:
        feat[f"ma{n}"] = c.rolling(n).mean()
        feat[f"bias_ma{n}"] = (c - feat[f"ma{n}"]) / feat[f"ma{n}"]

    # MACD
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    feat["macd_dif"] = ema12 - ema26
    feat["macd_dea"] = feat["macd_dif"].ewm(span=9).mean()
    feat["macd_hist"] = (feat["macd_dif"] - feat["macd_dea"]) * 2

    # RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    feat["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    # 布林带
    feat["boll_mid"] = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    feat["boll_upper"] = feat["boll_mid"] + 2 * std20
    feat["boll_lower"] = feat["boll_mid"] - 2 * std20
    feat["boll_width"] = (feat["boll_upper"] - feat["boll_lower"]) / feat["boll_mid"]

    # 收益率
    for n in [1, 3, 5, 10, 20]:
        feat[f"return_{n}d"] = c.pct_change(n)

    # 振幅
    feat["amplitude"] = (feat["high"] - feat["low"]) / c.shift(1)

    # 量比
    feat["volume_change"] = feat["volume"].pct_change()
    for n in [5, 10, 20]:
        vol_ma = feat["volume"].rolling(n).mean()
        feat[f"vol_ratio_{n}d"] = feat["volume"] / vol_ma.replace(0, np.nan)

    # 滞后特征
    for lag in [1, 2, 3, 5]:
        feat[f"close_lag{lag}"] = c.shift(lag)
        feat[f"volume_lag{lag}"] = feat["volume"].shift(lag)

    # 去除 NaN
    feat.dropna(inplace=True)
    feat.reset_index(drop=True, inplace=True)

    exclude = {"date", "open", "high", "low", "close", "volume"}
    feature_cols = [col for col in feat.columns if col not in exclude]

    emit("progress", stage="features", progress=0.15, message=f"特征工程完成: {len(feature_cols)} 个特征")
    return feat, feature_cols


# ==================== LSTM 模型 ====================

class LSTMNet(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, num_layers: int,
                 output_dim: int, dropout: float = 0.2):
        super().__init__()
        self.lstm = nn.LSTM(input_size=input_dim, hidden_size=hidden_dim,
                            num_layers=num_layers, batch_first=True,
                            dropout=dropout if num_layers > 1 else 0.0)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        last_hidden = lstm_out[:, -1, :]
        out = self.dropout(last_hidden)
        return self.fc(out)


def train_lstm(df: pd.DataFrame, feature_cols: list, forward_days: int,
               epochs: int, hidden_dim: int, batch_size: int, window_size: int = 60) -> dict:
    """训练 LSTM 模型，实时输出 NDJSON 进度事件"""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    emit("progress", stage="lstm", progress=0.20, message=f"训练 LSTM (device={device})...")

    features = df[feature_cols].values.astype(np.float32)
    closes = df["close"].values.astype(np.float32).reshape(-1, 1)

    # 归一化
    feat_scaler = MinMaxScaler()
    target_scaler = MinMaxScaler()
    features_scaled = feat_scaler.fit_transform(features)
    closes_scaled = target_scaler.fit_transform(closes)

    # 滑动窗口
    X, y = [], []
    for i in range(window_size, len(features_scaled) - forward_days + 1):
        X.append(features_scaled[i - window_size:i])
        y.append(closes_scaled[i:i + forward_days].flatten())
    X, y = np.array(X), np.array(y)

    if len(X) < 20:
        raise ValueError(f"样本不足（{len(X)}），无法训练")

    # 拆分
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    X_train_t = torch.FloatTensor(X_train).to(device)
    y_train_t = torch.FloatTensor(y_train).to(device)
    X_val_t = torch.FloatTensor(X_val).to(device)
    y_val_t = torch.FloatTensor(y_val).to(device)

    # 构建模型
    model = LSTMNet(features_scaled.shape[1], hidden_dim, 2, forward_days).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.MSELoss()

    # 通知前端训练开始
    emit("train_start", total_iters=epochs, config={
        "model": "LSTM", "hidden_dim": hidden_dim, "epochs": epochs,
        "batch_size": batch_size, "device": str(device),
        "train_samples": len(X_train), "val_samples": len(X_val),
    })

    best_val_loss = float("inf")
    best_state = None
    start_time = time.time()

    for epoch in range(1, epochs + 1):
        model.train()
        indices = torch.randperm(len(X_train_t))
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, len(X_train_t), batch_size):
            idx = indices[start:start + batch_size]
            batch_x, batch_y = X_train_t[idx], y_train_t[idx]

            optimizer.zero_grad()
            pred = model(batch_x)
            loss = criterion(pred, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        train_loss = epoch_loss / max(n_batches, 1)
        elapsed = time.time() - start_time
        it_sec = epoch / elapsed if elapsed > 0 else 0

        # 训练步事件
        emit("train_step", iter=epoch, train_loss=round(train_loss, 6), it_sec=round(it_sec, 2))

        # 验证（每 5 轮 + 最后一轮）
        if epoch % 5 == 0 or epoch == epochs:
            model.eval()
            with torch.no_grad():
                val_pred = model(X_val_t)
                val_loss = criterion(val_pred, y_val_t).item()

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

            emit("val_step", iter=epoch, val_loss=round(val_loss, 6))

        # 进度
        p = 0.20 + 0.50 * (epoch / epochs)
        emit("progress", stage="lstm", progress=round(p, 2),
             message=f"LSTM epoch {epoch}/{epochs}, loss={train_loss:.6f}")

    # 恢复最优参数 & 计算验证指标
    if best_state:
        model.load_state_dict(best_state)
    model.eval()

    with torch.no_grad():
        val_pred_scaled = model(X_val_t).cpu().numpy()

    val_pred_prices = target_scaler.inverse_transform(val_pred_scaled)
    val_true_prices = target_scaler.inverse_transform(y_val)

    mse = float(np.mean((val_pred_prices - val_true_prices) ** 2))
    mae = float(np.mean(np.abs(val_pred_prices - val_true_prices)))

    # 保存模型
    save_dir = Path("saved_models") / args.symbol.replace(".", "_")
    save_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), save_dir / "lstm_weights.pt")

    import joblib
    joblib.dump(feat_scaler, save_dir / "lstm_feature_scaler.pkl")
    joblib.dump(target_scaler, save_dir / "lstm_target_scaler.pkl")
    (save_dir / "lstm_meta.json").write_text(json.dumps({
        "window_size": window_size, "hidden_dim": hidden_dim,
        "num_layers": 2, "dropout": 0.2,
        "val_metrics": {"mse": round(mse, 4), "mae": round(mae, 4)},
    }))

    emit("progress", stage="lstm", progress=0.70, message=f"LSTM 完成: MSE={mse:.4f}, MAE={mae:.4f}")
    return {"mse": round(mse, 4), "mae": round(mae, 4)}


# ==================== XGBoost ====================

def train_xgboost(df: pd.DataFrame, feature_cols: list, forward_days: int) -> dict:
    """训练 XGBoost 模型"""
    import xgboost as xgb
    from sklearn.metrics import accuracy_score, roc_auc_score

    emit("progress", stage="xgboost", progress=0.75, message="训练 XGBoost...")

    X = df[feature_cols].values.astype(np.float32)
    closes = df["close"].values
    future_return = pd.Series(closes).shift(-forward_days) / pd.Series(closes) - 1
    labels = (future_return > 0).astype(int).values

    valid_mask = ~np.isnan(future_return.values)
    X, labels = X[valid_mask], labels[valid_mask]

    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = labels[:split], labels[split:]

    model = xgb.XGBClassifier(
        objective="binary:logistic", eval_metric="logloss",
        use_label_encoder=False, verbosity=0,
        n_estimators=200, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        tree_method="gpu_hist" if torch.cuda.is_available() else "hist",
        device="cuda" if torch.cuda.is_available() else "cpu",
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    val_proba = model.predict_proba(X_val)[:, 1]
    val_pred = (val_proba > 0.5).astype(int)
    accuracy = float(accuracy_score(y_val, val_pred))
    try:
        auc = float(roc_auc_score(y_val, val_proba))
    except ValueError:
        auc = 0.5

    # 保存
    save_dir = Path("saved_models") / args.symbol.replace(".", "_")
    save_dir.mkdir(parents=True, exist_ok=True)
    model.save_model(str(save_dir / "xgb_model.json"))
    (save_dir / "xgb_meta.json").write_text(json.dumps({
        "n_estimators": 200, "max_depth": 6, "learning_rate": 0.05,
        "val_metrics": {"accuracy": round(accuracy, 4), "auc": round(auc, 4)},
    }))

    emit("progress", stage="xgboost", progress=0.90,
         message=f"XGBoost 完成: Acc={accuracy:.4f}, AUC={auc:.4f}")
    return {"accuracy": round(accuracy, 4), "auc": round(auc, 4)}


# ==================== 主流程 ====================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="远程 GPU 预测模型训练")
    parser.add_argument("--symbol", required=True, help="股票代码，如 600519.SH")
    parser.add_argument("--period", default="2y", help="训练数据周期")
    parser.add_argument("--forward_days", type=int, default=5, help="预测天数")
    parser.add_argument("--epochs", type=int, default=100, help="LSTM 训练轮数")
    parser.add_argument("--hidden_dim", type=int, default=256, help="LSTM 隐藏层维度")
    parser.add_argument("--batch_size", type=int, default=64, help="Batch size")
    args = parser.parse_args()

    try:
        # 1. 获取数据
        years = int(args.period.replace("y", ""))
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=years * 365)).isoformat()
        df = fetch_data(args.symbol, start_date, end_date)

        if len(df) < 120:
            emit("error", message=f"数据不足: {args.symbol} 仅有 {len(df)} 行，至少需要 120 行")
            sys.exit(1)

        # 2. 特征工程
        df_feat, feature_cols = build_features(df)

        # 3. LSTM 训练
        lstm_metrics = train_lstm(
            df_feat, feature_cols, args.forward_days,
            epochs=args.epochs, hidden_dim=args.hidden_dim, batch_size=args.batch_size,
        )

        # 4. XGBoost 训练
        xgb_metrics = train_xgboost(df_feat, feature_cols, args.forward_days)

        # 5. 完成
        emit("train_complete", success=True, result={
            "symbol": args.symbol,
            "lstm": lstm_metrics,
            "xgboost": xgb_metrics,
            "data_points": len(df_feat),
            "train_range": [start_date, end_date],
        })

    except Exception as e:
        emit("train_complete", success=False, error=str(e))
        sys.exit(1)
