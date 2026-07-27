"""`common/outcome_eval.py` 的纯规则测试 —— 零 fixture、零 mock、零 DB。

内核之所以能这么测，是因为它是纯的：入参走 Protocol、唯一的时间概念是显式的
`age_days` 而不是 `date.today()`。这是把它放 common/ 的全部回报。

注释是资产：每条断言都写清**它在防什么**。
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.outcome_eval import (  # noqa: E402
    ENGINE_VERSION,
    MAX_WINDOW,
    compute_summary,
    evaluate_single,
    is_retryable,
    normalize_action,
)


def _bar(high, low, close):
    return SimpleNamespace(high=high, low=low, close=close)


def _advice(action="BUY", entry_price=100.0, stop_loss=None, take_profit=None):
    return SimpleNamespace(
        action=action, entry_price=entry_price,
        stop_loss=stop_loss, take_profit=take_profit,
    )


def _flat_bars(n, close=100.0):
    """n 根什么都不碰的平盘 bar。"""
    return [_bar(close, close, close) for _ in range(n)]


# ---------- unable：判定顺序 ----------

def test_no_action_wins_over_no_entry_price():
    """action 和 entry_price 同时缺 → 必须报 no_action。

    顺序即优先级：「连方向都没记」是更根本的原因；报 no_entry_price 会误导人
    去补价格，补完还是评不了。advisor 现在就是这个格子（它只传 output_text）。
    """
    r = evaluate_single(_advice(action=None, entry_price=None), _flat_bars(20))
    assert r.outcome_status == "unable"
    assert r.unable_reason == "no_action"


def test_hold_is_not_directional_not_a_miss():
    """HOLD / AGGREGATE / cockpit 的 "N/A" 都没有可证伪的方向断言。

    这是 unable 不是 loss —— 把「没法评」算成「判错」正是这张卡要防的事。
    """
    for action in ("HOLD", "AGGREGATE", "N/A"):
        r = evaluate_single(_advice(action=action), _flat_bars(20))
        assert r.outcome_status == "unable", action
        assert r.unable_reason == "action_not_directional", action


def test_missing_and_invalid_entry_price():
    assert evaluate_single(_advice(entry_price=None), _flat_bars(20)).unable_reason == "no_entry_price"
    assert evaluate_single(_advice(entry_price=0), _flat_bars(20)).unable_reason == "invalid_entry_price"
    assert evaluate_single(_advice(entry_price=-5), _flat_bars(20)).unable_reason == "invalid_entry_price"


def test_no_quotes_and_insufficient_bars_are_retryable():
    """停牌/新股/行情还没回补 → 明天补了就能评。这是「可重试」二分存在的理由。"""
    r = evaluate_single(_advice(), [])
    assert r.unable_reason == "no_quotes" and is_retryable(r.unable_reason)

    r = evaluate_single(_advice(), _flat_bars(3))
    assert r.unable_reason == "insufficient_bars" and is_retryable(r.unable_reason)


def test_stale_downgrades_retryable_to_permanent():
    """三个月都没补上的行情，明天也不会有（退市/长期停牌）。

    不降级的话它会被每天扫一遍，永远扫不出结果。
    """
    r = evaluate_single(_advice(), [], age_days=200)
    assert r.unable_reason == "stale_no_data"
    assert not is_retryable(r.unable_reason)

    r = evaluate_single(_advice(), _flat_bars(3), age_days=200)
    assert r.unable_reason == "stale_no_data"


def test_retryable_classification():
    """可重试性是 unable_reason 的函数，不是独立列。

    ⚠️ no_entry_price 在我们这儿**不可重试**（与外部蓝本刻意分歧）：entry_price 是
    决策产生时写死的存量字段且被 _IMMUTABLE_REFRESH_FIELDS 冻结，NULL 就永远是 NULL。
    """
    assert is_retryable("no_quotes")
    assert is_retryable("insufficient_bars")
    assert not is_retryable("no_action")
    assert not is_retryable("action_not_directional")
    assert not is_retryable("no_entry_price")
    assert not is_retryable("invalid_entry_price")
    assert not is_retryable("stale_no_data")
    # entry_price_outlier 同理：entry_price 冻结 → 离谱就永远离谱，重试一万次一样。
    assert not is_retryable("entry_price_outlier")
    assert not is_retryable(None)


# ---------- 离谱入场价守卫（P0-4 批次2 的防复发线）----------

def test_the_bomb_entry_100_btc_is_not_evaluated():
    """⭐ **那颗炸弹的固定用例**：`BTCUSDT.BN BUY entry=100.0` 撞上 6.5 万的 bar。

    库里曾躺着 39 行这个（测试写进生产库的假成交）。只要攒够 bar 就会被评成
    `(65000-100)/100 ≈ +64900%` 的 **win** → 胜率顶到 100% → P0-3 的校准
    （只下调不上抬）永久失效**且不报错**。批次1 已把那批脏行删了，这道守卫防的是
    **下一个**来源。
    """
    bars = _flat_bars(20, close=65000.0)
    r = evaluate_single(_advice(action="BUY", entry_price=100.0), bars)
    assert r.outcome_status == "unable"
    assert r.unable_reason == "entry_price_outlier"
    assert not is_retryable(r.unable_reason)
    # 收益率一个都不许算出来 —— 守卫的意义就是让这条行进不了任何统计。
    assert r.return_5d is None and r.return_20d is None
    assert r.outcome_5d is None and r.outcome_20d is None


def test_the_bomb_control_group_would_have_been_a_win():
    """**对照组** —— 防这条测试哪天测了空气。

    同一批 bar、同一个方向，只把 entry 换成合理值 → 正常评出来。若哪天守卫写错成
    「什么都拦」，上面那条测试照样绿，只有这条会红。
    """
    bars = _flat_bars(20, close=65000.0)
    r = evaluate_single(_advice(action="BUY", entry_price=65000.0), bars)
    assert r.outcome_status == "completed"
    assert r.unable_reason is None

    # 再证一次「若当成可评的就是一条 +64900% 的 win」：把守卫的阈值换算掉之后，
    # 同样的数据用 entry=6500（差 10 倍整，不触发严格 `>`）确实产出巨额正收益。
    r2 = evaluate_single(_advice(action="BUY", entry_price=6500.0), bars)
    assert r2.outcome_status == "completed"
    assert r2.outcome_20d == "win" and r2.return_20d == 900.0


def test_outlier_guard_is_bidirectional():
    """`entry=100 / ref=65000`（少写几个零）和 `entry=65000 / ref=100`（多写几个零、
    或把金额传进了 price 参数）是同一类错误，两个方向都得抓。"""
    assert evaluate_single(_advice(entry_price=1.0), _flat_bars(20, close=1000.0)
                           ).unable_reason == "entry_price_outlier"
    assert evaluate_single(_advice(entry_price=1000.0), _flat_bars(20, close=1.0)
                           ).unable_reason == "entry_price_outlier"


def test_outlier_boundary_is_exclusive():
    """恰好 10 倍**放行**，10 倍多一点点才拦。

    严格 `>` 是刻意的：10:1 拆股会让复权价与原始 entry 恰好差 10 倍，那种情况不该
    被当成脏数据误杀。
    """
    on_edge = evaluate_single(_advice(entry_price=100.0), _flat_bars(20, close=1000.0))
    assert on_edge.unable_reason != "entry_price_outlier"
    assert on_edge.outcome_status == "completed"

    over = evaluate_single(_advice(entry_price=100.0), _flat_bars(20, close=1000.01))
    assert over.unable_reason == "entry_price_outlier"


def test_outlier_guard_fires_before_insufficient_bars():
    """**判定顺序**：1 根 bar 就判得出离谱，不必等攒够 5 根。

    放在 MIN_BARS 之后的话，这条行会先报 insufficient_bars（**可重试**）→ 每天被
    回填重扫一遍，白等到第 5 根 bar 才拦得住。放前面 = 见到第一根就终结、当场退出
    候选集。
    """
    r = evaluate_single(_advice(entry_price=100.0), _flat_bars(1, close=65000.0))
    assert r.unable_reason == "entry_price_outlier"
    assert not is_retryable(r.unable_reason)


def test_no_bars_still_reports_no_quotes_not_outlier():
    """0 根 bar → 没有参考物，此时说「离谱」是**猜**。

    诚实地留在可重试的 no_quotes 里，等 bar 来了自然翻成 entry_price_outlier。
    """
    r = evaluate_single(_advice(entry_price=100.0), [])
    assert r.unable_reason == "no_quotes" and is_retryable(r.unable_reason)


def test_guard_skips_when_reference_price_unusable():
    """参考价取不到就**跳过守卫**，不误杀。

    宁可放过一条离谱价（它顶多是一条噪声），也不能拿空气当参考物把真建议判成脏数据
    —— 后者是**看不见**的数据损失。close 缺 → 退 (high+low)/2；再缺 → 放行。
    """
    # close 缺、high/low 在 → 用中值 (65000+65000)/2 仍判得出离谱
    r = evaluate_single(_advice(entry_price=100.0), [_bar(65000.0, 65000.0, None)] * 20)
    assert r.unable_reason == "entry_price_outlier"

    # 三个价全缺 → 守卫放行，走正常流程（收益率自然是 None）
    r = evaluate_single(_advice(entry_price=100.0), [_bar(None, None, None)] * 20)
    assert r.unable_reason is None
    assert r.outcome_status == "completed"


# ---------- action 归一（P0-4 批次2）----------

def test_normalize_action_is_the_single_ruler():
    """`buy` / ` Buy ` → `BUY`；空串与纯空白 → None（不是 `""`）。

    库里曾同时存着 `BUY`(69) 和 `buy`(6)，而消费方全是**精确匹配**
    （`report_engine/picks_log.py` 的 `action == "BUY"`、`query_decisions` 的等值
    过滤）—— 小写行在它们眼里根本不存在。写入期与评估期共用这一个函数，两边才不会
    各归一各的。
    """
    assert normalize_action("buy") == "BUY"
    assert normalize_action("  Buy  ") == "BUY"
    assert normalize_action("BUY") == "BUY"
    assert normalize_action("") is None
    assert normalize_action("   ") is None
    assert normalize_action(None) is None


def test_lowercase_action_still_evaluates():
    """评估侧对历史脏行的**容错不许撤**：库里存量的 `buy` 照样评得出方向。

    写入期归一只管新行；把评估期的 upper() 拿掉，存量小写行会全部掉进 no_action。
    """
    r = evaluate_single(_advice(action="buy"), _flat_bars(20, close=110.0))
    assert r.outcome_status == "completed"
    assert r.outcome_20d == "win"


# ---------- 状态机 ----------

def test_pending_when_window_not_full():
    """5 <= n < 20：5 日窗口已经评得出来，20 日的必须显式 None（不是跳过）。"""
    bars = [_bar(110, 105, 108)] * 10
    r = evaluate_single(_advice(), bars)
    assert r.outcome_status == "pending"
    assert r.return_5d is not None and r.outcome_5d == "win"
    assert r.return_20d is None and r.outcome_20d is None


def test_completed_at_full_window():
    r = evaluate_single(_advice(), _flat_bars(MAX_WINDOW))
    assert r.outcome_status == "completed"
    assert r.engine_version == ENGINE_VERSION


# ---------- 方向与中性带 ----------

def test_buy_direction():
    bars = [_bar(120, 100, 110)] * 20      # 涨 10%
    r = evaluate_single(_advice("BUY", 100.0), bars)
    assert r.return_20d == 10.0 and r.outcome_20d == "win"


def test_sell_sign_is_normalized_do_not_fix_this():
    """⚠️⚠️ 说卖、后来真跌了 → **正收益 → win**。这不是 bug，别「修」。

    符号按方向归一之后，win_rate / avg_return 才能跨 BUY/SELL 混算。若有人把
    SELL 的收益率改成「跌了就是负数」，胜率统计会把每一条判对的 SELL 记成 loss。
    公式与 signal_tracker.py:146-152 逐字一致。
    """
    bars = [_bar(95, 85, 90)] * 20         # 从 100 跌到 90
    r = evaluate_single(_advice("SELL", 100.0), bars)
    assert r.return_20d == 10.0, "SELL 说跌、真跌了 10% → 收益率 +10"
    assert r.outcome_20d == "win"


def test_sell_that_went_up_is_a_loss():
    bars = [_bar(115, 105, 110)] * 20
    r = evaluate_single(_advice("SELL", 100.0), bars)
    assert r.return_20d == -10.0 and r.outcome_20d == "loss"


def test_neutral_band_boundary_is_exclusive():
    """恰好 +1.0% → neutral，不是 win。闭区间在哪必须钉死。

    中性带在评估期定死、由 ENGINE_VERSION 背书 —— 挪到查询期动态算会让历史
    结果随带宽调整悄悄漂移。
    """
    bars = [_bar(101, 101, 101)] * 20      # 正好 +1%
    assert evaluate_single(_advice("BUY", 100.0), bars).outcome_20d == "neutral"

    bars = [_bar(99, 99, 99)] * 20         # 正好 -1%
    assert evaluate_single(_advice("BUY", 100.0), bars).outcome_20d == "neutral"

    bars = [_bar(101.5, 101.5, 101.5)] * 20
    assert evaluate_single(_advice("BUY", 100.0), bars).outcome_20d == "win"


# ---------- first_hit 四态 ----------

def test_first_hit_none_when_nothing_touched():
    """有价位、扫过了、没碰到 → "none"（与 None="没价位可判" 语义区分）。"""
    r = evaluate_single(_advice(stop_loss=80.0, take_profit=120.0), _flat_bars(20))
    assert r.first_hit == "none"
    assert r.first_hit_days is None
    assert r.hit_stop == 0 and r.hit_target == 0


def test_first_hit_stop_loss():
    bars = _flat_bars(20)
    bars[2] = _bar(100, 79, 85)            # 第 3 根跌破止损 80
    r = evaluate_single(_advice(stop_loss=80.0, take_profit=120.0), bars)
    assert r.first_hit == "stop_loss"
    assert r.first_hit_days == 3
    assert r.hit_stop == 1 and r.hit_target == 0


def test_first_hit_take_profit():
    bars = _flat_bars(20)
    bars[4] = _bar(121, 100, 118)
    r = evaluate_single(_advice(stop_loss=80.0, take_profit=120.0), bars)
    assert r.first_hit == "take_profit"
    assert r.first_hit_days == 5
    assert r.hit_target == 1 and r.hit_stop == 0


def test_first_hit_ambiguous_never_guesses():
    """同一根日线里止损止盈都被触及 → 日线数据**无法**判断先后，不许猜。

    保守口径（按先止损算）不落在这儿 —— 落在 compute_summary 的 stop_first_rate。
    存的是观测事实，算的才是口径；口径变了 bump ENGINE_VERSION 即可，不用改数据。
    """
    bars = _flat_bars(20)
    bars[1] = _bar(125, 75, 100)           # 一根大阴大阳，两个位都扫到了
    r = evaluate_single(_advice(stop_loss=80.0, take_profit=120.0), bars)
    assert r.first_hit == "ambiguous"
    assert r.first_hit_days == 2
    assert r.hit_stop == 1 and r.hit_target == 1


def test_breaks_on_first_hit_does_not_scan_whole_window():
    """止损出局后你已经空仓 —— 后面价格再碰到止盈位跟你没关系。

    记全窗口会让 hit_target_rate 虚高：把「被扫出局之后的止盈」也算成打中目标，
    读起来像赚了其实没赚。照外部蓝本 backtest_engine.py:736-767 的 break。
    """
    bars = _flat_bars(20)
    bars[2] = _bar(100, 79, 85)            # 第 3 根止损
    bars[6] = _bar(121, 100, 119)          # 第 7 根价格又碰到止盈位
    r = evaluate_single(_advice(stop_loss=80.0, take_profit=120.0), bars)
    assert r.first_hit == "stop_loss"
    assert r.hit_target == 0, "止损出局之后的止盈不算数"


def test_sell_direction_hits_are_mirrored():
    """做空：价格**涨**到止损位、**跌**到止盈位。"""
    bars = _flat_bars(20)
    bars[1] = _bar(121, 100, 118)          # 涨到 121 → 触发做空的止损 120
    r = evaluate_single(_advice("SELL", 100.0, stop_loss=120.0, take_profit=80.0), bars)
    assert r.first_hit == "stop_loss" and r.first_hit_days == 2


# ---------- 止损/止盈缺失 → 仍 completed ----------

def test_stop_only_still_completed_this_is_recommend_engines_shape():
    """只有止损没有止盈 —— **这就是 moneybill_recommend 的形状**（它压根没 take_profit 键）。

    completed 的门槛是「方向可评」不是「止损止盈齐全」。照卡片字面判 unable 会让
    主力 source 100% unable，验收标准 1 当场死。
    """
    r = evaluate_single(_advice(stop_loss=80.0), _flat_bars(20))
    assert r.outcome_status == "completed"
    assert r.first_hit == "none"
    assert r.hit_stop == 0
    assert r.hit_target is None, "没止盈位 → None（无从判起），不是 0（扫过没碰到）"


def test_no_levels_at_all_still_completed():
    """两个价位都没有 → 方向照样评得出来，first_hit 整个是 None。"""
    r = evaluate_single(_advice(), _flat_bars(20))
    assert r.outcome_status == "completed"
    assert r.first_hit is None
    assert r.hit_stop is None and r.hit_target is None


# ---------- compute_summary ----------

def test_win_rate_denominator_excludes_unable():
    """整张卡的题眼：100 条建议 6 对 2 错 2 条没法评。

    分母算 total → 60%（自己冤枉自己）；分母算 evaluated → **75%** 才是真的。
    卡片原文：「差 15 个点全是自己冤枉自己」。
    """
    up = [_bar(120, 100, 110)] * 20
    down = [_bar(100, 80, 90)] * 20
    results = (
        [evaluate_single(_advice("BUY", 100.0), up) for _ in range(6)]      # 6 对
        + [evaluate_single(_advice("BUY", 100.0), down) for _ in range(2)]  # 2 错
        + [evaluate_single(_advice(action=None), up) for _ in range(2)]     # 2 条没法评
    )
    s = compute_summary(results)
    assert s["total"] == 10
    assert s["evaluated"] == 8
    assert s["unable"] == 2
    assert s["win"] == 6 and s["loss"] == 2
    assert s["win_rate"] == 75.0, "不是 60.0 —— unable 必须从分母剔除"
    assert s["unable_breakdown"] == {"no_action": 2}


def test_win_rate_is_none_not_zero_when_nothing_evaluable():
    """一条都没法评 → None，**不是 0.0**。

    0.0 会被 LLM 读成「这个 source 烂透了」，真相是「一条都没法评」。advisor 现在
    就是这个格子 —— 返 0.0 等于让 MoneyBill 当着 Jason 的面说「advisor 胜率 0%」。
    """
    results = [evaluate_single(_advice(action=None), _flat_bars(20)) for _ in range(12)]
    s = compute_summary(results)
    assert s["evaluated"] == 0
    assert s["win_rate"] is None
    assert s["avg_return"] is None
    assert s["unable"] == 12


def test_stop_first_rate_counts_ambiguous_as_stop():
    """**保守假设先止损**的落地点：ambiguous 进 stop_first_rate 的分子。

    分母是 stop+target+ambiguous（不含 none —— 那些根本没决出胜负）。
    """
    bars_stop = _flat_bars(20)
    bars_stop[2] = _bar(100, 79, 85)
    bars_amb = _flat_bars(20)
    bars_amb[1] = _bar(125, 75, 100)
    bars_tp = _flat_bars(20)
    bars_tp[4] = _bar(121, 100, 118)
    adv = {"stop_loss": 80.0, "take_profit": 120.0}
    results = [
        evaluate_single(_advice(**adv), bars_stop),
        evaluate_single(_advice(**adv), bars_amb),
        evaluate_single(_advice(**adv), bars_tp),
        evaluate_single(_advice(**adv), _flat_bars(20)),   # none：不进分母
    ]
    s = compute_summary(results)
    assert s["first_hit"] == {"stop_loss": 1, "take_profit": 1, "ambiguous": 1, "none": 1}
    # (1 stop + 1 ambiguous) / 3 decided
    assert s["stop_first_rate"] == 66.67


def test_summary_reports_actual_engine_versions_not_a_constant():
    """混版本必须可见：返回的是数据里实际 distinct 的版本集合，不是硬编码常量。

    bump ENGINE_VERSION 后老 completed 行不重算，库里会混着两版；跨版本混算胜率
    正是设计点 2 想防的漂移。本卡不做重算，但至少让它可见。
    """
    s = compute_summary([evaluate_single(_advice(), _flat_bars(20))])
    assert s["engine_version"] == [ENGINE_VERSION]


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"✓ {_name}")
    print("all passed")
