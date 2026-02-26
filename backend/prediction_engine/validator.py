"""
预测验证器 — 回填实际结果 + 评估指标计算
"""
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np
from loguru import logger

from data_engine import DataEngine
from data_engine.storage.database import get_session
from data_engine.storage.models import PredictionRecord


class PredictionValidator:
    """预测验证器"""

    def __init__(self):
        self.data_engine = DataEngine()

    # ──────────────────── 自动回填 ────────────────────

    def backfill_outcomes(self) -> Dict[str, Any]:
        """
        扫描所有已到期但未回填的预测记录，拉取实际数据并回填。

        Returns:
            回填结果统计
        """
        session = get_session()
        today = date.today()
        filled = 0
        errors = 0

        try:
            # 查找到期且未回填的记录
            pending = (
                session.query(PredictionRecord)
                .filter(
                    PredictionRecord.target_date <= today,
                    PredictionRecord.actual_return.is_(None),
                )
                .all()
            )

            if not pending:
                logger.info("没有待回填的预测记录")
                return {"total": 0, "filled": 0, "errors": 0}

            logger.info(f"找到 {len(pending)} 条待回填记录")

            for record in pending:
                try:
                    self._backfill_single(session, record)
                    filled += 1
                except Exception as e:
                    errors += 1
                    logger.warning(f"回填 {record.symbol} ({record.prediction_date}) 失败: {e}")

            session.commit()
            logger.success(f"回填完成: 成功 {filled}, 失败 {errors}")
            return {"total": len(pending), "filled": filled, "errors": errors}

        except Exception as e:
            session.rollback()
            logger.error(f"回填任务失败: {e}")
            raise
        finally:
            session.close()

    def _backfill_single(self, session, record: PredictionRecord):
        """回填单条预测记录"""
        pred_date = record.prediction_date
        if isinstance(pred_date, str):
            pred_date = datetime.strptime(pred_date, "%Y-%m-%d").date()

        target_date = record.target_date
        if isinstance(target_date, str):
            target_date = datetime.strptime(target_date, "%Y-%m-%d").date()

        # 获取预测日到到期日之间的实际数据
        df = self.data_engine.get_daily_data(
            symbol=record.symbol,
            start_date=pred_date.isoformat(),
            end_date=target_date.isoformat(),
            db_only=True,
        )

        if df is None or len(df) < 2:
            raise ValueError("实际数据不足")

        # 预测起始日的收盘价
        base_close = float(df["close"].iloc[0])

        # 取预测日之后的 forward_days 个交易日的收盘价
        actual_df = df.iloc[1:]  # 去掉预测日当天
        actual_prices = actual_df["close"].head(record.forward_days).tolist()

        if not actual_prices:
            raise ValueError("预测日之后没有交易数据")

        actual_return = (actual_prices[-1] - base_close) / base_close
        actual_direction = "UP" if actual_return > 0 else "DOWN"
        outcome = "WIN" if actual_direction == record.direction else "LOSS"

        record.actual_prices = json.dumps([round(p, 2) for p in actual_prices])
        record.actual_return = round(actual_return, 4)
        record.outcome = outcome

    # ──────────────────── 历史预测查询 ────────────────────

    def get_history(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        查询历史预测记录。

        Args:
            symbol: 可选，筛选特定股票
            limit: 返回数量限制

        Returns:
            预测记录列表
        """
        session = get_session()
        try:
            q = session.query(PredictionRecord).order_by(PredictionRecord.prediction_date.desc())
            if symbol:
                q = q.filter(PredictionRecord.symbol == symbol)
            records = q.limit(limit).all()

            return [self._record_to_dict(r) for r in records]
        finally:
            session.close()

    # ──────────────────── 评估指标 ────────────────────

    def calc_performance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        计算预测表现指标。

        Args:
            symbol: 可选，筛选特定股票

        Returns:
            表现指标字典
        """
        session = get_session()
        try:
            q = session.query(PredictionRecord)
            if symbol:
                q = q.filter(PredictionRecord.symbol == symbol)
            records = q.order_by(PredictionRecord.prediction_date.asc()).all()

            total = len(records)
            evaluated = [r for r in records if r.outcome is not None]
            pending = total - len(evaluated)

            if not evaluated:
                return {
                    "total_predictions": total,
                    "evaluated": 0,
                    "pending": pending,
                    "overall": None,
                    "by_model": None,
                    "confidence_calibration": [],
                    "cumulative_return": 0,
                    "recent_streak": None,
                }

            # 方向准确率
            wins = [r for r in evaluated if r.outcome == "WIN"]
            direction_accuracy = len(wins) / len(evaluated)

            # 价格误差
            mae_list, rmse_list = [], []
            for r in evaluated:
                pred_prices = json.loads(r.predicted_prices) if r.predicted_prices else []
                actual_prices = json.loads(r.actual_prices) if r.actual_prices else []
                if pred_prices and actual_prices:
                    min_len = min(len(pred_prices), len(actual_prices))
                    p = np.array(pred_prices[:min_len])
                    a = np.array(actual_prices[:min_len])
                    mae_list.append(float(np.mean(np.abs(p - a))))
                    rmse_list.append(float(np.sqrt(np.mean((p - a) ** 2))))

            price_mae = np.mean(mae_list) if mae_list else None
            price_rmse = np.mean(rmse_list) if rmse_list else None

            # 分模型准确率
            lstm_correct, xgb_correct = 0, 0
            for r in evaluated:
                actual_dir = "UP" if (r.actual_return or 0) > 0 else "DOWN"
                lstm_detail = json.loads(r.lstm_detail) if r.lstm_detail else {}
                xgb_detail = json.loads(r.xgb_detail) if r.xgb_detail else {}
                if lstm_detail.get("direction") == actual_dir:
                    lstm_correct += 1
                if xgb_detail.get("direction") == actual_dir:
                    xgb_correct += 1

            n_eval = len(evaluated)
            lstm_acc = lstm_correct / n_eval
            xgb_acc = xgb_correct / n_eval

            # 置信度校准
            calibration = self._calc_calibration(evaluated)

            # 累计收益（假设按预测方向操作）
            cumulative_return = 0.0
            for r in evaluated:
                if r.actual_return is not None:
                    if r.direction == "UP":
                        cumulative_return += r.actual_return
                    else:
                        cumulative_return -= r.actual_return

            # 当前连胜/连败
            streak = self._calc_streak(evaluated)

            # 平均置信度
            avg_conf = np.mean([r.confidence for r in evaluated if r.confidence])

            return {
                "total_predictions": total,
                "evaluated": n_eval,
                "pending": pending,
                "overall": {
                    "direction_accuracy": round(direction_accuracy, 4),
                    "price_mae": round(price_mae, 4) if price_mae else None,
                    "price_rmse": round(price_rmse, 4) if price_rmse else None,
                    "avg_confidence": round(float(avg_conf), 4),
                },
                "by_model": {
                    "lstm": {"direction_accuracy": round(lstm_acc, 4)},
                    "xgboost": {"direction_accuracy": round(xgb_acc, 4)},
                    "ensemble": {"direction_accuracy": round(direction_accuracy, 4)},
                },
                "confidence_calibration": calibration,
                "cumulative_return": round(cumulative_return, 4),
                "recent_streak": streak,
            }
        finally:
            session.close()

    # ──────────────────── 辅助方法 ────────────────────

    @staticmethod
    def _calc_calibration(evaluated: list) -> List[Dict[str, Any]]:
        """按置信度分桶，计算每桶实际准确率"""
        buckets = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01)]
        result = []
        for low, high in buckets:
            in_bucket = [r for r in evaluated if low <= (r.confidence or 0) < high]
            if in_bucket:
                actual_acc = len([r for r in in_bucket if r.outcome == "WIN"]) / len(in_bucket)
            else:
                actual_acc = None
            result.append({
                "bucket": f"{low:.1f}-{high:.1f}".replace("1.0", "1.0"),
                "count": len(in_bucket),
                "actual_accuracy": round(actual_acc, 4) if actual_acc is not None else None,
            })
        return result

    @staticmethod
    def _calc_streak(evaluated: list) -> Optional[Dict[str, Any]]:
        """计算当前连胜/连败"""
        if not evaluated:
            return None
        # 按时间倒序看
        sorted_records = sorted(evaluated, key=lambda r: r.prediction_date, reverse=True)
        first_outcome = sorted_records[0].outcome
        count = 0
        for r in sorted_records:
            if r.outcome == first_outcome:
                count += 1
            else:
                break
        return {"type": first_outcome, "count": count}

    @staticmethod
    def _record_to_dict(r: PredictionRecord) -> Dict[str, Any]:
        """PredictionRecord → dict"""
        return {
            "id": r.id,
            "symbol": r.symbol,
            "prediction_date": str(r.prediction_date),
            "target_date": str(r.target_date) if r.target_date else None,
            "forward_days": r.forward_days,
            "direction": r.direction,
            "confidence": r.confidence,
            "predicted_prices": json.loads(r.predicted_prices) if r.predicted_prices else [],
            "predicted_return": r.predicted_return,
            "actual_prices": json.loads(r.actual_prices) if r.actual_prices else None,
            "actual_return": r.actual_return,
            "outcome": r.outcome,
            "lstm_detail": json.loads(r.lstm_detail) if r.lstm_detail else None,
            "xgb_detail": json.loads(r.xgb_detail) if r.xgb_detail else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
