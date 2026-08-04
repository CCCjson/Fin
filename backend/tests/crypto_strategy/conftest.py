"""竞技场那批测试的共同前提：**各市场本金已配置**。

🔒 2026-08-04 起「未配置本金 = 该市场 fail-closed」（Jason 拍板，见 `DECISIONS.md`）。
于是「算收益率」这件事有了一个新前提：分母得先存在。不配的话
`daily_returns` 一律回 `basis="none"`，这批测试就全在测 fail-closed 那一条路径。

⚠️ 值取 5000 是**刻意对齐旧的全局默认**：这样既有那些断绝对金额/比例的用例
数字不变，红的就只会是真的行为变化。
⛔ 「未配置」本身的行为**不在这里测**，它在 `tests/test_market_capital.py`
（那边有股票调度器 / crypto 引擎 / 收益率分母三条 fail-closed 用例）。
这个 fixture 每条用例后都清回未配置，别让它污染别的文件。
"""
import pytest

from data_engine.storage.models import UserSettings

# ⚠️ 借 adapter 自己那个**模块级绑定**来播种，保证与它读的是同一个库
# （`from ...database import get_session` 在别处被 monkeypatch 换掉时，
#  两边会指向不同的库 —— 表现就是「播了种还是 fail-closed」）。
from trading_engine.risk.adapter import get_session

_SEED = {"capital_crypto": "5000", "capital_a_share": "5000",
         "capital_us_stock": "5000", "capital_hk_stock": "5000"}


@pytest.fixture(autouse=True)
def _capitals_configured():
    s = get_session()
    try:
        for k, v in _SEED.items():
            row = s.query(UserSettings).filter(UserSettings.key == k).first()
            if row is None:
                s.add(UserSettings(key=k, value=v))
            else:
                row.value = v
        s.commit()
    finally:
        s.close()

    yield

    s = get_session()
    try:
        for k in _SEED:
            row = s.query(UserSettings).filter(UserSettings.key == k).first()
            if row is not None:
                row.value = ""      # 回到「未配置」，别污染别的文件
        s.commit()
    finally:
        s.close()
