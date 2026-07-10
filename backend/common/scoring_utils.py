"""打分归一化原语 —— 全项目唯一实现，勿再手写 `max(lo, min(hi, v))`。

`cockpit_engine`（五维体检分）与 `limit_up_engine`（涨停候选打分）此前各写了一份
逐字符相同的 `_clamp`；`_lerp` 只有涨停引擎有，但它是通用的「把某个原始指标映射
到 0-100 分」原语，其它引擎迟早要用。
"""


def clamp(v: float, lo: float, hi: float) -> float:
    """把 v 夹到闭区间 [lo, hi]。

    约定 lo <= hi（调用方保证；lo > hi 行为未定义）。不处理 None。
    """
    return max(lo, min(hi, v))


def lerp(x: float | None, x0: float, x1: float, y0: float, y1: float) -> float:
    """把落在 [x0, x1] 的 x 线性映射到 [y0, y1]，越界经 clamp 夹紧。

    两个刻意的兜底（不是疏漏，改前先想清楚）：
      - `x is None` → 返回中点 `(y0 + y1) / 2`。缺值中性化：既不奖励也不惩罚。
      - `x1 == x0` → 返回 `y0`。除零保护。
    """
    if x is None:
        return (y0 + y1) / 2
    if x1 == x0:
        return y0
    t = clamp((x - x0) / (x1 - x0), 0.0, 1.0)
    return y0 + t * (y1 - y0)
