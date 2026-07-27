"""全套测试的**生产库隔离**（P0-4）。

# 为什么要有这个文件

2026-07-27 查生产 `data/market.db` 发现 `decision_logs` 里有 40 行假数据：
`BTCUSDT.BN BUY entry_price=100.0 order=BTCUSDT.BN:999`。那个 order_id 只存在于
`tests/test_crypto_strategy_engine.py` 的 `_FakeOrder` —— **是测试写进生产库的**。

泄漏机制（老病，但方向反了一半）：

    mem_db fixture patch 的是 `data_engine.storage.database.get_session`。
    ├─ record_crypto_trade ① 写成交台账：**函数内延迟 import** → 吃到 patch → 内存库 ✅
    └─ record_crypto_trade ③ 决策留痕：走 decision_log.record_decision，而
       decision_log.py 顶部是 `from ... import get_session`（**模块级绑定**）
       → patch 打不到 → **直写生产 market.db** 💥

全项目有 20+ 个模块是同款模块级绑定，靠「每个测试记得 patch 对地方」是堵不住的 ——
少一个就是一次静默污染，而且要等几个月后查库才会发现。

# 怎么堵：**直接换掉 engine，不跟环境变量较劲**

试过「设 `DATABASE_URL` 再 import」，**不行**：`.env` 里有
`DATABASE_URL=sqlite:///./data/market.db`，而 `import data_engine.storage.database`
会先跑 `data_engine/__init__.py` → `DataEngine` → `acquisition/config.py` 的
`load_dotenv(override=True)`（这样的 override 全项目有六七处）→ 环境变量被踩回
生产库，**然后** database.py 才读它。实测：只设环境变量，跑完全套后生产库照样多一行。

所以这里改成事后**替换模块级的 `engine` / `SessionLocal`**：

- `get_session()` 的实现是 `return SessionLocal()`，**调用时**才读模块全局 ——
  所以 20+ 个 `from ... import get_session` 的模块级绑定会**一起**改到临时库，
  这正是环境变量方案堵不住的那一片。
- engine 一旦替换就与 `os.environ` 脱钩，后面谁再 `load_dotenv(override=True)`
  都追不回来。

# 逃生门

`FIN_TEST_USE_REAL_DB=1` 时整体跳过 —— 给 `-m integration` 那批「依赖真实库」的测试用
（它们被 `addopts = -m 'not network and not integration'` 默认排除，正常跑不到）。
"""
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.orm import scoped_session

from common.db import make_session_factory, make_sqlite_engine
from data_engine.storage import database as _db

_USE_REAL_DB = os.getenv("FIN_TEST_USE_REAL_DB") == "1"
PROD_DB = Path(__file__).resolve().parent.parent / "data" / "market.db"

if not _USE_REAL_DB:
    _TEST_DB_PATH = Path(tempfile.mkdtemp(prefix="fin-test-db-")) / "test_market.db"
    _TEST_DB_URL = f"sqlite:///{_TEST_DB_PATH}"
    os.environ["DATABASE_URL"] = _TEST_DB_URL      # 让晚到的 import 也看到同一个值
    _db.DATABASE_URL = _TEST_DB_URL
    _db.engine = make_sqlite_engine(_TEST_DB_URL)
    _db.SessionLocal = make_session_factory(_db.engine)
    _db.ScopedSession = scoped_session(_db.SessionLocal)
    # 漏网的写入落进一张空的、结构完整的库：测试照常跑，生产库一个字节不动。
    _db.Base.metadata.create_all(bind=_db.engine)


def _db_file_of(engine) -> Path:
    """engine 的 sqlite 文件绝对路径。

    ⚠️ **必须 resolve 后比**：`.env` 里写的是相对路径 `sqlite:///./data/market.db`，
    拿绝对路径去做子串匹配**永远匹配不上** —— 第一版门禁就是这么在泄漏正在发生的
    同时显示全绿的。
    """
    return Path(str(engine.url.database or "")).resolve()


@pytest.fixture(scope="session", autouse=True)
def _isolate_production_db():
    """兜底断言：整场测试的 engine 绝不能指向生产库。"""
    if not _USE_REAL_DB:
        from data_engine.storage.database import engine
        assert _db_file_of(engine) != PROD_DB.resolve(), (
            f"测试用的 engine 指向了生产库！{engine.url}\n"
            f"conftest 顶部的 engine 替换被谁覆盖了？"
        )
    yield
