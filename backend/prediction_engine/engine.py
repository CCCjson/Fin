"""
预测引擎 — 主入口
提供 train / predict / get_model_info 等核心方法
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd
from loguru import logger

from common.market import infer_market_from_symbol
from common.market_time import market_today
from data_engine import DataEngine
from prediction_engine.features import FeatureBuilder
from prediction_engine.models.lstm_model import LSTMPredictor
from prediction_engine.models.xgboost_model import XGBoostClassifier
from prediction_engine.ensemble import EnsemblePredictor
from data_engine.storage.database import get_session
from data_engine.storage.models import PredictionRecord, TrainedModelRecord

SAVED_MODELS_DIR = Path(__file__).parent / "saved_models"


class PredictionEngine:
    """股价预测引擎"""

    def __init__(self):
        self.data_engine = DataEngine()
        self.feature_builder = FeatureBuilder()

    # ──────────────────── 训练 ────────────────────

    def train(self, symbol: str, period: str = "2y",
              forward_days: int = 5,
              progress_cb: Optional[Callable] = None) -> Dict[str, Any]:
        """
        训练 LSTM + XGBoost 模型。

        Args:
            symbol: 股票代码
            period: 训练数据周期 (1y/2y/3y/5y)
            forward_days: 预测天数
            progress_cb: 进度回调 (stage, progress, message)

        Returns:
            训练结果摘要
        """
        def _cb(stage: str, progress: float, message: str):
            if progress_cb:
                progress_cb(stage, progress, message)

        _cb("data", 0.05, f"获取 {symbol} 历史数据中...")

        # 1. 获取数据
        # 数据窗口按这只票自己市场的今天：美股在美东，用北京日期会多要一天空数据
        mkt = infer_market_from_symbol(symbol)
        today = market_today(mkt)
        end_date = today.isoformat()
        years = int(period.replace("y", ""))
        start_date = (today - timedelta(days=years * 365)).isoformat()

        df = self.data_engine.get_daily_data(symbol, start_date, end_date)
        if df is None or len(df) < 120:
            raise ValueError(f"数据不足: {symbol} 仅有 {len(df) if df is not None else 0} 行，至少需要 120 行")

        _cb("features", 0.15, "计算特征工程中...")

        # 2. 特征工程
        df_feat = self.feature_builder.build(df)
        feature_cols = self.feature_builder.feature_columns

        _cb("lstm", 0.20, "训练 LSTM 模型...")

        # 3. 训练 LSTM
        lstm = LSTMPredictor()

        def lstm_progress(epoch, total, train_loss, val_loss):
            p = 0.20 + 0.45 * (epoch / total)
            _cb("lstm", p, f"训练 LSTM... epoch {epoch}/{total}, loss: {train_loss:.4f}")

        lstm_metrics = lstm.train(df_feat, feature_cols, forward_days, progress_cb=lstm_progress)

        _cb("xgboost", 0.70, "训练 XGBoost 模型...")

        # 4. 训练 XGBoost
        xgb_model = XGBoostClassifier()
        xgb_metrics = xgb_model.train(df_feat, feature_cols, forward_days)

        _cb("save", 0.90, "保存模型中...")

        # 5. 保存模型
        model_dir = SAVED_MODELS_DIR / symbol.replace(".", "_")
        lstm.save(model_dir)
        xgb_model.save(model_dir)

        # 6. 记录到数据库
        train_start = df["date"].iloc[0]
        train_end = df["date"].iloc[-1]
        if isinstance(train_start, str):
            train_start = datetime.strptime(train_start, "%Y-%m-%d").date()
        if isinstance(train_end, str):
            train_end = datetime.strptime(train_end, "%Y-%m-%d").date()

        self._save_train_record(
            symbol=symbol,
            train_period=period,
            data_points=len(df_feat),
            train_start=train_start,
            train_end=train_end,
            lstm_metrics=lstm_metrics,
            xgb_metrics=xgb_metrics,
            model_dir=str(model_dir),
        )

        _cb("complete", 1.0, "训练完成！")

        result = {
            "symbol": symbol,
            "lstm": lstm_metrics,
            "xgboost": xgb_metrics,
            "data_points": len(df_feat),
            "train_range": [str(train_start), str(train_end)],
        }
        logger.success(f"模型训练完成: {symbol}, LSTM MSE={lstm_metrics['mse']}, XGB Acc={xgb_metrics['accuracy']}")
        return result

    # ──────────────────── 预测 ────────────────────

    def predict(self, symbol: str, forward_days: int = 5) -> Dict[str, Any]:
        """
        生成预测结果。

        Args:
            symbol: 股票代码
            forward_days: 预测天数

        Returns:
            集成预测结果
        """
        model_dir = SAVED_MODELS_DIR / symbol.replace(".", "_")
        if not model_dir.exists():
            raise FileNotFoundError(f"未找到 {symbol} 的训练模型，请先训练")

        # 加载模型
        lstm = LSTMPredictor()
        lstm.load(model_dir)

        xgb_model = XGBoostClassifier()
        xgb_model.load(model_dir)

        # 获取最近数据
        # 窗口 + 落库的 prediction_date 都按这只票市场的今天算 ——
        # prediction_date 是 validator 判「预测到期没到期」的基准，错时区会偏一天
        mkt = infer_market_from_symbol(symbol)
        today = market_today(mkt)
        end_date = today.isoformat()
        start_date = (today - timedelta(days=180)).isoformat()  # 半年数据足够算特征
        df = self.data_engine.get_daily_data(symbol, start_date, end_date)
        if df is None or len(df) < 80:
            raise ValueError(f"近期数据不足: {symbol}")

        # 特征工程
        df_feat = self.feature_builder.build(df)
        feature_cols = self.feature_builder.feature_columns

        # 推理
        lstm_result = lstm.predict(df_feat, feature_cols, forward_days)
        xgb_result = xgb_model.predict(df_feat, feature_cols, forward_days)

        # 集成
        combined = EnsemblePredictor.combine(lstm_result, xgb_result)

        # 补充元数据
        prediction_date = today
        combined["symbol"] = symbol
        combined["prediction_date"] = prediction_date.isoformat()
        combined["forward_days"] = forward_days

        # 保存预测记录
        self._save_prediction_record(symbol, prediction_date, forward_days, combined)

        return combined

    # ──────────────────── 模型信息 ────────────────────

    def get_model_info(self, symbol: Optional[str] = None) -> list[Dict[str, Any]]:
        """
        查询已训练模型列表。

        Args:
            symbol: 可选，筛选特定股票

        Returns:
            模型信息列表
        """
        session = get_session()
        try:
            q = session.query(TrainedModelRecord).order_by(TrainedModelRecord.created_at.desc())
            if symbol:
                q = q.filter(TrainedModelRecord.symbol == symbol)
            records = q.all()

            result = []
            for r in records:
                val_metrics = json.loads(r.val_metrics) if r.val_metrics else {}
                result.append({
                    "id": r.id,
                    "symbol": r.symbol,
                    "model_type": r.model_type,
                    "train_period": r.train_period,
                    "data_points": r.data_points,
                    "train_start": str(r.train_start) if r.train_start else None,
                    "train_end": str(r.train_end) if r.train_end else None,
                    "val_metrics": val_metrics,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            return result
        finally:
            session.close()

    # ──────────────────── 内部方法 ────────────────────

    def _save_train_record(self, symbol: str, train_period: str,
                           data_points: int, train_start, train_end,
                           lstm_metrics: dict, xgb_metrics: dict,
                           model_dir: str):
        """保存训练记录到数据库"""
        session = get_session()
        try:
            combined_metrics = {"lstm": lstm_metrics, "xgboost": xgb_metrics}
            record = TrainedModelRecord(
                symbol=symbol,
                model_type="ensemble",
                train_period=train_period,
                data_points=data_points,
                train_start=train_start,
                train_end=train_end,
                val_metrics=json.dumps(combined_metrics),
                model_path=model_dir,
                status="ready",
            )
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"保存训练记录失败: {e}")
        finally:
            session.close()

    def _save_prediction_record(self, symbol: str, prediction_date: date,
                                forward_days: int, result: dict):
        """保存预测记录到数据库"""
        session = get_session()
        try:
            # 计算预测到期日（简单加日历天数，不考虑交易日）
            target_date = prediction_date + timedelta(days=int(forward_days * 1.5))

            record = PredictionRecord(
                symbol=symbol,
                prediction_date=prediction_date,
                target_date=target_date,
                forward_days=forward_days,
                direction=result["direction"],
                confidence=result["confidence"],
                predicted_prices=json.dumps(result["predicted_prices"]),
                predicted_return=result["predicted_return"],
                lstm_detail=json.dumps(result["models"]["lstm"]),
                xgb_detail=json.dumps(result["models"]["xgboost"]),
            )
            session.add(record)
            session.commit()
            logger.info(f"预测记录已保存: {symbol} {prediction_date}")
        except Exception as e:
            session.rollback()
            logger.error(f"保存预测记录失败: {e}")
        finally:
            session.close()
