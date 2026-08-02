"""上线前回测闸 —— live 武装的前置硬条件：净费回测必须为正。

## 保真边界（务必知情）

DSL 条件引用的是 `analyze_crypto_symbol` 的 cockpit 聚合量（composite/dim.*/资金费率/
排雷/大势/恐慌贪婪）。**价格/技术类**天然有历史序列；**资金费率/多空比/大户比/主动买卖比/
基差/排雷分/解锁占比/恐慌贪婪/稳定币供应**从 2026-07-22 起每轮 tick 落进 `crypto_metrics`，
攒够 `_MIN_HISTORY_DAYS` 天后由 `replayable_from_metrics()` 升格为「可回放」。

仍无历史的（composite/dim.* 这些聚合量、新闻情绪、4h 对齐）依旧标 degraded。

## 「可回放」是真的被回放了（不是嘴上说说）

⛔ 此前 `matured_fields` 只是把原语从 degraded 名单里**扣除**，回测却依旧只跑写死的双均线
代理——一条 DSL 条件都没被评估过，等于跟 Jason 说「funding/排雷那几道闸验证过了」而实际没有。
现在由 `crypto_strategy.replay` 逐日造帧真评估，形态按**规则**分：

- 规则里**还有**非可回放原语 → `触发 = 双均线代理 AND 可回放条件全过`
  （代理替那些评估不了的择时判断出面，可回放的那部分真的在 gate 交易）。
- 规则原语**全部**成熟 → `触发 = 完整 DSL 评估`，代理退场，`degraded=False`。

所以 `degraded` 不再写死 True：它 = `bool(degraded_reasons)`。返回值里的 `replay` 段
（mode/evaluated_fields/proxy_used）与 per_symbol 的 `replay_days` 让「到底回放了多少」可核对。

无论哪种形态，回测都**套用本策略自己的费率+滑点**（`CostModel`），所以「毛赚净亏/手续费
吃光」这个 Jason 最担心的失败模式始终被验证到。
"""
from collections.abc import Callable
from typing import Any

from crypto_intel_engine.backtest import run_crypto_portfolio_backtest
from crypto_intel_engine.dsl import CryptoStrategySpec, round_trip_cost
from crypto_strategy.replay import METRIC_BACKED_FIELDS

# 回测代理只回放价格/技术类；其余原语无历史序列，用到即标 degraded
_REPLAYABLE_PREFIXES = ("price.",)
_REPLAYABLE_FIELDS = {"price.change_5d_pct", "price.change_20d_pct", "price.change_60d_pct"}

# 少于这么多天的历史，不算「可回放」（几个点算不出有意义的回测）
_MIN_HISTORY_DAYS = 60


def replayable_from_metrics(symbols: list[str], *, min_days: int = _MIN_HISTORY_DAYS,
                            session=None) -> set[str]:
    """查库：哪些 metric 支撑的原语**已经攒够历史**，可以逐日回放了。

    对每个候选原语，要求 universe 里**每个**币（市场级指标只查一次）都有 ≥ `min_days`
    条历史——只要有一个币缺，这条原语在本策略上就还不能算忠实回放。
    """
    from crypto_intel_engine.store import read_market_series, read_metric_series

    own_session = session is None
    if own_session:
        from data_engine.storage.database import get_session
        session = get_session()
    ready: set[str] = set()
    try:
        for field, (metric, is_market) in METRIC_BACKED_FIELDS.items():
            try:
                if is_market:
                    ok = len(read_market_series(session, metric, days=min_days * 2)) >= min_days
                else:
                    ok = all(
                        len(read_metric_series(session, s, metric, days=min_days * 2)) >= min_days
                        for s in symbols
                    )
            except Exception:  # noqa: BLE001 — 查不到就当没攒够，保守
                ok = False
            if ok:
                ready.add(field)
    finally:
        if own_session:
            session.close()
    return ready


_FAST, _SLOW = 10, 30    # 双均线代理周期（自然日）


def _ma_cross_on_bar(fast: int = _FAST, slow: int = _SLOW) -> Callable:
    """返回一个只看历史的 on_bar：短均线上穿长均线→buy，下穿→sell（无未来函数）。"""
    def on_bar(history) -> str | None:
        if len(history) < slow + 1:
            return None
        closes = history["close"]
        fast_now = closes.iloc[-fast:].mean()
        slow_now = closes.iloc[-slow:].mean()
        fast_prev = closes.iloc[-fast - 1:-1].mean()
        slow_prev = closes.iloc[-slow - 1:-1].mean()
        if fast_prev <= slow_prev and fast_now > slow_now:
            return "buy"
        if fast_prev >= slow_prev and fast_now < slow_now:
            return "sell"
        return None
    return on_bar


def _non_replayable_fields(spec: CryptoStrategySpec,
                           extra_replayable: set[str] | None = None) -> list[str]:
    """收集策略用到的、回测代理无法逐日回放的原语（去重）。

    `extra_replayable` 是「库里已攒够历史」的原语集合（见 `replayable_from_metrics`）——
    随着 `crypto_metrics` 时序变长，这个名单会自然缩短。
    """
    replayable = _REPLAYABLE_FIELDS | (extra_replayable or set())
    fields: set[str] = set()
    # ⚠️ 走 `rule_sets()`：组合策略下顶层 entry/exit 是 None，直接读会 AttributeError。
    #    单策略会被归一成一条 weight=1.0 的 RuleSet，两种写法在这儿长成同一个形状。
    for rs in spec.rule_sets():
        for rules in (rs.entry_rules, rs.exit_rules):
            for c in list(rules.when.all_of) + list(rules.when.any_of):
                if c.field not in replayable and not c.field.startswith(_REPLAYABLE_PREFIXES):
                    fields.add(c.field)
    return sorted(fields)


# C++ 引擎对 market="crypto" 写死的 taker 费率（`CommissionConfig::crypto()`）。
# 接口不接受费率覆盖，故策略 CostModel 的 taker_fee_pct 传不下去 —— 差异如实报出来。
_ENGINE_TAKER_PCT = 0.001
# 回放覆盖度低于此值就提醒：net_return 被大量「结构上不可能开仓」的日子稀释了
_MIN_REPLAY_COVERAGE = 0.5

# 🔒 回测里的总仓位上限 = 实盘那条硬风控（必须留 20% 现金）。
# ⛔ 别为了「让回测数字好看一点」把它调高 —— 那等于让回测承诺一个实盘做不到的收益。
_MAX_TOTAL_POSITION_PCT = 0.8


def _stamp_weight(signals: list[dict[str, Any]], weight: float) -> list[dict[str, Any]]:
    """给买入信号盖上子策略的资金权重。

    ⭐ **这是「权重真的影响回测」的唯一落点。** 信号的 `weight` 字段是
    「本单想动用当前可用现金的比例」，C++ 侧默认 0.95。组合策略里把它换成
    子策略自己的权重，于是 60/40 的分配真的会体现在成交名义额上 ——
    否则权重就只是 DSL 里一个没人读的装饰字段。

    ⚠️ 只盖买入：卖出是全平持仓，跟资金分配无关。
    ⚠️ 这是**软上限**不是独立钱包：所有子策略仍抢同一份现金，最终谁买到多少
    由引擎的等比分配 + 仓位闸门决定（见 `engine.cpp` 的 (2b)(2c) 段）。
    ⛔ 别改成「按权重把本金切成 N 份各自独立跑」——那就退回 S8 要解决的老问题了。
    """
    out = []
    for sig in signals:
        s2 = dict(sig)
        if s2.get("action") == "buy":
            s2["weight"] = weight
        out.append(s2)
    return out


def run_backtest_gate(
    spec: CryptoStrategySpec,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    initial_capital: float = 100000.0,
    run_pf: Callable = run_crypto_portfolio_backtest,
    collect: Callable | None = None,
    _matured: set[str] | None = None,
) -> dict[str, Any]:
    """对 universe 每个币逐日回放 DSL（成熟原语真评估 + 代理补缺），聚合净费回报。

    返回 {passed, net_return, metrics, degraded, degraded_reasons, replay, per_symbol}。
    🔴 **口径已于 S8 变更**：从「每个币各发一份完整本金独立跑再把收益率平均」
    改成**一次组合回测（所有币共享同一份资金）**。`net_return` 因此是**组合收益**，
    与历史上那个「各币独立收益的平均」**不可比**。见返回里的 `basis`。

    passed = 组合净费回报 > 0。run_pf/collect/_matured 可注入以便单测（避开 C++/大数据/查库）。
    """
    if collect is None:
        from alpha_lab.signal_runner import collect_signals
        collect = collect_signals

    import pandas as pd

    from alpha_lab.signal_runner import _date_str
    from crypto_strategy.replay import build_frames, dsl_on_bar, load_metric_maps, rule_fields

    cm = spec.cost_model
    proxy_on_bar = _ma_cross_on_bar()
    # C++ 引擎侧强制止损（AI/DSL 的 on_bar 无状态）；滑点走策略假设
    # 🔒 `max_total_position_pct=0.8` = **回测里也必须留 20% 现金**（Jason 2026-07-31 拍板：
    #    回测口径必须跟实盘风控一致）。此前回测能满仓，于是「回测过闸的策略上实盘后
    #    收益系统性低于预期」—— 高估在先。
    #    ⚠️ 它与单币上限是**两条独立约束取更严**，引擎里不是 max（见 risk_manager.h）。
    #    ⚠️ 单币上限走 DSL 的 `per_symbol_exposure_cap_pct`：组合口径下它**第一次真正生效**
    #    （逐币独立回测时每个币都有完整一份本金，这条约束等于不存在）。
    risk_config = {
        "enabled": True,
        "stop_loss_pct": 0.08,
        "max_total_position_pct": _MAX_TOTAL_POSITION_PCT,
        "max_position_pct": spec.position_policy.per_symbol_exposure_cap_pct,
    }
    per_symbol: list[dict[str, Any]] = []

    # 库里已攒够历史的原语算「可回放」——**必须先算**，它决定 on_bar 的形态（代理 vs 真 DSL）
    if _matured is not None:
        matured = set(_matured)
    else:
        try:
            matured = replayable_from_metrics(list(bars_by_symbol))
        except Exception:  # noqa: BLE001 — 查库失败就当一条没攒够（保守，不放宽闸门）
            matured = set()
    replayable = _REPLAYABLE_FIELDS | matured
    # ⭐ 组合策略下每条子策略各有自己的规则 —— 走 `rule_sets()` 归一：
    #    单策略会被归成一条 weight=1.0 的 RuleSet，两种写法在下面长成同一个形状。
    rule_sets = spec.rule_sets()
    used_fields: set[str] = set()
    for rs in rule_sets:
        used_fields |= rule_fields(rs.entry_rules.when) | rule_fields(rs.exit_rules.when)
    evaluated = sorted(used_fields & replayable)
    proxy_used = bool(used_fields - replayable)

    # ── 逐币生成信号：按**它归属的那条规则集**评估 ──
    signals_by_symbol: dict[str, list[dict[str, Any]]] = {}
    usable_bars: dict[str, list[dict[str, Any]]] = {}
    for symbol, bars in bars_by_symbol.items():
        owner = spec.owner_of(symbol)
        if owner is None:
            # ⛔ 不静默跳过。两种情况都到这儿，理由要说准：
            #    ① 单策略：这个币根本不在 spec.universe 里（调用方多传了 bars）
            #    ② 组合策略：顶层 universe 有它，但没有任何子策略认领
            #    悄悄不交易会让人以为「策略认为它没机会」，而其实它压根没被评估过。
            why = ("不在 universe 白名单里" if not spec.sub_strategies
                   else "顶层 universe 有它，但没有任何子策略认领")
            per_symbol.append({"symbol": symbol, "skipped": why, "net_return": None})
            continue
        if not bars or len(bars) < _SLOW + 2:
            per_symbol.append({"symbol": symbol, "skipped": "历史不足", "net_return": None})
            continue
        df = pd.DataFrame(bars)
        if "date" in df.columns:
            df = df.set_index("date")
        dates = [_date_str(idx) for idx in df.index]
        frames = _frames_for(symbol, dates, used_fields & matured,
                             load_metric_maps, build_frames)
        on_bar = dsl_on_bar(owner, frames, replayable, proxy_on_bar, date_of=_date_str)
        signals_by_symbol[symbol] = _stamp_weight(collect(on_bar, df), owner.weight)
        usable_bars[symbol] = bars
        per_symbol.append({"symbol": symbol,
                           # 这个币归哪条子策略管、那条分到多少资金权重
                           "rule_set": owner.name, "weight": owner.weight,
                           # 这个币有几天真的取到了指标帧（0 = 全靠代理）
                           "replay_days": sum(1 for f in frames.values() if f)})

    # ── 一次组合回测：所有币**共享同一份资金**（S8）──
    #
    # 🔴 **这是口径变更，不是优化。**
    # 从前是「每个币各发一份完整本金独立跑，再把收益率平均」——那样没有资金竞争，
    # 「A 占了钱 B 就买不了」不会发生，DSL 里的 `per_symbol_exposure_cap_pct`
    # 也**从来没被验证过**（每个币都能满仓）。现在这些约束第一次真正生效。
    #
    # ⚠️ 新旧 `net_return` **不可比**：旧的是「各币独立收益的算术平均」，
    #    新的是「一个组合的总收益」。历史数字会变，多半变小。
    #    返回里的 `basis` / `engine_version` 就是给这件事留的标记。
    portfolio: dict[str, Any] = {}
    net_return = None
    if usable_bars:
        # ⚠️ `market` 决定 C++ 侧的**费率**（A 股有印花税、crypto 没有）
        #    **和交易单位**（A 股 100 股一手、crypto/美股 1）。
        #    ⛔ 别写死 crypto —— 股票策略拿 crypto 的费率回测出来的净收益是假的，
        #    而这个数字正是 arm 前的准入判据。
        portfolio = run_pf(usable_bars, signals_by_symbol,
                           initial_capital=initial_capital,
                           market=spec.market,
                           slippage_pct=cm.slippage_pct,
                           risk_config=risk_config) or {}
        pm = portfolio.get("metrics") or {}
        net_return = _safe(pm.get("total_return"))
        if net_return is not None:
            net_return = round(net_return, 6)
        # 逐币成交数从组合结果里回填（组合口径下**没有**逐币收益率这回事：
        # 一份共享现金拆不出「这个币赚了百分之几」，硬拆出来的数会骗人）
        fills: dict[str, int] = {}
        for t in portfolio.get("trades") or []:
            fills[t.get("symbol")] = fills.get(t.get("symbol"), 0) + 1
        coverage = portfolio.get("bar_coverage") or {}
        for row in per_symbol:
            if row.get("skipped"):
                continue
            # ⚠️ **单位变了**：旧口径这里是 `metrics.total_trades`（配对后的
            #    **回合数**），组合口径下是**成交笔数**（BUY/SELL 各算一笔），
            #    闭合回合大约翻一倍。同名同位置，别拿新旧值比大小。
            row["num_trades"] = fills.get(row["symbol"], 0)
            row["bar_days"] = coverage.get(row["symbol"])
            # ⛔ 刻意**不给** per-symbol 的 net_return：见上面那段
            row["net_return"] = None
    degraded_fields = _non_replayable_fields(spec, matured)
    mode = "proxy" if proxy_used and not evaluated else ("hybrid" if proxy_used else "dsl")
    # `degraded` 专指「你的规则没能被逐日回放」（原语不可回放 → 用了双均线代理）。
    # 下面两条是**另一类**问题：规则回放了，但这个 net_return 的含义比看上去弱。
    # 混进 degraded_reasons 会让两种完全不同的警告纠缠在一起，故单列 `caveats`。
    caveats: list[str] = []

    # ① 费率口径：C++ 引擎按 market="crypto" 用**标准 taker 0.1%**，接口不接受费率覆盖，
    #    策略 CostModel 里配的 taker_fee_pct **传不下去**（滑点能传，费率不能）。
    #    改这个要动 C++ 引擎重编译，超出本次范围——但必须如实说明，别让「按你的费率回测过了」
    #    这句话骗人。BNB 抵扣（0.075%）或 VIP 费率与 0.1% 有差时，真实净收益有系统性偏差。
    fee_basis = {"engine_taker_pct": _ENGINE_TAKER_PCT,
                 "strategy_taker_pct": cm.taker_fee_pct,
                 "matches": abs(cm.taker_fee_pct - _ENGINE_TAKER_PCT) < 1e-9}
    if not fee_basis["matches"]:
        caveats.append(
            f"回测按引擎标准 taker {_ENGINE_TAKER_PCT:.3%} 计费，而策略配置的是 "
            f"{cm.taker_fee_pct:.3%}（引擎接口不支持费率覆盖）——净收益有系统性偏差")

    # ② 回放覆盖度：原语「成熟」后 net_return 会**静默换口径**。指标只从 crypto_metrics
    #    开始攒（几十天），而 bars 拉 400 天：没有指标帧的那些日子里 evaluate 对缺值一律
    #    判 False → 结构上不可能开仓，却照样按全窗口平均算进 net_return。
    #    同一个数字在原语成熟前后含义完全不同，必须把「几天真有指标」摆出来。
    #    ⚠️ 只有当规则**真的依赖指标帧**时这个数才有意义，见 `_replay_coverage`。
    metric_fields = used_fields & matured
    coverage = _replay_coverage(per_symbol, bars_by_symbol, metric_fields)
    if coverage is not None and coverage < _MIN_REPLAY_COVERAGE:
        caveats.append(
            f"只有 {coverage:.0%} 的回测日有真实指标帧，其余日子进场条件结构上恒为 False"
            f"（指标历史还没攒够）——net_return 被大量「不可能开仓」的日子稀释，仅供参考")

    # ③ 组合口径特有的诊断，全部**不静默**
    contention = portfolio.get("cash_contention") or {}
    # 🔴 `.get(...) or {}` 会把**两件完全不同的事**压成同一个值：
    #    「引擎报了：一天都没卡住」和「引擎压根没报这个字段」。
    #    实测踩过：:8002 上跑着一个被覆盖前的旧二进制，`cap_contention` 根本不在响应里，
    #    于是 15% 上限明明削掉了 85% 的名义额，caveats 却是空的 —— 全程静默。
    #    ⛔ 这正是本模块最不该有的那种静默，所以缺字段要**明说**。
    cap_missing = "cap_contention" not in portfolio and bool(portfolio)
    cap_contention = portfolio.get("cap_contention") or {}
    if contention.get("days"):
        caveats.append(
            f"有 {contention['days']} 天出现资金竞争（多个币同日抢同一份现金，"
            f"按名义额等比缩减，累计削掉 {contention.get('trimmed_notional', 0):,.0f} 名义额）"
            f"——这正是组合口径与「各币独立满仓」最大的差别所在")
    # ⚠️ 仓位上限竞争与资金竞争是**两件事**：额度卡住时账上现金还剩着，
    #    上面那条一天都不会记。第一版漏了它，于是「后几个币一单都买不到」全程静默。
    if cap_missing:
        caveats.append(
            "回测引擎没有返回 `cap_contention` —— 说明它是 S8 之前的旧二进制。"
            "仓位上限**可能**削了单但无法核对（重新编译并重启 :8002 上的 backtest_server）")
    # ⛔ 单币上限直接裁掉的部分 —— 与「额度竞争」是两件事，但对使用者一样重要：
    #    实测 15% 上限把请求削掉约 85%，net_return 跟着缩到 1/6。
    #    不说的话屏幕上只剩一个数字，看不出它是被上限压出来的。
    symbol_cap = portfolio.get("symbol_cap") or {}
    if symbol_cap.get("days"):
        caveats.append(
            f"有 {symbol_cap['days']} 天被**单币敞口上限**"
            f"（{spec.position_policy.per_symbol_exposure_cap_pct:.0%}）直接裁小了下单量，"
            f"累计削掉 {symbol_cap.get('trimmed_notional', 0):,.0f} 名义额 —— "
            f"⚠️ 这会把收益和亏损**一起**按比例压向 0，别把跌幅收窄读成「策略变好了」")
    if cap_contention.get("days"):
        caveats.append(
            f"有 {cap_contention['days']} 天被仓位上限卡住（单币上限 "
            f"{spec.position_policy.per_symbol_exposure_cap_pct:.0%} / 总仓位 "
            f"{_MAX_TOTAL_POSITION_PCT:.0%}，额度按名义额等比分摊，累计削掉 "
            f"{cap_contention.get('trimmed_notional', 0):,.0f} 名义额）"
            f"——币数越多这个约束越紧，net_return 会被它系统性压低")
    # ⛔ 一单都没下的币要点名。价格缩放失真、信号全空、历史太短都会导致它，
    #    而在组合里它只表现为「收益率略低」——看不出是哪个币出了问题。
    silent = [r["symbol"] for r in per_symbol
              if not r.get("skipped") and not r.get("num_trades")]
    if silent:
        caveats.append(
            f"这些币在整个回测期内**一单都没下**：{'、'.join(silent)} —— "
            f"它们对组合收益的贡献是 0，先确认是「策略确实没信号」还是数据有问题")
    if portfolio.get("duplicate_dates"):
        caveats.append(
            f"输入数据里有 {portfolio['duplicate_dates']} 根 bar 的日期与同币另一根重复"
            f"（日线不该有重复日期）——每天只保留了最后一根，数据源要查")
    if portfolio.get("dropped_stale_orders"):
        caveats.append(
            f"{portfolio['dropped_stale_orders']} 笔挂单因所属币的数据半途断了而从未成交")

    return {
        "passed": bool(net_return is not None and net_return > 0),
        "net_return": net_return,
        # 🔴 **口径标记**：S8 之前这个数是「各币独立收益的算术平均」（每个币各发一份
        #    完整本金），之后是「一个组合的总收益」（共享一份资金）。两者**不可比**，
        #    拿新数字跟库里的历史值比大小是没有意义的。S3 竞技场读的就是它。
        "basis": "portfolio_shared_capital",
        "engine_version": portfolio.get("engine_version"),
        "cash_contention": contention,
        "cap_contention": cap_contention,
        "symbol_cap": symbol_cap,
        "metrics": {"portfolio_net_return": net_return, "symbols_tested": len(usable_bars),
                    "round_trip_cost": round(round_trip_cost(cm), 6),
                    "matured_fields": sorted(matured),
                    # 本次真正需要查 crypto_metrics 的字段。空 = replay_coverage 无意义
                    # （规则全靠 bar 派生原语，每根 bar 都算得出来），故 coverage 为 None
                    "metric_fields": sorted(metric_fields),
                    "fee_basis": fee_basis, "replay_coverage": coverage},
        "degraded": bool(degraded_fields),
        "degraded_reasons": _degraded_reasons(mode, degraded_fields, evaluated),
        "caveats": caveats,     # 「数字本身可信度」的警告，与 degraded 正交
        "replay": {"mode": mode, "proxy_used": proxy_used, "evaluated_fields": evaluated},
        "per_symbol": per_symbol,
    }


def _replay_coverage(per_symbol: list[dict], bars_by_symbol: dict,
                     metric_fields: set[str]) -> float | None:
    """「有真实指标帧的天数 / 回测总天数」。没有可比对的返回 None（≠ 0%）。

    ⛔ **规则不依赖指标帧时必须返回 None，不能返回 0%**。`replay_days` 数的是
    `crypto_metrics` 来的 frames，但 `replay.py` 的 on_bar 是
    `frame = {**frames.get(date, {}), **_price_frame(history)}` —— `price.change_*`
    这类原语**每根 bar 都直接从 df 算得出来，压根不走 frames**。

    于是一条只用 `price.change_5d_pct` 的策略：`mode="dsl"`、`degraded=False`
    （100% 忠实回放），旧实现却算出 coverage=0，弹出「其余日子进场条件结构上恒为
    False（指标历史还没攒够）」—— 把最干净的那种回放说成最不可信，正好和本模块
    「别让数字看起来比实际更有分量」的初衷反了。

    Args:
        metric_fields: 本次规则里**真正需要查 `crypto_metrics`** 的字段
            （= `used_fields & matured`，也正是 `_frames_for` 的入参）。空集 = 无可比对。
    """
    if not metric_fields:
        return None
    have = total = 0
    for r in per_symbol:
        bars = bars_by_symbol.get(r.get("symbol")) or []
        if r.get("skipped") or not bars:
            continue
        have += int(r.get("replay_days") or 0)
        total += len(bars)
    return round(have / total, 4) if total else None


def _frames_for(symbol: str, dates: list[str], fields: set[str],
                load_metric_maps: Callable, build_frames: Callable) -> dict[str, dict]:
    """取该币的逐日指标帧。查库失败 → 空帧（退化成纯代理，绝不放宽闸门）。"""
    if not fields:
        return {}
    from data_engine.storage.database import get_session
    session = get_session()
    try:
        maps = load_metric_maps(symbol, fields, days=len(dates) + 5, session=session)
    except Exception:  # noqa: BLE001
        return {}
    finally:
        session.close()
    return build_frames(dates, maps)


def _degraded_reasons(mode: str, degraded_fields: list[str], evaluated: list[str]) -> list[str]:
    """三态如实交代：全代理 / 混合 / 纯 DSL 忠实回放。编译工具会把这段原样回给 Jason。"""
    if mode == "dsl":
        return []
    reasons = []
    if degraded_fields:
        reasons.append(f"未逐日回放的 DSL 原语：{degraded_fields}")
    if evaluated:
        reasons.append(f"已逐日真实评估的原语：{evaluated}")
    reasons.append("其余条件用双均线技术代理替身，验证该币在真实费率下的成本可行性"
                   if evaluated else
                   "回测用双均线技术代理，仅验证该币在真实费率下的成本可行性，非 DSL 忠实回放")
    return reasons


def _safe(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None   # NaN 过滤
    except (TypeError, ValueError):
        return None
