"""快代理提取响应的解析：真实寿命必须来自 API，不许硬编码猜。

## 事故

`.env` 的提取链接原本没带 `f_et=1`，响应里就没有过期时间，
`_parse_single_proxy` 于是给每个 IP 盖上 `_default_expire_at()` = **写死 5 分钟**。

实测真实寿命是 **180 秒**。也就是说每个 IP 的最后 2 分钟里 `is_expired` 都在撒谎，
请求拿着死 IP 出去，失败 → `switch_proxy()` 买新的。等于没复用，还多绕一圈。

## 两种响应格式

| 链接参数 | `proxy_list[0]` |
|---|---|
| `f_auth=1`（旧） | `"110.7.18.40:11745:d2572314887:vx7ikual"` |
| `f_auth=1&f_et=1`（现在） | `"58.217.206.125:16883,d2572314887:vx7ikual,180"` |

加了 `f_et` 之后字段改成**逗号分组**，旧的 `split(":")` 会把 `16883,d2572314887`
丢进 `int()` 里炸掉。两种都得吃下去。
"""
from datetime import datetime

import pytest

from net.proxy_manager import ProxyInfo, ProxyManager

pytestmark = pytest.mark.baseline


@pytest.fixture
def pm(monkeypatch):
    monkeypatch.setenv("kuaidaili_api", "https://dps.kdlapi.com/fake")
    return ProxyManager()


def _ttl_seconds(p: ProxyInfo) -> float:
    return (datetime.fromisoformat(p.expire_at) - datetime.now()).total_seconds()


# ── 新格式：ip:port,user:pass,ttl ──────────────────────────────────────────

def test_parses_f_et_format_with_real_ttl(pm):
    """真实抓包：`f_auth=1&f_et=1` 的响应。180 秒必须原样落到 expire_at。"""
    p = pm._parse_single_proxy("58.217.206.125:16883,d2572314887:vx7ikual,180")
    assert p is not None
    assert p.ip == "58.217.206.125"
    assert p.port == 16883
    assert p.username == "d2572314887"
    assert p.password == "vx7ikual"
    assert 170 <= _ttl_seconds(p) <= 180, "过期时间必须来自 API 的 180，不是硬编码的 300"


def test_f_et_without_auth(pm):
    """只开 f_et 不开 f_auth：`ip:port,ttl`。"""
    p = pm._parse_single_proxy("1.2.3.4:8080,60")
    assert p is not None and p.ip == "1.2.3.4" and p.port == 8080
    assert not p.username and not p.password
    assert 50 <= _ttl_seconds(p) <= 60


def test_real_ttl_beats_the_five_minute_guess(pm):
    """核心回归：短命 IP 不许被当成活 5 分钟的。"""
    short = pm._parse_single_proxy("1.2.3.4:8080,d2572314887:vx7ikual,180")
    guessed = pm._parse_single_proxy("1.2.3.4:8080:d2572314887:vx7ikual")   # 无 ttl，只能猜
    assert _ttl_seconds(short) < _ttl_seconds(guessed), \
        "API 给了 180 秒还按 300 秒用，最后 2 分钟全是死 IP"


# ── 旧格式：ip:port:user:pass（向后兼容，别的调用方/备用链接可能还没加 f_et）──

def test_parses_legacy_colon_format(pm):
    p = pm._parse_single_proxy("110.7.18.40:11745:d2572314887:vx7ikual")
    assert p is not None
    assert (p.ip, p.port, p.username, p.password) == ("110.7.18.40", 11745, "d2572314887", "vx7ikual")


def test_parses_bare_ip_port(pm):
    p = pm._parse_single_proxy("1.2.3.4:8080")
    assert p is not None and (p.ip, p.port) == ("1.2.3.4", 8080)
    assert not p.username


# ── 脏数据不许把整条链路炸掉 ────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "garbage", "1.2.3.4", "1.2.3.4:notaport", "1.2.3.4:8080,notattl,x"])
def test_malformed_returns_none_not_raises(pm, bad):
    """解析失败要返回 None（上游按「取不到 IP」处理），不是抛 ValueError 炸穿。"""
    assert pm._parse_single_proxy(bad) is None


# ── 余额：order_left_count 必须被读出来 ─────────────────────────────────────

def test_captures_order_left_count(pm):
    """快代理每次提取都在响应里报余额——这是唯一的余额真源，不能丢。"""
    pm._parse_response({
        "code": 0, "msg": "success",
        "data": {"proxy_list": ["1.2.3.4:8080,60"], "count": 1, "order_left_count": 9956},
    })
    assert pm.order_left_count == 9956


def test_order_left_count_is_none_before_any_fetch(pm):
    assert pm.order_left_count is None


# ── dict 格式（别的快代理套餐可能返回结构化 JSON）───────────────────────────

def test_dict_item_expire_time_still_works(pm):
    p = pm._parse_single_proxy({"ip": "1.2.3.4", "port": 8080, "expire_time": "90"})
    assert p is not None and 80 <= _ttl_seconds(p) <= 90
