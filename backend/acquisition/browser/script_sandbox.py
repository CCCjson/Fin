"""
【预留骨架 · 零调用方】抓取脚本安全沙箱。

当前 discover_api/fetch_api/scrape 都是固定的 Python 函数，没有"LLM 现场生成
抓取脚本再执行"这回事，所以本模块暂时没有任何调用方——等真正出现这类需求时
再接线（把 run_script 接进 scrape 的某个分支）。参照 alpha_lab/sandbox.py 的
"AST 白名单 + subprocess 隔离 + JSON 文件 IPC"整体结构，共享
common/sandbox_ast.py 的检查核心。

设计要点：生成脚本不允许自己 import 网络库发请求（SCRAPE_ALLOWED_IMPORTS 里
故意不放 requests/httpx/urllib/socket，AST 检查会直接拦掉这些 import）。需要
网络出口时，脚本只能通过注入进沙箱命名空间的 ScrapeContext 实例调用
fetch_json()，内部委托给已加固的 acquisition.browser.reverse.fast_fetch，
让脚本的网络出口可审计、可限速，而不是给它裸 socket。
"""
import os
import tempfile
from typing import Dict, List, Optional, Tuple

from common.sandbox_ast import ast_check

# 刻意不含任何网络库——脚本发请求只能走注入的 ScrapeContext，不能自己 import
SCRAPE_ALLOWED_IMPORTS = {"re", "json", "datetime", "typing", "math", "collections"}

TIMEOUT_SECONDS = 30


class ScrapeContext:
    """注入进沙箱执行命名空间的受控网络出口（预留，未接入真实实现）。"""

    def fetch_json(self, url: str, params: Optional[dict] = None) -> dict:
        """委托给已加固的 fast_fetch（走已有代理/cookie/并发闸门机制），
        而不是让脚本自己拿到裸网络能力。"""
        raise NotImplementedError("预留骨架：等『LLM 现场生成抓取脚本』功能落地时再实现")


def check_script(code: str) -> Tuple[bool, List[str]]:
    """AST 白名单检查（复用 common.sandbox_ast 的核心逻辑）。"""
    return ast_check(code, SCRAPE_ALLOWED_IMPORTS)


def run_script(code: str, session_id: str, timeout: int = TIMEOUT_SECONDS) -> Dict:
    """
    预留骨架：subprocess 隔离执行抓取脚本，JSON 文件传递结果
    （结构参照 alpha_lab/sandbox.py 的 execute_backtest/_build_runner）。

    当前没有调用方；先做通过检查就返回，真正接线时再补 runner 拼装 + 执行。
    """
    ok, violations = check_script(code)
    if not ok:
        return {"success": False, "error": "AST 检查未通过", "violations": violations}

    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = os.path.join(tmpdir, "generated_scrape.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
        result_path = os.path.join(tmpdir, "result.json")

        # TODO: 接线时在这里拼装真正的 runner（注入 ScrapeContext，写 result.json），
        # 参照 alpha_lab/sandbox.py 的 _build_runner，再用 subprocess.run(
        #   ["conda", "run", "-n", "quant", "python", runner_path],
        #   timeout=timeout, cwd=tmpdir, capture_output=True, text=True,
        # ) 执行并读回 result_path。
        return {"success": False, "error": "run_script 骨架未接线（session_id=%s）" % session_id}
