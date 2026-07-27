"""门禁：**总仓位上限 80%（= 留 20% 现金）是独立硬底线，谁都抬不动它**。

# 为什么要有这张门禁（S0 §1.3）

2026-07-27 定位变更把「单笔交易金额」放开了（单股上限取消），但**同时特意叮嘱
「别记错这两条的组合」——放开的是「单笔能下多大」，没放开的是「总共能压多少」**。

而写卡时核实发现，现有实现恰恰做不到这个组合：

    # trading_engine/risk/adapter.py（旧）
    cfg["max_total_position_pct"] = max(cfg.get("max_total_position_pct", 0.8), pct)
    #                                                                        ↑ 单股上限

Jason 按新规则去设置页把 `max_position_pct` 调到 1.0 → 总仓位上限**自动变成 1.0**
→ 20% 现金保护被静默撤销，且不会有任何报错。`position_sizing._get_risk_manager`
里还有一份同款拷贝。

这张门禁钉死的不变式只有一条，但它值一整个文件：

    **无论单股上限被设成多少，`max_total_position_pct` 恒等于 0.80。**

⚠️ 方向别再反过来。单股上限受总仓位上限 `min` 约束是对的；拿单股上限去 `max`
总仓位上限是错的。
"""
import ast
import os

import pytest

from data_engine.storage.database import get_session
from data_engine.storage.models import UserSettings
from trading_engine.config import RISK_CONFIG

HARD_FLOOR = 0.80
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_setting(key: str):
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == key).first()
        return row.value if row else None
    finally:
        session.close()


def _write_setting(key: str, value) -> None:
    """value=None → 删除该行（用来测「压根没设置过」的默认路径）。"""
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == key).first()
        if value is None:
            if row is not None:
                session.delete(row)
        elif row is None:
            session.add(UserSettings(key=key, value=value))
        else:
            row.value = value
        session.commit()
    finally:
        session.close()


@pytest.fixture
def set_setting():
    """改 UserSettings，测完**恢复原值**（不是一删了事）。

    ⚠️ `api/routes/data.py::_init_default_settings()` 在 import 期就会 seed
    `max_position_pct=0.2`；直接 delete 会把 seed 的行也带走，测试顺序一变就串味。
    """
    saved: dict[str, object] = {}

    def _set(key: str, value) -> None:
        if key not in saved:
            saved[key] = _read_setting(key)
        _write_setting(key, value)

    yield _set

    for key, old in saved.items():
        _write_setting(key, old)


@pytest.fixture
def set_max_position_pct(set_setting):
    return lambda value: set_setting("max_position_pct", value)


def _fresh_adapter():
    """adapter 无模块级缓存，直接 import 即可；单列一函数是为了让意图显眼。"""
    from trading_engine.risk import adapter
    return adapter


# ── 1. 硬底线本身 ────────────────────────────────────────────────────────

def test_risk_config_total_cap_is_80pct():
    """RISK_CONFIG 里那个 0.80 是四条保留硬风控之一，改它要先改 CLAUDE.md。"""
    assert RISK_CONFIG["max_total_position_pct"] == HARD_FLOOR


def test_total_cap_does_not_read_user_settings(set_max_position_pct):
    """总仓位上限**不读 UserSettings** —— 它不是 Jason 在设置页能调软的东西。"""
    adapter = _fresh_adapter()
    set_max_position_pct("1.0")
    assert adapter.get_max_total_position_pct() == HARD_FLOOR


# ── 2. 单股上限抬不动总仓位上限（本卡的主症状）────────────────────────────

@pytest.mark.parametrize("pct", ["0.2", "0.5", "0.9", "1.0"])
def test_single_position_cap_never_lifts_total_cap(set_max_position_pct, pct):
    adapter = _fresh_adapter()
    set_max_position_pct(pct)
    cfg = adapter.get_effective_risk_config()
    assert cfg["max_total_position_pct"] == HARD_FLOOR, (
        f"单股上限设成 {pct} 就把总仓位上限顶到 "
        f"{cfg['max_total_position_pct']} —— 20% 现金保护被静默撤销了"
    )


def test_single_position_cap_is_clamped_by_total_cap(set_max_position_pct):
    """反方向：单股上限受总仓位上限约束。设 1.0 = 取消单股约束，但买满也只到 80%。"""
    adapter = _fresh_adapter()
    set_max_position_pct("1.0")
    assert adapter.get_max_position_pct() == HARD_FLOOR
    assert adapter.get_effective_risk_config()["max_position_pct"] == HARD_FLOOR


def test_explicit_setting_below_ceiling_is_respected(set_max_position_pct):
    """夹取只在超过底线时生效，别把 Jason 显式设的 0.5 也改掉。"""
    adapter = _fresh_adapter()
    set_max_position_pct("0.5")
    assert adapter.get_max_position_pct() == 0.5


def test_default_when_no_setting_row(set_max_position_pct):
    """压根没设置过 → 取最严值 0.20（「兜底取最严」这句话得有测试撑）。"""
    adapter = _fresh_adapter()
    set_max_position_pct(None)
    assert adapter.get_max_position_pct() == 0.20


@pytest.mark.parametrize("raw,want", [
    ("5", HARD_FLOOR),      # 手滑写成 500%
    ("0", 0.20),            # 0 不合法 → 兜底
    ("-1", 0.20),           # 负数不合法 → 兜底
    ("abc", 0.20),          # 非数字 → 兜底（走 except）
    ("", 0.20),             # 空串（falsy，走「没设置」分支）
])
def test_garbage_settings_never_exceed_the_floor(set_max_position_pct, raw, want):
    """脏值不能变成放宽风控的后门 —— 这个 key 从 API 层没有数值校验。"""
    adapter = _fresh_adapter()
    set_max_position_pct(raw)
    assert adapter.get_max_position_pct() == want


# ── 3. position_sizing 里那份拷贝（同款病，别只修一处）─────────────────────

def test_position_sizing_risk_manager_keeps_total_cap():
    from trading_engine import position_sizing
    from trading_engine.risk.rules import (
        MaxPositionPercentRule,
        MaxTotalPositionPercentRule,
    )

    position_sizing._risk_managers.clear()
    try:
        rm = position_sizing._get_risk_manager(1.0)   # 调用方喊「单股不限」
        totals = [r for r in rm.rules if isinstance(r, MaxTotalPositionPercentRule)]
        singles = [r for r in rm.rules if isinstance(r, MaxPositionPercentRule)]
        assert totals and totals[0].max_percent == HARD_FLOOR
        assert singles and singles[0].max_percent == HARD_FLOOR
    finally:
        position_sizing._risk_managers.clear()


# ── 3b. crypto 侧那份拷贝（第三份，审查时才挖出来）─────────────────────────

def test_crypto_sizing_keeps_total_cap(set_setting):
    """`size_crypto_position` 收 DSL 传下来的 `max_position_pct`（可到 1.0）。

    不夹的话：建议量按 100% 资金算出来 + `risk_passed=True` 盖章，而这个 True 会
    进 DecisionLog、也会摆到 Jason 眼前。
    """
    from crypto_intel_engine.cockpit import size_crypto_position

    set_setting("total_capital", "100000")
    broker_info = {"cash": 100_000.0, "market_value": 0.0, "total_value": 100_000.0,
                   "unrealized_pnl": 0.0, "positions": {}, "recent_closed_pnls": [],
                   "last_loss_date": None}
    r = size_crypto_position("BTCUSDT.BN", 50_000.0, target_pct=100.0,
                             broker_info=broker_info, total_capital=100_000.0,
                             max_position_pct=1.0, step_size=0.00001,
                             min_qty=0.0, min_notional=0.0)
    assert r["max_single_amount"] == 80_000.0, "单标上限没被总仓位底线夹住"
    assert r["amount_usdt"] <= 80_000.0
    assert r["risk_passed"] is True, f"夹完还过不了风控？{r['warnings']}"


# ── 3c. 结构性防复发：全项目不许再出现「拿别的值去 max 总仓位上限」───────────

def test_no_one_lifts_the_total_position_cap():
    """AST 扫全项目：任何 `X["max_total_position_pct"] = max(...)` 都判红。

    这条比上面三条逐点断言更重要 —— 前两份拷贝是照着第一份抄的，第三份是
    审查时才挖出来的。**同一个模式复制三次，说明逐点修根本兜不住**，
    得有一条能自动抓到第四份的门禁（`docs/CODING_STANDARDS.md` §10 的老规矩）。

    合法写法：`= 常量 / get_max_total_position_pct() / cfg[...]`（照抄，不抬高）。
    """
    offenders = []
    for root, _dirs, files in os.walk(_BACKEND):
        if any(p in root for p in (os.sep + "tests", os.sep + "__pycache__", os.sep + ".")):
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            src = open(path, encoding="utf-8").read()
            if "max_total_position_pct" not in src:
                continue
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, ast.Assign):
                    continue
                targets_it = any(
                    isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                    and t.slice.value == "max_total_position_pct"
                    for t in node.targets
                )
                if not targets_it:
                    continue
                v = node.value
                if isinstance(v, ast.Call) and getattr(v.func, "id", None) == "max":
                    offenders.append(
                        f"{os.path.relpath(path, _BACKEND)}:{node.lineno}")
    assert not offenders, (
        f"这些地方在拿别的值抬高总仓位上限：{offenders}。"
        f"总仓位 80%（= 留 20% 现金）是**独立硬底线**，方向应该反过来 —— "
        f"单股上限受它 min 约束。这个模式在项目里已经被复制过三次了，别再有第四次。"
    )


# ── 4. 端到端：一笔吃掉全部资金的买单必须被拦 ─────────────────────────────

def test_all_in_order_is_rejected_end_to_end(set_setting):
    """最终判据不是配置长什么样，而是**一笔 all-in 真的下不去**。"""
    from trading_engine.risk.manager import RiskManager

    adapter = _fresh_adapter()
    set_setting("max_position_pct", "1.0")
    # ⚠️ 必须把总资金也对齐：`RiskManager` 会按 `get_total_capital() × 3%` 建
    # 单日亏损规则，测试库默认 5000 → 阈值 150，而下面 broker_info 写的是 10 万。
    # 不对齐的话，将来谁给 unrealized_pnl 填个非零值，80% 那条断言就会因为
    # **跟本门禁无关的原因**变红。
    set_setting("total_capital", "100000")
    rm = RiskManager(adapter.get_effective_risk_config())

    broker_info = {
        "cash": 100_000.0,
        "market_value": 0.0,
        "total_value": 100_000.0,
        "unrealized_pnl": 0.0,
        "positions": {},
        "recent_closed_pnls": [],
        "last_loss_date": None,
    }
    # 100% 仓位 → 必须被拦
    passed, results = rm.check_order("600519.SH", "BUY", 1000, 100.0, broker_info)
    assert passed is False
    assert any("总仓位" in r.message for r in results if not r.passed)

    # 80% 仓位 → 恰好在线上，放行（严格 `>` 才拦，边界不误杀）
    passed_ok, _ = rm.check_order("600519.SH", "BUY", 800, 100.0, broker_info)
    assert passed_ok is True
