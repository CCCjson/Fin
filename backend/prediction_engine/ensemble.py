"""
多模型集成融合
- 方向：加权投票（LSTM 0.4 + XGBoost 0.6）
- 价格：以 LSTM 为主
- 置信度：两模型一致性决定
"""
from typing import Any, Dict
from loguru import logger


class EnsemblePredictor:
    """集成预测器"""

    LSTM_WEIGHT = 0.4
    XGB_WEIGHT = 0.6

    @staticmethod
    def combine(lstm_result: Dict[str, Any], xgb_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        融合 LSTM 和 XGBoost 的预测结果。

        Args:
            lstm_result: LSTM 预测结果
            xgb_result: XGBoost 预测结果

        Returns:
            集成后的预测结果
        """
        lstm_dir = lstm_result["direction"]
        xgb_dir = xgb_result["direction"]
        xgb_prob = xgb_result["probability_up"]

        # LSTM 方向 → 转概率
        lstm_return = lstm_result["predicted_return"]
        lstm_prob_up = 0.5 + min(max(lstm_return * 5, -0.5), 0.5)  # 映射到 [0, 1]

        # 加权方向概率
        combined_prob = (EnsemblePredictor.LSTM_WEIGHT * lstm_prob_up +
                         EnsemblePredictor.XGB_WEIGHT * xgb_prob)
        direction = "UP" if combined_prob > 0.5 else "DOWN"

        # 置信度：两模型一致时高，不一致时低
        agree = lstm_dir == xgb_dir
        if agree:
            confidence = 0.5 + abs(combined_prob - 0.5)  # 0.5 ~ 1.0
        else:
            confidence = 0.5 - abs(combined_prob - 0.5) * 0.5  # 降低置信度

        confidence = round(min(max(confidence, 0.0), 1.0), 2)

        result = {
            "direction": direction,
            "confidence": confidence,
            "predicted_prices": lstm_result["predicted_prices"],
            "predicted_return": lstm_result["predicted_return"],
            "models": {
                "lstm": {
                    "direction": lstm_dir,
                    "predicted_prices": lstm_result["predicted_prices"],
                    "predicted_return": lstm_result["predicted_return"],
                    "mse": lstm_result.get("mse", 0),
                },
                "xgboost": {
                    "direction": xgb_dir,
                    "probability_up": xgb_prob,
                    "accuracy": xgb_result.get("accuracy", 0),
                },
            },
            "agreement": agree,
        }

        logger.info(
            f"集成预测: {direction} (置信度 {confidence:.0%}), "
            f"LSTM={lstm_dir}, XGB={xgb_dir}, 一致={'是' if agree else '否'}"
        )
        return result
