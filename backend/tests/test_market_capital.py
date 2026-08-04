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
