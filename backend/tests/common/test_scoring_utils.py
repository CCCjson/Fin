"""13.4-1 行为基线：打分归一化原语。

`clamp`/`lerp` 抽自 `limit_up_engine/scoring.py`，原本零测试覆盖。`lerp` 的两个
兜底（None 取中点、除零取 y0）是刻意的，最容易被后人「顺手优化」掉——钉住它们。
"""
import pytest

from common.scoring_utils import clamp, lerp

pytestmark = pytest.mark.baseline


# ── clamp ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v,lo,hi,expected", [
    (5, 0, 3, 3),          # 越上界
    (-1, 0, 3, 0),         # 越下界
    (1.5, 0, 3, 1.5),      # 区间内
    (0, 0, 3, 0),          # 贴下界
    (3, 0, 3, 3),          # 贴上界
    (7, 5, 5, 5),          # lo == hi
])
def test_clamp(v, lo, hi, expected):
    assert clamp(v, lo, hi) == expected


def test_clamp_does_not_special_case_none():
    """clamp 不处理 None——缺值兜底是 lerp 的职责，别把两者混在一起。"""
    with pytest.raises(TypeError):
        clamp(None, 0, 1)


# ── lerp ──────────────────────────────────────────────────────────────────

def test_lerp_maps_linearly():
    assert lerp(0.25, 0, 0.5, 0, 30) == 15.0
    assert lerp(0, 0, 1, 10, 20) == 10.0
    assert lerp(1, 0, 1, 10, 20) == 20.0


def test_lerp_clamps_out_of_range():
    assert lerp(-5, 0, 1, 10, 20) == 10.0
    assert lerp(99, 0, 1, 10, 20) == 20.0


def test_none_returns_midpoint_not_zero():
    """缺值中性化：既不奖励也不惩罚。返回 0 会让缺数据的票被系统性打压。"""
    assert lerp(None, 0, 1, 10, 20) == 15.0
    assert lerp(None, 0, 1, 0, 100) == 50.0


def test_zero_width_domain_returns_y0_not_zerodivision():
    """x1 == x0 时不能除零。"""
    assert lerp(2, 0, 0, 7, 9) == 7
    assert lerp(-99, 5, 5, 7, 9) == 7


def test_descending_range_still_works():
    """y0 > y1 是合法的（指标越大分越低），别假设单调递增。"""
    assert lerp(0.5, 0, 1, 100, 0) == 50.0
    assert lerp(1.0, 0, 1, 100, 0) == 0.0


def test_matches_the_implementation_it_replaced():
    """逐字符搬运的等价性：对照被删掉的 limit_up_engine/scoring.py 原实现。"""
    def _old_clamp(v, lo, hi):
        return max(lo, min(hi, v))

    def _old_lerp(x, x0, x1, y0, y1):
        if x is None:
            return (y0 + y1) / 2
        if x1 == x0:
            return y0
        t = _old_clamp((x - x0) / (x1 - x0), 0.0, 1.0)
        return y0 + t * (y1 - y0)

    cases = [(None, 0, 1, 10, 20), (0.3, 0, 1, 0, 100), (-2, 0, 1, 0, 100),
             (5, 0, 0, 7, 9), (0.75, 0.2, 0.8, 30, 90)]
    for c in cases:
        assert lerp(*c) == _old_lerp(*c), c
