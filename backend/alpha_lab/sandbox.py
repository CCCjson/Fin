"""
安全沙箱 — AST 白名单 + subprocess 隔离执行

AST 白名单检查核心已抽到 common/sandbox_ast.py（供其他沙箱消费者共享），
这里只保留 alpha_lab 专属的 import 白名单 + subprocess 隔离执行回测的逻辑。
"""
import os
import json
import subprocess
import tempfile
from typing import List, Dict, Tuple, Optional
from loguru import logger

from common.sandbox_ast import ast_check as _shared_ast_check

# ==================== AST 白名单配置 ====================

ALLOWED_IMPORTS = {
    "pandas", "numpy", "math", "datetime", "collections",
    "dataclasses", "typing", "enum",
    # 策略框架（在沙箱 runner 中已注入，允许 AI 代码 import）
    "backtest_engine",
}


class Sandbox:
    """安全沙箱 — 两层防护"""

    TIMEOUT_SECONDS = 90

    def ast_check(self, code: str) -> Tuple[bool, List[str]]:
        """
        第一层：AST 静态安全检查

        Returns:
            (is_safe, violations)
        """
        return _shared_ast_check(code, ALLOWED_IMPORTS)

    def execute_backtest(
        self,
        strategy_code: str,
        symbol: str,
        train_data_path: str,
        val_data_path: str,
        initial_capital: float = 1000000.0,
    ) -> Dict:
        """
        第二层：subprocess 隔离执行回测

        将策略代码写入临时文件，用独立进程执行，通过 JSON 传递结果。

        Returns:
            {
                "success": bool,
                "error": str | None,
                "train_metrics": {...},
                "val_metrics": {...},
            }
        """
        backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        with tempfile.TemporaryDirectory() as tmpdir:
            # 写入 AI 生成的策略代码
            strategy_path = os.path.join(tmpdir, "generated_strategy.py")
            with open(strategy_path, "w", encoding="utf-8") as f:
                f.write(strategy_code)

            # 写入回测运行器
            result_path = os.path.join(tmpdir, "result.json")
            runner_path = os.path.join(tmpdir, "runner.py")
            runner_code = self._build_runner(
                backend_root=backend_root,
                strategy_path=strategy_path,
                result_path=result_path,
                symbol=symbol,
                train_data_path=train_data_path,
                val_data_path=val_data_path,
                initial_capital=initial_capital,
            )
            with open(runner_path, "w", encoding="utf-8") as f:
                f.write(runner_code)

            # 在独立进程中执行
            try:
                proc = subprocess.run(
                    ["conda", "run", "-n", "quant", "python", runner_path],
                    capture_output=True,
                    text=True,
                    timeout=self.TIMEOUT_SECONDS,
                    cwd=tmpdir,
                )

                if proc.returncode != 0:
                    stderr = proc.stderr[-1000:] if proc.stderr else "未知错误"
                    logger.warning(f"沙箱回测失败: {stderr}")
                    return {
                        "success": False,
                        "error": stderr,
                        "train_metrics": {},
                        "val_metrics": {},
                    }

                # 读取结果
                if not os.path.exists(result_path):
                    return {
                        "success": False,
                        "error": "回测未生成结果文件",
                        "train_metrics": {},
                        "val_metrics": {},
                    }

                with open(result_path, "r", encoding="utf-8") as f:
                    return json.load(f)

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "error": f"回测执行超时（{self.TIMEOUT_SECONDS}秒）",
                    "train_metrics": {},
                    "val_metrics": {},
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"沙箱执行异常: {e}",
                    "train_metrics": {},
                    "val_metrics": {},
                }

    def _build_runner(
        self,
        backend_root: str,
        strategy_path: str,
        result_path: str,
        symbol: str,
        train_data_path: str,
        val_data_path: str,
        initial_capital: float,
    ) -> str:
        """生成回测运行器脚本"""
        return f'''# -*- coding: utf-8 -*-
"""Alpha Lab 沙箱回测运行器（自动生成，勿手动编辑）"""
import sys
import json
import traceback

sys.path.insert(0, {json.dumps(backend_root)})

import pandas as pd
from backtest_engine.engine import BacktestEngine
from strategy.indicators import TechnicalIndicators

result = {{"success": True, "error": None, "train_metrics": {{}}, "val_metrics": {{}}}}

try:
    # 动态导入 AI 生成的策略
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_strategy", {json.dumps(strategy_path)})
    mod = importlib.util.module_from_spec(spec)

    # 注入依赖让 AI 代码能 import
    sys.modules["backtest_engine"] = __import__("backtest_engine")
    sys.modules["backtest_engine.strategies"] = __import__("backtest_engine.strategies", fromlist=["base"])
    sys.modules["backtest_engine.strategies.base"] = __import__("backtest_engine.strategies.base", fromlist=["BaseStrategy", "StrategyContext"])
    sys.modules["backtest_engine.portfolio"] = __import__("backtest_engine.portfolio", fromlist=["order"])
    sys.modules["backtest_engine.portfolio.order"] = __import__("backtest_engine.portfolio.order", fromlist=["Order", "OrderType"])

    # 直接注入 base 类到模块命名空间（防止 AI 未正确 import）
    from backtest_engine.strategies.base import BaseStrategy, StrategyContext
    from backtest_engine.portfolio.order import Order, OrderType
    mod.BaseStrategy = BaseStrategy
    mod.StrategyContext = StrategyContext
    mod.Order = Order
    mod.OrderType = OrderType

    spec.loader.exec_module(mod)

    if not hasattr(mod, "GeneratedStrategy"):
        result["success"] = False
        result["error"] = "代码中未找到 GeneratedStrategy 类"
    else:
        symbol = {json.dumps(symbol)}

        # ====== 训练集回测 ======
        train_df = pd.read_csv({json.dumps(train_data_path)}, index_col=0, parse_dates=True)
        strategy_train = mod.GeneratedStrategy()
        engine_train = BacktestEngine(initial_capital={initial_capital})
        train_result = engine_train.run(symbol=symbol, data=train_df, strategy=strategy_train)

        if train_result:
            metrics = train_result["metrics"]
            # 序列化 metrics（处理不可序列化的值）
            clean = {{}}
            for k, v in metrics.items():
                if isinstance(v, dict):
                    clean[k] = {{kk: float(vv) if isinstance(vv, (int, float)) else str(vv) for kk, vv in v.items()}}
                elif isinstance(v, (int, float)):
                    clean[k] = v
                else:
                    clean[k] = str(v)
            result["train_metrics"] = clean

        # ====== 验证集回测 ======
        val_df = pd.read_csv({json.dumps(val_data_path)}, index_col=0, parse_dates=True)
        strategy_val = mod.GeneratedStrategy()
        engine_val = BacktestEngine(initial_capital={initial_capital})
        val_result = engine_val.run(symbol=symbol, data=val_df, strategy=strategy_val)

        if val_result:
            metrics = val_result["metrics"]
            clean = {{}}
            for k, v in metrics.items():
                if isinstance(v, dict):
                    clean[k] = {{kk: float(vv) if isinstance(vv, (int, float)) else str(vv) for kk, vv in v.items()}}
                elif isinstance(v, (int, float)):
                    clean[k] = v
                else:
                    clean[k] = str(v)
            result["val_metrics"] = clean

except Exception as e:
    result["success"] = False
    result["error"] = traceback.format_exc()[-1500:]

with open({json.dumps(result_path)}, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, default=str)
'''


def _ndjson(obj: dict) -> str:
    """序列化为 NDJSON 行"""
    return json.dumps(obj, ensure_ascii=False) + "\n"
