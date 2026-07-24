"""时间口径防复发门禁 —— 受管目录内不许裸取「现在 / 今天」。

## 守的是什么

`date.today()` / `datetime.now()` 拿到的是**服务器本地时间**。项目服务四个市场，
A 股/港股恰好是上海时间所以从没出过事，另外两个不是：

- 美股在美东，本地 today 每天有约 12 小时在问一个**还没发生的交易日**
- crypto 在 UTC，本地 today 与自己 UTC 落库的行情差 8 小时 —— 实测「当日手续费」
  在本地凌晨恒为 0，当日回撤熔断与费用护栏每天瞎 8 小时

真源是 `common/market_time.py`：`market_today(market)` / `market_day_bounds(market)` /
`utc_now()`。

## 三类被咬的写法

| 写法 | 替代 |
|---|---|
| `date.today()` / `datetime.today()` | `market_today(market)` |
| `datetime.now()`（**无参**） | `utc_now()`（写库）/ `market_now(market)`（判几点） |
| `datetime.combine(<某日>, time.min)` | `market_day_bounds(market, day)` |

`datetime.now(tz)` **带参**的不咬 —— 它已经显式声明了时区，不是本门禁要防的「隐式
本地时间」。

## 白名单只减不增

`_ALLOWED` 是**存量豁免基线**，语义同 `pyproject.toml` 的 ruff 豁免表：数字只许往下走。
- 实际 > 基线 → 你新写了裸时间，改成 `market_time` 的接口。
- 实际 < 基线 → 你修好了，**把表里的数字调下来**，把成果锁住。

受管目录随批次扩张（批 1 只挂 crypto 两个域，批 4 加 `data_engine/` + `agents/tools/`，
批 7 扩全后端）。**扩域和降基线都只能单向**。
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.baseline

_BACKEND = pathlib.Path(__file__).resolve().parents[1]

# 受管目录（相对 backend/）。随批次只增不减。
MANAGED_DIRS = (
    "crypto_strategy",
    "crypto_intel_engine",
    "data_engine",
    "agents/tools",
    "trading_engine/risk",
)

# 存量豁免基线：`相对路径 → 允许的裸时间调用数`。**只减不增**。
#
# crypto 两个域已在批 2 清零，**保持空的**；`agents/tools` 在批 4 清零。
# 表里剩下的全是 `data_engine`，分两类，都排在后续批次：
#   - B 类系统时刻（DataUpdateLog 的 started_at/end_time、任务耗时）—— 本就不归时区管，
#     但等全库时间列翻成 UTC 后要一起改成 utc_now()
#   - 「当日边界」类（history_repository 的 cutoff、repository 的 updated_at）—— 要跟
#     存储口径翻转同批改，提前改会和还存着本地时间的列对不上
_ALLOWED: dict[str, int] = {
    "data_engine/daily_pipeline_scheduler.py": 3,
    "data_engine/daily_updater.py": 3,
    "data_engine/deep_history/a_share_job.py": 1,
    "data_engine/engine.py": 2,
    "data_engine/financial_updater.py": 2,
    "data_engine/storage/history_repository.py": 6,
    "data_engine/storage/repository.py": 3,
}


def _is_naive_time_call(node: ast.AST) -> bool:
    """这次调用是不是「隐式拿服务器本地时间」。"""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    attr = node.func.attr
    if attr in ("today",):
        # date.today() / datetime.today()
        return True
    if attr == "now":
        # ⛔ `func.now()` 不归本门禁管：它是 SQLAlchemy 的 SQL 函数，在 SQLite 上落
        # **UTC**，属于「隐式 UTC」这个**反向**问题（列默认值与 Python 侧写入混口径），
        # 由后续批次统一处理。本门禁只咬「隐式本地时间」。
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "func":
            return False
        # datetime.now() 无参才咬；datetime.now(tz)/now(timezone.utc) 是显式的，放行
        return not node.args and not node.keywords
    if attr == "combine":
        # datetime.combine(某日, time.min) —— 「当日边界」的经典错法，比上面两种更隐蔽。
        # ⚠️ 必须限定接收者是 `datetime`：`combine` 是个太常见的方法名，实测
        # `EnsemblePredictor.combine(lstm, xgb)`（prediction_engine/engine.py）就被误报过。
        return isinstance(node.func.value, ast.Name) and node.func.value.id == "datetime"
    return False


def _violations(path: pathlib.Path) -> list[int]:
    """返回该文件里裸时间调用所在的行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted({n.lineno for n in ast.walk(tree) if _is_naive_time_call(n)})


def _scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for d in MANAGED_DIRS:
        for py in sorted((_BACKEND / d).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            lines = _violations(py)
            if lines:
                found[str(py.relative_to(_BACKEND))] = lines
    return found


def test_no_new_naive_time_sources():
    """新增裸 `date.today()` / `datetime.now()` / `datetime.combine` 会被咬住。"""
    found = _scan()
    regressions = {
        rel: lines for rel, lines in found.items()
        if len(lines) > _ALLOWED.get(rel, 0)
    }
    assert not regressions, (
        "受管目录内新增了裸时间调用 —— 请改用 common.market_time "
        "(market_today / market_day_bounds / utc_now / market_now)：\n"
        + "\n".join(f"  {rel}: {len(lines)} 处（基线 {_ALLOWED.get(rel, 0)}），行 {lines}"
                    for rel, lines in sorted(regressions.items()))
    )


def test_baseline_has_no_stale_entries():
    """修好了就要把基线调下来，否则等于把额度留着给下一个人乱用。"""
    actual = {rel: len(lines) for rel, lines in _scan().items()}
    stale = {rel: n for rel, n in _ALLOWED.items() if actual.get(rel, 0) < n}
    assert not stale, (
        "这些文件的裸时间调用已经少于基线，请把 _ALLOWED 里的数字调下来（改好的成果要锁住）：\n"
        + "\n".join(f"  {rel}: 实际 {actual.get(rel, 0)} < 基线 {n}"
                    for rel, n in sorted(stale.items()))
    )


def test_managed_dirs_exist():
    """防止改目录结构后门禁静默失效（扫了个空目录，永远绿）。"""
    for d in MANAGED_DIRS:
        assert (_BACKEND / d).is_dir(), f"受管目录不存在：{d}"


# ──────────────── 探测器自测（门禁自己不许静默失效）────────────────

def _detects(src: str) -> bool:
    return any(_is_naive_time_call(n) for n in ast.walk(ast.parse(src)))


@pytest.mark.parametrize("src", [
    "date.today()",
    "datetime.today()",
    "datetime.now()",
    "x = datetime.now().date()",
    "datetime.combine(date.today(), time.min)",
    "day_start = datetime.combine(d, datetime.min.time())",
])
def test_detector_bites(src):
    assert _detects(src), f"这种写法本该被咬住：{src}"


@pytest.mark.parametrize("src", [
    "datetime.now(tz=UTC)",                  # 显式时区，不是本门禁要防的
    "Column(DateTime, server_default=func.now())",   # SQL 函数，是反向问题（隐式 UTC）
    "datetime.now(timezone.utc)",
    "market_today(CRYPTO)",                  # 真源接口
    "utc_now()",
    "market_day_bounds(A_SHARE, day)",
    "time.time()",                           # epoch float，与时区无关
    "df.now",                                # 属性访问不是调用
    "EnsemblePredictor.combine(a, b)",       # combine 是个常见方法名，只认 datetime.combine
])
def test_detector_lets_these_through(src):
    assert not _detects(src), f"这种写法不该被咬：{src}"
