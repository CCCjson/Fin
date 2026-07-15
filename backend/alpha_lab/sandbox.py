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
    # 注：域5 阶段②起，AI 只写 on_bar(history) 产信号，不再 import backtest_engine
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
        """生成回测运行器脚本（域5 阶段②：AI 只产信号，执行/指标走 C++）"""
        return f'''# -*- coding: utf-8 -*-
"""Alpha Lab 沙箱回测运行器（自动生成，勿手动编辑）。

AI 代码只暴露 on_bar(history)->'buy'/'sell'/None；本 runner 把它交给
alpha_lab.signal_runner，逐 bar 只喂历史产信号 → C++ run_signals 执行+算指标。
"""
import sys
import json
import traceback

sys.path.insert(0, {json.dumps(backend_root)})

result = {{"success": True, "error": None, "train_metrics": {{}}, "val_metrics": {{}}}}

try:
    import importlib.util
    from alpha_lab.signal_runner import backtest_via_cpp

    # 动态导入 AI 生成的策略模块（只需 on_bar 函数）
    spec = importlib.util.spec_from_file_location("gen_strategy", {json.dumps(strategy_path)})
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = backtest_via_cpp(
        strategy_module=mod,
        train_csv={json.dumps(train_data_path)},
        val_csv={json.dumps(val_data_path)},
        symbol={json.dumps(symbol)},
        capital={initial_capital},
    )

except Exception:
    result["success"] = False
    result["error"] = traceback.format_exc()[-1500:]

with open({json.dumps(result_path)}, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, default=str)
'''


def _ndjson(obj: dict) -> str:
    """序列化为 NDJSON 行"""
    return json.dumps(obj, ensure_ascii=False) + "\n"
