"""防复发门禁：出网唯一入口 = `acquisition/`。

## 为什么需要这条门禁

13.4-2 把一切出网取数收进 `acquisition/`（内部再走 `net/` 代理池 + 铁律）。但规范
写「引擎层禁止散写 requests/httpx 出网」没有机器约束时，人总会忘——history 上正是
这样散出一堆裸 `requests.get`/`yf.download`（web_searcher 9 处、overseas_job、板块榜…）。

这条门禁把「谁能出网」结构化：`acquisition/`（唯一出网门面）与 `net/`（出网基础层：
代理/session/bounded）之外，任何 `.py` 直接 import/调用出网库即红。见 CODING_STANDARDS §8（例外三类=硬规矩 6）。

## 侦测什么

- 专用出网库的 import：`yfinance` / `finnhub` / `aiohttp` / `curl_cffi`（这些不会
  为类型标注而 import，命中即出网意图）。
- `requests.<get|post|put|delete|patch|request|Session>` 调用。
- `httpx.<Client|AsyncClient|get|post>` 调用。

（LLM 走 `net.session.make_httpx_client` + llm_client，net/ 已豁免；pytdx 是 TCP 非
HTTP，不在侦测面。）
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.baseline

BACKEND = pathlib.Path(__file__).resolve().parent.parent.parent

_SKIP_DIRS = {"tests", "node_modules", "venv", ".venv", "__pycache__", "backtest_cpp"}
_EXEMPT_TOP = {"acquisition", "net"}          # 出网门面 + 出网基础层，本就该出网
_OUT_LIBS = {"yfinance", "finnhub", "aiohttp", "curl_cffi"}
_REQ_ATTRS = {"get", "post", "put", "delete", "patch", "request", "Session"}
_HTTPX_ATTRS = {"Client", "AsyncClient", "get", "post"}

# 合法例外（CODING_STANDARDS §8 硬规矩 6，**永久**）：localhost 内部服务 / 远端独立 worker。
# AST 分辨不了 localhost，靠人工白名单标注理由。
_LEGAL_EXCEPTIONS = {
    "api/routes/orderbook.py",          # httpx → localhost 撮合引擎
    "api/routes/walk_forward.py",       # requests → localhost C++ 回测服务
    "orderbook/market_maker.py",        # httpx → localhost 撮合引擎
    "services/backtest_cpp_client.py",  # requests → localhost C++ 回测服务
    "scripts/token_bench.py",           # requests → localhost 压测脚本（非生产）
    "remote_scripts/predict_train.py",  # yfinance → 远端训练 worker（独立环境）
}

# 待还债（ratcheting，**只减不增**）：13.4-2 收尾遗留三笔债已全部收编（S8→债务清理）：
# QMT 整删、ingest curl_cffi 下沉 acquisition、finnhub/yfinance 海外出网收进 acquisition
# 并按 net.overseas 注入代理。现已清零——新违规一律走 acquisition，不许在此新增。
_PENDING_DEBT: set[str] = set()

_ALLOWLIST = _LEGAL_EXCEPTIONS | _PENDING_DEBT


def _callee(node: ast.Call):
    f = node.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.value.id, f.attr
    return "", ""


def _has_egress(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in _OUT_LIBS for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _OUT_LIBS:
                return True
        elif isinstance(node, ast.Call):
            base, attr = _callee(node)
            if base == "requests" and attr in _REQ_ATTRS:
                return True
            if base == "httpx" and attr in _HTTPX_ATTRS:
                return True
    return False


def _egress_files() -> set[str]:
    offenders: set[str] = set()
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if set(rel.parts) & _SKIP_DIRS or rel.parts[0] in _EXEMPT_TOP:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if _has_egress(tree):
            offenders.add(str(rel))
    return offenders


def test_no_egress_outside_acquisition():
    offenders = _egress_files() - _ALLOWLIST
    assert not offenders, (
        "这些引擎层文件直接散写出网（requests/httpx/yfinance/finnhub/curl_cffi），"
        "绕过了 acquisition 唯一出网入口。改走 acquisition/（见 CODING_STANDARDS §8 硬规矩 6）；"
        "若确是 localhost/远端例外，登记到 _LEGAL_EXCEPTIONS：\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_allowlist_only_shrinks():
    """白名单里已不再散写出网的文件必须移除（ratcheting，防止债越滚越多）。"""
    stale = _ALLOWLIST - _egress_files()
    assert not stale, (
        "这些文件已不再散写出网（可能已收编进 acquisition），请从白名单移除：\n  "
        + "\n  ".join(sorted(stale))
    )
