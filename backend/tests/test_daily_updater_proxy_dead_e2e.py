"""
DailyUpdater 慢路径端到端模拟：代理连接持续失败（发 IP 但连不上）场景。

背景：2026-07-07 体检实锤复现——快代理 API 正常发 IP 但 IP 全连不上时，
旧实现会让每个 worker 无限换 IP 狂烧配额，进度长时间冻结后被 180s 看门狗
误杀（成功 47 / 失败 6547 这种数量级）。这个测试完整驱动一次
`DailyUpdater.update_stream()`（不经真实网络/真实 DB，只替换爬虫和代理
管理器），验证修复后的行为：
- 会持续产出心跳/进度事件（不会静默冻结）
- 熔断状态会走到 open 再到 dead
- 最终以 aborted=True, abort_reason="proxy_pool_dead" 收尾（而不是干等
  180s 后一次性判死）
- success + failed 与总待处理数守恒
"""

import pytest

pytestmark = pytest.mark.network
import json
import os
import sys
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import StockInfo  # noqa: E402
from data_engine import daily_updater as du_mod  # noqa: E402
from net.proxy_manager import ProxyInfo  # noqa: E402


N_STOCKS = 15


@pytest.fixture()
def isolated_db(monkeypatch):
    """内存 SQLite，跟真实 data/market.db 完全隔离（同 test_daily_coverage.py 的手法）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(du_mod, "get_session", lambda: Session())
    return Session


def _seed_stocks(session, n: int) -> None:
    for i in range(n):
        session.add(StockInfo(
            symbol=f"60{i:04d}.SH", name=f"测试股{i}", market="a_share",
            stock_type="stock", exchange="SH", is_active=1,
        ))
    session.commit()


class _FakeProxyManager:
    """模拟「快代理 API 正常发 IP」——但拿到的 IP 全部连不上（由 FakeCrawler 体现）。"""

    def __init__(self) -> None:
        self.api_url = "http://fake-kuaidaili.example/getdps"
        self.fetch_count = 0

    def fetch_one_proxy(self):
        self.fetch_count += 1
        return ProxyInfo(
            ip=f"10.0.0.{self.fetch_count % 250}", port=8080,
            expire_at=(du_mod.datetime.now() + timedelta(minutes=5)).isoformat(),
        )

    def switch_proxy(self):
        return self.fetch_one_proxy()


class _FakeCrawler:
    """模拟东财爬虫：warm_up/reset_session 是空操作，fetch_stock_history 恒定抛
    「代理连接失败」异常（不管走不走代理，模拟 IP 全连不上 + 直连也被掐的现实）。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def _warm_up(self, proxies=None) -> None:
        pass

    def reset_session(self, proxies=None) -> None:
        pass

    def fetch_stock_history(self, code, start_date, end_date, proxies=None, secid_market=None):
        # 真实网络连接失败也要花点时间（TCP RST/超时），给主循环留出机会在
        # 熔断彻底 dead 之前先观察到几个 retry activity 事件——纯瞬时失败会让
        # 熔断在主循环第一次检查前就已经 dead，测不出「重试可见性」这条修复点
        time.sleep(0.02)
        raise du_mod.ProxyTimeoutError(
            "连接异常: ProxyError('Unable to connect to proxy', "
            "RemoteDisconnected('Remote end closed connection without response'))"
        )


def test_slow_path_aborts_cleanly_when_proxy_pool_goes_dead(isolated_db, monkeypatch):
    monkeypatch.setattr(du_mod, "ProxyManager", _FakeProxyManager)
    monkeypatch.setattr(du_mod, "EastMoneyCrawler", _FakeCrawler)
    monkeypatch.setenv("DAILY_UPDATE_WORKERS", "2")

    session = isolated_db()
    _seed_stocks(session, N_STOCKS)
    session.close()

    updater = du_mod.DailyUpdater()
    events = [json.loads(line) for line in updater.update_stream()]

    progress_events = [e for e in events if e["event"] == "progress"]
    complete_events = [e for e in events if e["event"] == "complete"]
    assert len(complete_events) == 1
    complete = complete_events[0]

    # 核心回归点 1：期间必须持续产出事件（不会静默冻结）——至少要有心跳/进度帧
    assert len(progress_events) > 0

    # 核心回归点 2：熔断状态可见，且真的走到过 open（乃至 dead）
    proxy_states_seen = {e.get("proxy_state") for e in progress_events if "proxy_state" in e}
    assert "open" in proxy_states_seen or "dead" in proxy_states_seen

    # 核心回归点 3：以 aborted 收尾，原因是代理池彻底不可用，而不是傻等 180s
    # 静默超时才发现（proxy_pool_dead 应该在几秒内就被判定）
    assert complete["aborted"] is True
    assert complete["abort_reason"] == "proxy_pool_dead"

    # 核心回归点 4：计数守恒——success+failed 覆盖了本次待处理的全部股票，
    # 不会有「悄悄消失」的股票
    assert complete["success"] + complete["failed"] == N_STOCKS

    # 核心回归点 5：曾经重试过（retries 计数被驱动），证明失败重入队没有
    # 被误判成「零活动」
    retries_seen = [e.get("retries", 0) for e in progress_events]
    assert max(retries_seen) > 0


class _HealthyCrawler:
    """模拟代理/网络一切正常：每只都直接成功，返回空 K 线（无新数据但不算失败）。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def _warm_up(self, proxies=None) -> None:
        pass

    def reset_session(self, proxies=None) -> None:
        pass

    def fetch_stock_history(self, code, start_date, end_date, proxies=None, secid_market=None):
        return None  # 无数据（正常情况，如当天已是最新）


def test_slow_path_completes_normally_when_proxies_healthy(isolated_db, monkeypatch):
    """健康路径回归：熔断器/心跳改造不该影响一切正常时的完成路径。"""
    monkeypatch.setattr(du_mod, "ProxyManager", _FakeProxyManager)
    monkeypatch.setattr(du_mod, "EastMoneyCrawler", _HealthyCrawler)
    monkeypatch.setenv("DAILY_UPDATE_WORKERS", "2")

    session = isolated_db()
    _seed_stocks(session, N_STOCKS)
    session.close()

    updater = du_mod.DailyUpdater()
    events = [json.loads(line) for line in updater.update_stream()]

    complete_events = [e for e in events if e["event"] == "complete"]
    assert len(complete_events) == 1
    complete = complete_events[0]

    assert complete["aborted"] is False
    assert complete["abort_reason"] is None
    assert complete["success"] == N_STOCKS
    assert complete["failed"] == 0
