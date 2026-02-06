"""
数据验证器
"""
import pandas as pd
from typing import Tuple, List
from loguru import logger


class DataValidator:
    """数据验证器"""

    @staticmethod
    def validate_ohlc(df: pd.DataFrame) -> Tuple[bool, List[str]]:
        """验证 OHLC 关系"""
        errors = []

        if df.empty:
            return True, []

        # 检查必需列
        required_cols = ["open", "high", "low", "close"]
        missing_cols = [col for col in required_cols if col not in df.columns]

        if missing_cols:
            errors.append(f"缺少必需列: {missing_cols}")
            return False, errors

        # 检查 high >= max(open, close, low)
        invalid_high = df[df["high"] < df[["open", "close", "low"]].max(axis=1)]
        if not invalid_high.empty:
            errors.append(f"发现 {len(invalid_high)} 行 high < max(open, close, low)")

        # 检查 low <= min(open, close, high)
        invalid_low = df[df["low"] > df[["open", "close", "high"]].min(axis=1)]
        if not invalid_low.empty:
            errors.append(f"发现 {len(invalid_low)} 行 low > min(open, close, high)")

        # 检查价格为正
        negative_prices = df[(df[["open", "high", "low", "close"]] <= 0).any(axis=1)]
        if not negative_prices.empty:
            errors.append(f"发现 {len(negative_prices)} 行价格 <= 0")

        is_valid = len(errors) == 0

        if not is_valid:
            for error in errors:
                logger.warning(error)

        return is_valid, errors

    @staticmethod
    def validate_completeness(
        df: pd.DataFrame,
        expected_records: int = None
    ) -> Tuple[bool, dict]:
        """验证数据完整性"""
        actual_records = len(df)

        if expected_records is None:
            # 如果没有指定期望记录数，只检查是否为空
            return actual_records > 0, {
                "actual_records": actual_records,
                "is_empty": actual_records == 0
            }

        completeness_ratio = actual_records / expected_records if expected_records > 0 else 0

        return completeness_ratio >= 0.9, {
            "expected_records": expected_records,
            "actual_records": actual_records,
            "completeness_ratio": completeness_ratio,
            "missing_records": max(0, expected_records - actual_records)
        }

    @staticmethod
    def validate_duplicates(df: pd.DataFrame) -> Tuple[bool, int]:
        """检查重复数据"""
        if "date" not in df.columns:
            return True, 0

        duplicates = df.duplicated(subset=["date"]).sum()

        if duplicates > 0:
            logger.warning(f"发现 {duplicates} 条重复数据")

        return duplicates == 0, duplicates

    @staticmethod
    def validate(df: pd.DataFrame) -> Tuple[bool, dict]:
        """完整验证流程"""
        results = {
            "ohlc_valid": True,
            "has_duplicates": False,
            "is_complete": True,
            "errors": []
        }

        if df.empty:
            results["errors"].append("数据为空")
            return False, results

        # 1. OHLC 验证
        ohlc_valid, ohlc_errors = DataValidator.validate_ohlc(df)
        results["ohlc_valid"] = ohlc_valid
        results["errors"].extend(ohlc_errors)

        # 2. 重复检查
        no_duplicates, dup_count = DataValidator.validate_duplicates(df)
        results["has_duplicates"] = not no_duplicates
        if not no_duplicates:
            results["errors"].append(f"发现 {dup_count} 条重复数据")

        # 3. 完整性检查
        is_complete, completeness_info = DataValidator.validate_completeness(df)
        results["is_complete"] = is_complete
        results["completeness_info"] = completeness_info

        is_valid = results["ohlc_valid"] and not results["has_duplicates"]

        return is_valid, results
