"""
AST 白名单安全检查核心 — 从 alpha_lab/sandbox.py 抽出，供多个沙箱消费者共享。

只含"禁止名单 + dunder 逃逸检测"这套静态检查逻辑本身；import 白名单由各消费者
自己定义并传入（策略回测代码允许 pandas/numpy，抓取脚本则完全不允许网络库），
避免出现两份检测逻辑各自维护、一份修了漏洞另一份忘改的风险。
"""
import ast
from typing import List, Tuple

BANNED_NAMES = {
    "os", "sys", "subprocess", "shutil", "pathlib",
    "exec", "eval", "compile", "__import__", "open",
    "globals", "locals", "getattr", "setattr", "delattr",
    "breakpoint", "exit", "quit", "input",
}

BANNED_ATTRIBUTES = {
    "system", "popen", "remove", "rmdir",
    "unlink", "write", "read", "listdir", "makedirs",
    # pandas/IO 写盘出口（策略回测无需落盘，堵掉 to_csv 系写文件）
    "to_csv", "to_pickle", "to_parquet", "to_json",
    "to_feather", "to_hdf", "to_sql", "to_excel", "savefig", "save",
}

# dunder 属性访问逃逸链——沙箱逃逸的核心武器。经典 payload
# `().__class__.__base__.__subclasses__()` 与 `__init__.__globals__["__builtins__"]`
# 都靠这些属性从任意对象爬回 subprocess/os/open。BaseStrategy 子类合法用到的
# __init__ 不在此列（那是 super().__init__() 调用，放行）。
BANNED_DUNDER_ATTRS = {
    "__class__", "__bases__", "__base__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__dict__", "__getattribute__",
    "__reduce__", "__reduce_ex__", "__code__", "__closure__",
    "__func__", "__self__", "__module__", "__import__", "__loader__",
    "__spec__", "__init_subclass__", "__subclasshook__",
}


class ASTChecker(ast.NodeVisitor):
    """AST 白名单检查器。allowed_imports 由调用方传入，检测逻辑本身消费者共享。"""

    def __init__(self, allowed_imports: set):
        self.allowed_imports = allowed_imports
        self.violations: List[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module = alias.name.split(".")[0]
            if module not in self.allowed_imports:
                self.violations.append(f"禁止 import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module = node.module.split(".")[0]
            if module not in self.allowed_imports:
                self.violations.append(f"禁止 from {node.module} import")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id in BANNED_NAMES:
                self.violations.append(f"禁止调用: {node.func.id}()")
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in BANNED_ATTRIBUTES:
                self.violations.append(f"禁止调用: .{node.func.attr}()")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        """属性访问（非调用也要查）——堵 dunder 逃逸链与写盘属性。

        没有这个 visitor，`x.__subclasses__` / `x.to_csv` 这类**取属性**
        （即便不立即调用）都不会被检查，是原沙箱的致命缺口。"""
        attr = node.attr
        if attr in BANNED_DUNDER_ATTRS:
            self.violations.append(f"禁止访问 dunder 属性: .{attr}")
        elif attr in BANNED_ATTRIBUTES:
            self.violations.append(f"禁止访问属性: .{attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id in BANNED_NAMES:
            self.violations.append(f"禁止使用: {node.id}")
        if node.id.startswith("__") and node.id.endswith("__"):
            if node.id not in ("__init__", "__name__", "__main__", "__class__"):
                self.violations.append(f"禁止使用 dunder: {node.id}")
        self.generic_visit(node)


def ast_check(code: str, allowed_imports: set) -> Tuple[bool, List[str]]:
    """解析 + 跑白名单检查。返回 (is_safe, violations)。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"语法错误: {e}"]

    checker = ASTChecker(allowed_imports=allowed_imports)
    checker.visit(tree)
    return len(checker.violations) == 0, checker.violations
