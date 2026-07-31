"""🔴 回测**绝不能用模拟数据顶替缺失的真实行情**。

## 这条门禁在防什么

C++ 的 `/api/backtest/run` 在**没收到 bars 时会自己造一段随机游走**
（`DataLoader::generate_sample_data`）。造假数据本身是正当的（demo/自测要用），
真正的问题在调用方：`api/routes/backtest_cpp.py` 从前的写法是

    except Exception as e:
        logger.warning(f"从 DataEngine 获取数据失败: {e}, 将使用模拟数据")
    # ↑ 然后**照样**把不带 bars 的请求发过去

于是 Jason 回测 `600519.SH`、而库里恰好没这段数据时，拿到的是
**一份贴着 600519.SH 标签的随机数回测结果，HTTP 200，页面上一切正常**。
夏普、胜率、最大回撤全都煞有介事 —— 而它们描述的是一条伪随机游走。

⛔ 别把这条改回「取不到就继续」。回测数字是要拿来做真钱决策的。
"""
import pytest
from fastapi import HTTPException

from api.routes import backtest_cpp as bt


class _FakeDF:
    def __init__(self, empty: bool):
        self.empty = empty

    def iterrows(self):
        return iter(())


def _patch_engine(monkeypatch, *, df=None, boom: Exception | None = None):
    """替换 `_load_bars_or_400` 内部那次 DataEngine 取数。"""
    class _DE:
        def get_daily_data(self, symbol, start_date=None, end_date=None):
            if boom is not None:
                raise boom
            return df

    import data_engine
    monkeypatch.setattr(data_engine, "DataEngine", lambda: _DE())


@pytest.mark.asyncio
async def test_empty_dataframe_becomes_400_not_fake_bars(monkeypatch):
    """库里没这段数据 → 400，而不是「不传 bars 让 C++ 造一段」。"""
    _patch_engine(monkeypatch, df=_FakeDF(empty=True))

    with pytest.raises(HTTPException) as e:
        await bt._load_bars_or_400("600519.SH", "2024-01-01", "2024-06-30",
                                   label="test")
    assert e.value.status_code == 400
    assert "600519.SH" in str(e.value.detail)
    # ⭐ 人话必须点明「不会用模拟数据顶替」——否则调用方仍会以为是偶发失败
    assert "模拟数据" in str(e.value.detail)


@pytest.mark.asyncio
async def test_datasource_exception_becomes_400_with_the_reason(monkeypatch):
    """取数抛异常也一样 —— 而且原因要原样带给调用方，别只留在日志里。"""
    _patch_engine(monkeypatch, boom=RuntimeError("数据库锁住了"))

    with pytest.raises(HTTPException) as e:
        await bt._load_bars_or_400("AAPL", "", "", label="test")
    assert e.value.status_code == 400
    assert "数据库锁住了" in str(e.value.detail)


@pytest.mark.asyncio
async def test_none_dataframe_is_also_rejected(monkeypatch):
    """`get_daily_data` 返回 None 与返回空表是同一件事，不能只挡一种。"""
    _patch_engine(monkeypatch, df=None)

    with pytest.raises(HTTPException) as e:
        await bt._load_bars_or_400("00700.HK", "", "", label="test")
    assert e.value.status_code == 400


def test_no_logger_line_announces_falling_back_to_fake_data():
    """🔒 结构性门禁：不许有「记一条 warning 然后继续用假数据」的日志。

    那句 `logger.warning(f"...将使用模拟数据")` 当年是实现的忠实描述 ——
    它出现在哪儿，哪儿就有一条静默降级的路。

    ⚠️ 只盯 `logger.*(...)` 调用行，**不盯注释和 docstring** ——
    文档里引用这段历史是有价值的，不该被门禁误伤。
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(bt))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == "logger"):
            continue
        text = " ".join(ast.dump(a) for a in node.args)
        assert "模拟数据" not in text, (
            f"第 {node.lineno} 行还在记「将使用模拟数据」—— "
            f"说明那条静默降级的路还开着")


def test_pairs_second_leg_is_not_silently_skipped():
    """🔴 配对交易的第二条腿拿不到数据时，**不许静默跳过**。

    旧写法是 `except Exception: logger.warning(...)` 然后继续 —— 于是
    `PairsStrategy` 拿到空的 bars2，算不出 z-score，**一单不下**地跑完，
    返回一份「这个配对没有机会」的结论。那句结论是假的：它根本没比过。
    """
    import inspect
    src = inspect.getsource(bt.run_and_save)
    assert "获取配对股票数据失败" not in src, "第二条腿的静默跳过还在"
    # 两条腿都必须走同一个「取不到就 400」的入口
    assert src.count("_load_bars_or_400") >= 2
