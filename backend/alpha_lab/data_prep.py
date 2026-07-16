"""
数据准备 —— alpha_lab 训练/验证集切分与临时落盘的共享纯函数。

从 `AlphaLabEngine._prepare_data` / `_cleanup_temp_data` 抽出，供旧引擎与
LangGraph 图路径（阶段A）共用。**刻意放在 alpha_lab 顶层而非 graph/ 子包**：
旧引擎依赖它，若放 graph/ 则「回滚即删 graph/」会连带打断旧引擎——放这里保证
删除 graph/ 目录后旧引擎仍完整可用。

数据是确定性的（同 symbols + 日期区间 → 同 CSV），故图路径 resume 时可安全重建。
"""
import os
import shutil
import tempfile

from loguru import logger

from analysis_engine import AnalysisEngine
from data_engine.engine import DataEngine


def prepare_data(
    symbols: list[str],
    data_start: str,
    data_end: str,
) -> tuple[dict[str, str], dict[str, str], dict]:
    """
    准备训练/验证数据，保存为 CSV 临时文件。

    时序分割：前 70% 训练，后 30% 验证。确定性——同入参产出同内容 CSV，
    故 resume 后原临时目录被清理时可用同参数安全重建。

    Args:
        symbols: 股票代码列表（当前引擎主循环只用 symbols[0]，但按多股准备）
        data_start: 数据起始日 YYYY-MM-DD
        data_end: 数据结束日 YYYY-MM-DD

    Returns:
        (train_paths: Dict[symbol->path], val_paths: Dict[symbol->path], data_summary: Dict)
    """
    data_engine = DataEngine()
    train_paths: dict[str, str] = {}
    val_paths: dict[str, str] = {}
    data_summary: dict = {}

    tmpdir = tempfile.mkdtemp(prefix="alab_data_")

    for symbol in symbols:
        logger.info(f"准备数据: {symbol} ({data_start} ~ {data_end})")
        df = data_engine.get_daily_data(
            symbol=symbol,
            start_date=data_start,
            end_date=data_end,
        )

        if df.empty:
            raise ValueError(f"{symbol} 在 {data_start}~{data_end} 期间无数据")

        # 计算技术指标
        df = AnalysisEngine().add_indicators(df)

        # 时序分割：前 70% 训练，后 30% 验证
        split_idx = int(len(df) * 0.7)
        train_df = df.iloc[:split_idx].copy()
        val_df = df.iloc[split_idx:].copy()

        # 保存为 CSV 临时文件
        train_path = os.path.join(tmpdir, f"{symbol}_train.csv")
        val_path = os.path.join(tmpdir, f"{symbol}_val.csv")
        train_df.to_csv(train_path, index=True)
        val_df.to_csv(val_path, index=True)

        train_paths[symbol] = train_path
        val_paths[symbol] = val_path

        # 数据摘要（累积多股票信息）
        sym_summary = {
            "train_days": len(train_df),
            "val_days": len(val_df),
            "total_days": len(df),
            "price_range": f"{df['close'].min():.2f} ~ {df['close'].max():.2f}",
            "avg_volume": f"{df['volume'].mean():,.0f}",
            "latest_price": f"{df['close'].iloc[-1]:.2f}",
        }
        if not data_summary:
            data_summary = sym_summary
        else:
            # 多股票时汇总：天数取最小值，价格范围合并
            data_summary["train_days"] = min(data_summary["train_days"], sym_summary["train_days"])
            data_summary["val_days"] = min(data_summary["val_days"], sym_summary["val_days"])
            data_summary["total_days"] = min(data_summary["total_days"], sym_summary["total_days"])
            data_summary["price_range"] += f" | {symbol}: {sym_summary['price_range']}"
            data_summary["avg_volume"] += f" | {symbol}: {sym_summary['avg_volume']}"
            data_summary["latest_price"] += f" | {symbol}: {sym_summary['latest_price']}"

    logger.info(f"数据准备完成: train={data_summary['train_days']}天, val={data_summary['val_days']}天")
    return train_paths, val_paths, data_summary


def cleanup_temp_data(train_paths: dict, val_paths: dict) -> None:
    """清理临时数据文件（按父目录去重后 rmtree）。"""
    cleaned = set()
    for path in list(train_paths.values()) + list(val_paths.values()):
        parent = os.path.dirname(path)
        if parent not in cleaned and os.path.exists(parent):
            try:
                shutil.rmtree(parent)
                cleaned.add(parent)
            except OSError as e:
                logger.warning(f"清理临时目录失败: {e}")
