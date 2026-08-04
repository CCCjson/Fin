"""分市场本金（S5 03c 地基）。

Jason 2026-08-04 拍板的三条，每条都在这里有一个会红的用例：

1. **不要「总资金」这个概念** —— 各市场是 CNY/HKD/USD/USDT，求和就是混币种；
2. **不自动迁移、强制手填** —— 未配置就是未配置，⛔ 不回落到那个全局 5000
   （它是**A 股口径的人民币**，拿去给美股/币算仓位是错的口径且不报错）；
3. 本轮**只做地基**，一个消费方都不迁 —— 所以 `get_total_capital()` 行为必须原样。
"""
import pytest

from data_engine.storage.database import get_session
from data_engine.storage.models import UserSettings
from trading_engine.risk import adapter


@pytest.fixture
def clean():
    """测完清掉分市场本金，并**把 `total_capital` 还原**。

    ⚠️ 「一删了事」不够：本文件会写 `total_capital` 来验老函数没被改口径，
    留着的话整轮 session 就永久钉着一个非默认总资金 —— 而
    `tests/test_risk_total_position_floor.py` 的 fixture 恢复的是「原值」，
    跑在本文件之后会把它当原值恢复回去。任何基于 5000 默认值断绝对金额的用例
    就变成了**顺序依赖的偶发红灯**。
    """
    s = get_session()
    try:
        row = s.query(UserSettings).filter(
            UserSettings.key == "total_capital").first()
        before = row.value if row else None
    finally:
        s.close()

    yield

    s = get_session()
    try:
        s.query(UserSettings).filter(
            UserSettings.key.like(f"{adapter.CAPITAL_KEY_PREFIX}%")).delete(
                synchronize_session=False)
        row = s.query(UserSettings).filter(
            UserSettings.key == "total_capital").first()
        if row is not None and before is not None:
            row.value = before
        s.commit()
    finally:
        s.close()


def _set(key: str, value: str):
    s = get_session()
    try:
        row = s.query(UserSettings).filter(UserSettings.key == key).first()
        if row is None:
            s.add(UserSettings(key=key, value=value))
        else:
            row.value = value
        s.commit()
    finally:
        s.close()


def test_unset_market_returns_none_not_a_fallback(clean):
    """🔴 未配置 → None，**不回落到全局总资金**。

    回落的话「没配置」和「配置成 5000」就再也分不出来了，而那个 5000 是
    **A 股口径的人民币** —— 拿去给美股或币算仓位是错的口径，且不会有任何报错。
    """
    assert adapter.get_market_capital("us_stock") is None
    assert adapter.get_total_capital() > 0          # 全局值仍在，但不该被借用
    assert adapter.get_market_capital("crypto") is None


def test_zero_is_not_the_same_as_unset(clean):
    """🔴 `0` = 明确「这个市场不投钱」，`None` = 还没填。两件事。"""
    _set("capital_a_share", "0")
    assert adapter.get_market_capital("a_share") == 0.0
    _set("capital_a_share", "")
    assert adapter.get_market_capital("a_share") is None


def test_garbage_value_counts_as_unset_not_zero(clean):
    """⚠️ 填错字的人想表达的显然不是「这个市场不投钱」。"""
    _set("capital_us_stock", "五千")
    assert adapter.get_market_capital("us_stock") is None


def test_each_market_is_independent(clean):
    """各市场各算各的 —— 币种都不一样。"""
    _set("capital_a_share", "100000")
    _set("capital_crypto", "5000")
    assert adapter.get_market_capital("a_share") == 100000.0
    assert adapter.get_market_capital("crypto") == 5000.0
    assert adapter.get_market_capital("hk_stock") is None
    caps = adapter.market_capitals()
    assert caps == {"a_share": 100000.0, "hk_stock": None,
                    "us_stock": None, "crypto": 5000.0}


def test_nobody_sums_the_market_capitals():
    """⛔ **全仓不许有人把各市场本金加起来。**

    各市场是 CNY/HKD/USD/USDT，求和是混币种，而「每市场独立本金、不折算汇率」
    是投资组合模块已拍的板。Jason 2026-08-04 的原话是「这个概念早晚还会有人
    拿它当分母」，所以这里是**结构性门禁**，不是查函数名 ——
    求和不会发生在 `adapter` 里，会发生在下一轮某个消费方里。

    ## ⚠️ 判据刻意放宽到「同一个函数体内既取了本金、又有求和动作」

    第一版只认 `sum(...)` **实参里字面出现** `market_capitals` 的形状，实测漏掉
    下面这两种 —— 而它们恰恰是人正常会写的：

        caps = market_capitals()
        total = sum(v or 0 for v in caps.values())      # 漏

        for m in markets:
            total += get_market_capital(m) or 0          # 漏

    所以改成「函数体里调了取本金的 API + 出现 `sum(`/`+=`」就报。
    宁可误报：合法用法只有「逐市场取值」和「渲染」两种，误报了加一行
    `# noqa: capital-sum` 豁免即可（成本远低于漏掉一次混币种求和）。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    skip = {"tests", "__pycache__", ".venv", "node_modules"}
    getters = {"market_capitals", "get_market_capital"}
    offenders: list[str] = []

    def _scan(fn: ast.AST, path: Path, src_lines: list[str]) -> None:
        takes_capital = False
        sums_at: list[int] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in getters:
                    takes_capital = True
                elif name == "sum":
                    sums_at.append(node.lineno)
            elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
                sums_at.append(node.lineno)
        if not (takes_capital and sums_at):
            return
        for lineno in sums_at:
            line = src_lines[lineno - 1] if lineno - 1 < len(src_lines) else ""
            if "noqa: capital-sum" in line:
                continue
            offenders.append(f"{path.relative_to(root)}:{lineno}")

    for py in root.rglob("*.py"):
        if any(part in skip for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _scan(node, py, lines)

    assert not offenders, (
        f"有人把分市场本金加起来了（混币种）：{offenders}。"
        f"⛔ 见 DECISIONS.md 2026-08-04：不做「总资金 = 各市场之和」。"
        f"确属误报就在那一行加 `# noqa: capital-sum`")


def test_every_canonical_market_has_a_slot_and_is_ai_readonly():
    """三处必须同时有：读取键、`_DEFAULT_SETTINGS` 槽位、AI 只读黑名单。

    ⚠️ 从 `CANONICAL_MARKETS` 推导而不是硬编码四个 —— 加第五个市场时才会红。
    漏 `_DEFAULT_SETTINGS`：PUT 直接 404，设置页只弹个红 toast（看起来像网络抖动）；
    漏黑名单：AI 就能改本金了。
    """
    from agents.tools.settings_tools import _RISK_READONLY_KEYS
    from api.routes.data import _DEFAULT_SETTINGS
    from common.market import CANONICAL_MARKETS, currency_of

    for m in CANONICAL_MARKETS:
        key = adapter.capital_key(m)
        assert key in _DEFAULT_SETTINGS, f"{key} 没有槽位，设置页存不进去"
        assert _DEFAULT_SETTINGS[key]["value"] == "", f"{key} 不该有默认值（强制手填）"
        assert key in _RISK_READONLY_KEYS, f"{key} 没锁住，AI 能改本金"
        # ⚠️ 币种缺了会静默显示成「某某本金（）」——加第五个市场时必须红
        assert currency_of(m), f"{m} 没有币种，设置页会显示成「本金（）」"


def test_ai_cannot_write_capital_even_when_it_tries(clean):
    """🔒 行为级：AI 真去写本金，必须被挡回来且**库里没变**。

    ⚠️ 只断言集合成员关系测不到「黑名单检查有没有被绕过去」。
    """
    from agents.tools.settings_tools import update_setting

    r = update_setting(key="capital_a_share", value="999999")
    assert r.business_result == "negative", "AI 写本金居然成功了"
    assert adapter.get_market_capital("a_share") is None, "库里被改了"


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-5000", "1e400"])
def test_non_finite_or_negative_is_unset_not_a_number(clean, bad):
    """🔴 `float()` 对 nan/inf/负数一个都不抛。

    下一轮这个值就是仓位上限的基数和收益率的分母：`nan` 会让所有 `>` 比较
    恒为 False —— **风控检查静默放行**。
    """
    _set("capital_crypto", bad)
    assert adapter.get_market_capital("crypto") is None


def test_market_key_is_built_in_one_place(clean):
    """键名只此一处拼 —— 各处手拼的话，改前缀时会漏。"""
    assert adapter.capital_key("a_share") == "capital_a_share"
    # 任意写法先归一（别名表在 common/market.py）
    assert adapter.capital_key("A股") == "capital_a_share"
    assert adapter.capital_key("us") == "capital_us_stock"


def test_capital_keys_are_readonly_to_the_ai():
    """🔒 **本金就是风控参数** —— 它决定每笔下多大、日亏 3% 的基数是多少。

    漏进白名单 AI 就能自己改本金了，而风控是门，不是可协商的同事。
    """
    from agents.tools.settings_tools import _RISK_READONLY_KEYS
    for m in ("a_share", "hk_stock", "us_stock", "crypto"):
        assert adapter.capital_key(m) in _RISK_READONLY_KEYS


def test_get_total_capital_still_reads_its_own_key(clean):
    """本轮**只做地基**：一个消费方都不迁，所以老函数行为必须原样。

    ⚠️ 只断言「是个正 float」拦不住它偷偷改成各市场求和 —— 那样也是正 float。
    所以两个方向都测：它还读 `total_capital` 那个键、且**不受分市场值影响**。
    """
    _set("total_capital", "12345")
    assert adapter.get_total_capital() == 12345.0

    _set("capital_a_share", "777")
    _set("capital_crypto", "888")
    assert adapter.get_total_capital() == 12345.0, "地基往存量口径里渗了"


# ── 03c-2：未配置本金 → 策略 fail-closed ──────────────────────────────────
#
# 🔒 Jason 2026-08-04：不自动迁移、强制手填，未配置的市场**策略不跑**。
# ⛔ 这三条守的是「不许回落」—— 回落到那个全局 5000 的话，美股/币会拿着
#    **A 股口径的人民币**算仓位，而且不会有任何报错。

def test_stock_scheduler_does_not_tick_without_capital(clean):
    """股票调度器：没配本金就不跑，且**说得出为什么**。"""
    from unittest.mock import patch

    from strategy_runtime.scheduler import StockStrategyScheduler

    row = type("R", (), {"strategy_id": "CS-X", "market": "a_share",
                         "mode": "paper", "interval_minutes": 30,
                         "last_run_at": None})()
    sch = StockStrategyScheduler()
    with patch("strategy_runtime.scheduler.is_open", return_value=True), \
         patch("crypto_strategy.service.spec_from_row") as spec_from_row:
        spec_from_row.return_value = type("S", (), {"market": "a_share",
                                                    "interval_minutes": 30})()
        out = sch.tick_one(row)
    assert "还没配置本金" in out["skipped"]
    assert "设置页" in out["skipped"], "只说不跑不够，得说去哪填"


def test_stock_scheduler_runs_once_capital_is_set(clean):
    """反方向：配上就该跑起来，别把这道闸做成永久墙。"""
    from unittest.mock import patch

    from strategy_runtime.scheduler import StockStrategyScheduler

    _set("capital_a_share", "100000")
    row = type("R", (), {"strategy_id": "CS-X", "market": "a_share",
                         "mode": "paper", "interval_minutes": 30,
                         "last_run_at": None})()
    sch = StockStrategyScheduler()
    with patch("strategy_runtime.scheduler.is_open", return_value=True), \
         patch("crypto_strategy.service.spec_from_row") as spec_from_row, \
         patch.object(StockStrategyScheduler, "_adapter_for"), \
         patch("strategy_runtime.executor.run_tick",
               return_value={"decisions": [], "tally": {}}) as run_tick:
        spec_from_row.return_value = type("S", (), {"market": "a_share",
                                                    "interval_minutes": 30})()
        sch.tick_one(row)
    assert run_tick.called, "配了本金还是不跑"
    assert run_tick.call_args.kwargs["capital"] == 100000.0, "没把分市场本金传下去"


def test_crypto_engine_skips_the_strategy_without_capital(clean):
    """crypto 引擎：没配本金**在熔断判定之前**就返回。

    ⚠️ 顺序要紧：`check_daily_loss` 拿 capital 当分母，None 进去要么炸、
    要么算出个假的亏损比例 —— 那会变成「因为一个不存在的亏损把策略熔断」。
    """
    from unittest.mock import patch

    from crypto_strategy.engine import CryptoStrategyEngine

    eng = CryptoStrategyEngine()
    row = type("R", (), {"strategy_id": "CS-C", "mode": "paper"})()
    # ⚠️ patch 到 `engine.spec_from_row` 而不是 `service.spec_from_row`：
    #    engine.py 顶部是 `from crypto_strategy.service import spec_from_row`
    #    （**模块级绑定**），打在 service 上根本不生效 —— 这个坑全项目 20+ 处。
    with patch("crypto_strategy.engine.spec_from_row") as spec_from_row, \
         patch.object(CryptoStrategyEngine, "_broker_and_info",
                      return_value=(None, {})), \
         patch("crypto_strategy.guardrails.check_daily_loss") as daily_loss, \
         patch.object(CryptoStrategyEngine, "_log_run") as log_run, \
         patch.object(CryptoStrategyEngine, "_touch"):
        spec_from_row.return_value = type("S", (), {"capital_basis": "config"})()
        eng._run_one(row)
    assert not daily_loss.called, "没本金却先跑了熔断判定"
    assert log_run.called
    assert "还没配置本金" in str(log_run.call_args)


def test_returns_denominator_is_per_market_and_says_so(clean):
    """收益率分母按市场取；没配时**说人话**而不是报「读不到」。"""
    from crypto_strategy import performance as perf

    row = type("R", (), {"strategy_id": "CS-X", "market": "us_stock",
                         "capital_basis": "config"})()
    assert perf._capital_basis(row, "us_stock") is None
    _set("capital_us_stock", "20000")
    assert perf._capital_basis(row, "us_stock") == 20000.0
    # ⚠️ 同市场内仍是一把尺子 —— 换一条策略拿到的是同一个数
    other = type("R", (), {"strategy_id": "CS-Y", "market": "us_stock",
                           "capital_basis": "real_total_value"})()
    assert perf._capital_basis(other, "us_stock") == 20000.0


def test_daily_returns_says_unset_differently_from_zero(clean):
    """🔴 「没配本金」和「本金填了 0」必须说**不同的话**。

    前者去设置页填，后者是这个市场没钱可用 —— 压成同一句，Jason 会查错方向。
    ⚠️ 这条早返回在 `tests/crypto_strategy/` 里永远走不到（那边 autouse 播了种），
    所以必须在这个文件测。
    """
    from crypto_strategy import performance as perf
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy

    s = get_session()
    try:
        s.add(CryptoStrategy(strategy_id="CS-CAP", name="本金测试", mode="paper",
                             status="armed", enabled=1, market="us_stock"))
        s.commit()
    finally:
        s.close()
    try:
        unset = perf.daily_returns("CS-CAP", days=30)
        assert unset["basis"] == "none"
        assert "还没配置本金" in unset["reason"] and "设置页" in unset["reason"]

        _set("capital_us_stock", "0")
        zero = perf.daily_returns("CS-CAP", days=30)
        assert zero["basis"] == "none"
        assert zero["reason"] != unset["reason"], "两种原因说了同一句话"
        assert "设置为 0" in zero["reason"]
    finally:
        s = get_session()
        try:
            s.query(CryptoStrategy).filter(
                CryptoStrategy.strategy_id == "CS-CAP").delete()
            s.commit()
        finally:
            s.close()


def test_real_total_value_leg_fails_closed_too(clean):
    """🔴 `real_total_value` 那条腿取不到值时也要 fail-closed。

    回 0.0 的话会**越过** `is None` 那道闸 → `check_daily_loss` 判「资金口径未知」
    放行 → 每个币回一句「仓位换算为 0」。**停摆了，而 run 行里一个字都没提资金** ——
    这是最难查的那种停摆。
    """
    from crypto_strategy.engine import CryptoStrategyEngine

    eng = CryptoStrategyEngine()
    spec = type("S", (), {"capital_basis": "real_total_value"})()
    assert eng._capital(spec, {}) is None
    assert eng._capital(spec, {"total_value": 0}) is None
    assert eng._capital(spec, {"total_value": 1234.5}) == 1234.5


def test_health_says_capital_not_arm_status(clean):
    """🔴 体检出口**不许把人指反方向**。

    股票 tick 从来不写 run 行（既有欠债），所以本金没填时「一条运行记录都没有」
    是唯一的出口 —— 它原本会说「先确认是不是没被武装」，而真因是本金没配。
    """
    from crypto_strategy import performance as perf
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy

    s = get_session()
    try:
        s.add(CryptoStrategy(strategy_id="CS-H", name="体检测试", mode="paper",
                             status="armed", enabled=1, market="a_share"))
        s.commit()
    finally:
        s.close()
    try:
        h = perf.strategy_health("CS-H", days=30)
        assert h["ok"] is False
        assert "还没配置本金" in h["reason"] and "设置页" in h["reason"]
        assert "没被武装" not in h["reason"], "把人指去查 arm 状态了"

        # 配上之后回到原来那句（这道交叉不该变成永久覆盖）
        _set("capital_a_share", "100000")
        h2 = perf.strategy_health("CS-H", days=30)
        assert "没被武装" in h2["reason"]
    finally:
        s = get_session()
        try:
            s.query(CryptoStrategy).filter(
                CryptoStrategy.strategy_id == "CS-H").delete()
            s.commit()
        finally:
            s.close()


# ── 09a：纸面账户按市场分池 ────────────────────────────────────────────────
#
# 🔴 改之前 `get_paper_broker()` 是**一个全局单例**，初始现金取 `get_total_capital()`，
#    却同时服务 A 股和美股两条策略线 —— 两个市场共用一笔钱（还是混币种的一笔）。


@pytest.fixture
def fresh_brokers():
    """清空纸面账户注册表。

    ⚠️ 注册表是**进程级**的，不清的话第一个用例建出来的账户会被后面的用例复用，
    于是「本金改了、账户没跟着改」这类回归就测不出来了。
    """
    from trading_engine.brokers import paper_broker as pb
    pb._paper_brokers.clear()
    yield
    pb._paper_brokers.clear()


def test_each_market_gets_its_own_cash_pool(clean, fresh_brokers):
    """🔴 一个市场一个现金池，初始现金 = 该市场本金。

    共用一个池子的话，先跑的市场把现金买光，另一个市场就**静默**下不出单 ——
    而且两笔钱币种都不一样，共用本身就是混币种。
    """
    from trading_engine.brokers.paper_broker import get_paper_broker

    _set("capital_a_share", "100000")
    _set("capital_us_stock", "20000")

    a = get_paper_broker("a_share")
    u = get_paper_broker("us_stock")
    assert a is not u, "两个市场拿到了同一个账户"
    assert a.initial_cash == 100000.0
    assert u.initial_cash == 20000.0

    # 同一个市场必须是同一个账户（否则持仓和现金每次调用都重置）
    assert get_paper_broker("a_share") is a
    # 任意写法先归一，⛔ 别因为别名多开一个池子
    assert get_paper_broker("A股") is a


def test_no_paper_account_without_capital(clean, fresh_brokers):
    """🔒 没配本金就不发账户，且**说得出去哪填**。

    ⛔ 回落到全局 5000 的话，美股会拿着 A 股口径的人民币当初始现金开跑，
    不报错、不留痕，纸面战绩从第一天起就是错的。
    """
    from trading_engine.brokers.paper_broker import (
        MarketCapitalNotConfiguredError,
        get_paper_broker,
    )

    with pytest.raises(MarketCapitalNotConfiguredError) as ei:
        get_paper_broker("us_stock")
    assert "还没配置本金" in str(ei.value)
    assert "设置页" in str(ei.value), "只说不给账户不够，得说去哪填"


def test_zero_capital_still_gets_an_account(clean, fresh_brokers):
    """⚠️ `0` 是**合法本金**（这个市场不投钱），不是「未配置」。

    判据写成 `if not capital` 就会把它当没配置抛出去 —— 那是把 Jason 的一个
    明确选择报成了配置缺失。

    🔴 **必须测到「拿这个账户干活」为止**：只断言 `initial_cash` 的话，「0 合法」
    就只在构造函数那半程成立 —— `get_account_info()` 的 `return_pct` 拿
    `initial_cash` 当分母，实测会 `ZeroDivisionError` 一路炸到 MoneyBill 的下单预览。
    """
    from trading_engine.brokers.paper_broker import get_paper_broker

    _set("capital_hk_stock", "0")
    b = get_paper_broker("hk_stock")
    assert b.initial_cash == 0.0
    assert b.cash == 0.0

    acct = b.get_account_info()          # ⛔ 别删这行：除零就在这儿
    assert acct["cash"] == 0.0
    # ⚠️ `None` 而不是 `0`：本金 0 的账户没有收益率这个量，
    #    填 0 等于断言「不赚不亏」。
    assert acct["return_pct"] is None


def test_scheduler_hands_each_strategy_its_own_market_broker(clean, fresh_brokers):
    """调度器必须按 spec 的市场取账户，⛔ 别再发全局单例。"""
    from strategy_runtime.scheduler import StockStrategyScheduler
    from trading_engine.brokers.paper_broker import get_paper_broker

    _set("capital_a_share", "100000")
    _set("capital_us_stock", "20000")

    # ⚠️ `mode` 不可省：`_adapter_for` 按它决定走真券商还是纸面。
    #    真实 spec 一定带（`CryptoStrategySpec.mode` 是有默认值的 Literal），
    #    所以源码**刻意不用 `getattr` 兜底** —— 没有 mode 的 spec 是 bug。
    a_spec = type("S", (), {"market": "a_share", "mode": "paper",
                            "interval_minutes": 30})()
    u_spec = type("S", (), {"market": "us_stock", "mode": "paper",
                            "interval_minutes": 30})()

    a_adapter = StockStrategyScheduler._adapter_for(a_spec)
    u_adapter = StockStrategyScheduler._adapter_for(u_spec)

    assert a_adapter.broker is get_paper_broker("a_share")
    assert u_adapter.broker is get_paper_broker("us_stock")
    assert a_adapter.broker is not u_adapter.broker


def test_chat_order_says_it_plainly_when_capital_is_missing(clean, fresh_brokers):
    """MoneyBill 下单入口：本金没配要说人话，⛔ 别抛 traceback。

    这个文件整个是 A 股口径（成交日/¥/StockInfo），所以它取的就是 A 股账户。
    """
    from agents.tools import trading_tools as tt

    prev = tt.preview_order({"symbol": "600519.SH", "side": "buy",
                             "quantity": 100, "price": 100.0})
    assert "还没配置本金" in prev.get("error", "")

    r = tt.place_order(symbol="600519.SH", side="buy", quantity=100, price=100.0)
    assert r.business_result == "negative"
    assert "还没配置本金" in (r.message or "")


def test_verdict_reads_the_entry_gate_reason(clean):
    """⭐ 入口闸的理由就写在 run 的顶层 detail 里，必须被念出来。

    不读的话只会说一句「没有任何逐币决策明细，看 runs.by_status」——
    让人去翻日志，而理由一直躺在手边。
    """
    from crypto_strategy import performance as perf

    runs = [{"status": "skipped", "detail": {"capital": "加密还没配置本金 —— 去设置页填"}}]
    assert "还没配置本金" in (perf._entry_gate_reason(runs) or "")
    # 逐币明细不该被当成入口闸原因
    assert perf._entry_gate_reason([{"status": "evaluated",
                                     "detail": {"decisions": [{"status": "no_signal"}]}}]) is None

    row = type("R", (), {"status": "armed", "halted_reason": None})()
    v = perf._verdict(row, runs, {}, 0, {"basis": "none", "reason": "x"})
    assert "还没配置本金" in v


# ── 任务 10：没接真券商时不许跑 live ──────────────────────────────────────


def test_paper_strategy_never_touches_a_real_broker(clean, fresh_brokers,
                                                    monkeypatch):
    """🔴 真券商到位那天，`mode="paper"` 的策略**必须还是走纸面**。

    `paper` 和 `live` 的区别**全在 broker**（`executor.run_tick` 的原话），
    这一层不判 mode 的话，接上券商当天所有纸面策略会一起拿真钱下单 ——
    而纸面策略正是那些**还没验证过**的。
    """
    import strategy_runtime.stock_adapter as sa
    from strategy_runtime.scheduler import StockStrategyScheduler
    from trading_engine.brokers.paper_broker import get_paper_broker

    _set("capital_a_share", "100000")
    real = object()
    monkeypatch.setattr(sa, "live_broker_for", lambda market: real)

    paper_spec = type("S", (), {"market": "a_share", "mode": "paper",
                                "interval_minutes": 30})()
    live_spec = type("S", (), {"market": "a_share", "mode": "live",
                               "interval_minutes": 30})()

    assert StockStrategyScheduler._adapter_for(paper_spec).broker is \
        get_paper_broker("a_share"), "纸面策略被塞了真券商"
    assert StockStrategyScheduler._adapter_for(live_spec).broker is real


def test_existing_live_stock_row_is_skipped_at_runtime(clean):
    """🔴 arm 那道闸管不到**存量** `mode="live"` 的行，运行时这道才管得到。

    存量行每 tick 都会往 `strategy_trades` 写 `mode="live"` 的成交，而成交其实是
    纸面的 —— S4 的提案打分会拿它当真钱证据。
    """
    from unittest.mock import patch

    from strategy_runtime.scheduler import StockStrategyScheduler

    _set("capital_a_share", "100000")
    row = type("R", (), {"strategy_id": "CS-L", "market": "a_share",
                         "mode": "live", "interval_minutes": 30,
                         "last_run_at": None})()
    sch = StockStrategyScheduler()
    with patch("strategy_runtime.scheduler.is_open", return_value=True), \
         patch("crypto_strategy.service.spec_from_row") as spec_from_row, \
         patch("strategy_runtime.executor.run_tick") as run_tick:
        spec_from_row.return_value = type("S", (), {"market": "a_share",
                                                    "mode": "live",
                                                    "interval_minutes": 30})()
        out = sch.tick_one(row)
    assert not run_tick.called, "没接券商却把 live 策略跑起来了"
    assert "真券商" in out["skipped"]


def test_the_runtime_gate_lets_paper_through(clean):
    """⛔ 这道闸只挡 live —— 挡了 paper 股票策略就彻底不跑了。"""
    from unittest.mock import patch

    from strategy_runtime.scheduler import StockStrategyScheduler

    _set("capital_a_share", "100000")
    row = type("R", (), {"strategy_id": "CS-P", "market": "a_share",
                         "mode": "paper", "interval_minutes": 30,
                         "last_run_at": None})()
    sch = StockStrategyScheduler()
    with patch("strategy_runtime.scheduler.is_open", return_value=True), \
         patch("crypto_strategy.service.spec_from_row") as spec_from_row, \
         patch.object(StockStrategyScheduler, "_adapter_for"), \
         patch("strategy_runtime.executor.run_tick",
               return_value={"decisions": [], "tally": {}}) as run_tick:
        spec_from_row.return_value = type("S", (), {"market": "a_share",
                                                    "mode": "paper",
                                                    "interval_minutes": 30})()
        sch.tick_one(row)
    assert run_tick.called, "纸面股票策略被这道闸挡住了"
