"""13.4-1 行为基线：涨停判定全仓一条口径（板块 10/20），ST 的 5% 阈值不启用。

## 为什么

`common/limit_rules.py` 支持 ST 的 4.9% 阈值（传 `name` 即可），但**没人该传**：

- 本库 `RealtimeSnapshot` 里名字带 ST 的**主板**股 153 只，其中 62 只（40.5%）
  涨跌幅超过 ±5%，最深 `*ST春兴 -10.16%`。近 39 个交易日 2418 个日观测里
  411 个（17%）越过 ±5%，峰值 10.26%，并反复撞 10% 板。若真受 5% 限价，
  这在数学上不可能。
- 同批数据里主板非ST(3047只) 0 越界 10%、创业板(1392只) 0 越界 20%、
  科创板(604只) 仅 1 只边界值 → **板块阈值是对的，只有靠 name 判 ST 这一支不可靠**。
- 全仓没有任何 `is_st` 权威列；`StockInfo.name`（停在 2026-02-05）与
  `RealtimeSnapshot.name` 对约一半 ST 名互相打架（153 vs 75）。

`realtime.compute_statistics` 与 `report_engine` 已经不传 name（被
`tests/baseline/test_realtime_parse_baseline.py` 钉住）。`limit_up_engine` 曾是
唯一的偏离者——它把那 62 只行为像 10% 股的票在 +5%~9.9% 时**误当已封板踢出候选**。

根治要给 `StockInfo` 加 `is_st` 权威列（从 akshare `stock_zh_a_st_em` 刷新），
需动 DB schema，属第 14 步。
"""
import inspect

import pytest

from common.limit_rules import is_limit_up
from limit_up_engine import candidate_pool, metrics, scoring

pytestmark = pytest.mark.baseline

# 主板 ST 名义股，涨 5.5%：ST 规则说已封板(≥4.9)，板块规则说还差得远(<9.9)
ST_MAIN = "600251.SH"
ST_NAME = "*ST冠农"


# ── 规则本身仍支持 ST（别把 is_st 删了）─────────────────────────────────────

def test_the_rule_itself_still_supports_st():
    """common.limit_rules 保留 ST 能力——只是全仓没人传 name。

    等 StockInfo.is_st 权威列落地（第 14 步），传 name 就能一键启用。
    """
    assert is_limit_up(ST_MAIN, 4.95, ST_NAME) is True
    assert is_limit_up(ST_MAIN, 4.95) is False


# ── limit_up_engine 的 8 个传参点：一个都不许传 name ────────────────────────

@pytest.mark.parametrize("module", [candidate_pool, scoring, metrics])
def test_no_module_passes_name_to_limit_rules(module):
    """漏一处就是引擎内 10%/5% 自相矛盾，比全传 name 更糟。"""
    src = inspect.getsource(module)
    for bad in ["is_limit_up(symbol, row.get(\"change_pct\"), name)",
                "is_limit_up(row.symbol, row.change_pct, row.name)",
                "is_limit_up(symbol, change_pct, name)",
                "is_limit_up(symbol, chg, name)",
                "get_limit_threshold(symbol, name)",
                "get_limit_threshold(candidate[\"symbol\"], candidate.get(\"name\"))"]:
        assert bad not in src, f"{module.__name__} 还在传 name: {bad}"


def test_supported_board_check_is_unaffected_by_name():
    """`get_limit_threshold(...) is not None` —— 4.9 与 9.9 都非 None，去 name 是 no-op。

    北交所/未知板块仍然返回 None（不覆盖）。
    """
    assert candidate_pool._is_supported_board("600519.SH") is True
    assert candidate_pool._is_supported_board("300750.SZ") is True
    assert candidate_pool._is_supported_board("430047.BJ") is False   # 北交所
    assert candidate_pool._is_supported_board("510300.SH") is False   # ETF


# ── 行为：ST 名义股不再被误判封板 ──────────────────────────────────────────

def test_st_named_main_board_at_5_5_pct_is_not_sealed():
    """候选池的语义是「排除已封板的票」（候选 = 能买进）。

    +5.5% 的主板股离 10% 板还远得很，该进候选池。旧口径按 4.9% 判它已封板，
    把它踢了出去。
    """
    assert is_limit_up(ST_MAIN, 5.5) is False


def test_approach_ratio_uses_board_threshold_not_st():
    """`ratio = change_pct / threshold`。分母 4.9 → 9.9，比例减半。

    旧口径下 +5.5% 的 ST 名义股 ratio=1.12（>1，荒谬），新口径 0.556。
    """
    candidate = {"symbol": ST_MAIN, "name": ST_NAME, "change_pct": 5.5,
                 "limit_threshold": None, "approach_ratio": None}
    ratio = scoring._approach_ratio(candidate)
    assert ratio == pytest.approx(5.5 / 9.9, rel=1e-6)
    assert ratio < 1.0


@pytest.mark.parametrize("symbol,pct,expected", [
    ("600519.SH", 9.95, True),     # 主板 10%
    ("600519.SH", 5.5, False),
    ("300750.SZ", 15.0, False),    # 创业板 20%：15% 只是普通大涨
    ("300750.SZ", 19.95, True),
    ("688371.SH", 19.95, True),    # 科创板 20%
    ("302132.SZ", 19.95, True),    # 后开的创业板段
    ("689009.SH", 19.95, True),    # 后开的科创板段
    ("430047.BJ", 29.9, False),    # 北交所不覆盖
])
def test_board_thresholds(symbol, pct, expected):
    assert is_limit_up(symbol, pct) is expected


# ── 两链一致性回归钉（防止未来再分叉）──────────────────────────────────────

def test_limit_up_count_matches_compute_statistics():
    """同一组行情，市场宽度统计与涨停引擎必须数出同样多的涨停。

    这两条链曾经不一致：compute_statistics 不传 name（10%），
    limit_up_engine 传 name（ST 走 4.9%）。
    """
    from data_engine.fetchers.realtime import compute_statistics

    quotes = [
        {"symbol": "600519.SH", "name": "贵州茅台", "change_pct": 9.95},   # 涨停
        {"symbol": ST_MAIN, "name": ST_NAME, "change_pct": 5.5},           # 不是涨停
        {"symbol": "300750.SZ", "name": "宁德时代", "change_pct": 15.0},   # 不是涨停
        {"symbol": "301158.SZ", "name": "德石股份", "change_pct": 19.95},  # 涨停
        {"symbol": "430047.BJ", "name": "诺思兰德", "change_pct": 29.9},   # 不覆盖
    ]
    breadth_count = compute_statistics(quotes)["limit_up"]
    engine_count = sum(
        1 for q in quotes if is_limit_up(q["symbol"], q["change_pct"])
    )
    assert breadth_count == engine_count == 2
