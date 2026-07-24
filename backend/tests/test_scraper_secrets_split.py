"""站点配置的凭据剥离 —— 防止 cookie 被提交进版本库。

逆向侦查的成果（端点 / gate 头 / 字段样例）该跟着代码走版本控制；
但 cookie 是登录态（CapitalIQ 付费订阅会话、雪球 xq_a_token），一旦进了 git
历史就洗不掉。registry 在读写时做剥离，`<domain>.secrets.json` 单独 gitignore。

这条测试守着那道边界：save 之后主配置里绝不能出现 cookie。
"""
import json
import os
import pathlib

import pytest

from acquisition.crawler import sites as registry


@pytest.fixture()
def cfg_dir(tmp_path, monkeypatch) -> pathlib.Path:
    monkeypatch.setattr(registry, "get_scrapers_config_dir", lambda: str(tmp_path))
    return tmp_path


SAMPLE = {
    "domain": "example.com",
    "page_url": "https://example.com/stock",
    "cookies": "session=SUPER_SECRET_TOKEN; uid=42",
    "cookies_updated_at": "2026-07-09T18:00:00",
    "endpoints": [
        {"name": "quote", "url": "https://example.com/api/quote", "method": "GET",
         "gate_headers": {"referer": "https://example.com/"}, "is_json": True,
         "status": 200, "sample_fields": ["code", "price"]},
    ],
}


def test_cookies_never_written_to_main_config(cfg_dir):
    registry.save_site_config(dict(SAMPLE))

    main = (cfg_dir / "example.com.json").read_text()
    assert "SUPER_SECRET_TOKEN" not in main
    assert "cookies" not in json.loads(main)
    assert "cookies_updated_at" not in json.loads(main)


def test_cookies_written_to_secrets_sidecar(cfg_dir):
    registry.save_site_config(dict(SAMPLE))

    secrets_path = cfg_dir / "example.com.secrets.json"
    assert secrets_path.exists()
    secrets = json.loads(secrets_path.read_text())
    assert secrets["cookies"] == SAMPLE["cookies"]
    assert secrets["cookies_updated_at"] == SAMPLE["cookies_updated_at"]


def test_secrets_file_is_owner_only(cfg_dir):
    registry.save_site_config(dict(SAMPLE))
    mode = os.stat(cfg_dir / "example.com.secrets.json").st_mode
    assert oct(mode)[-3:] == "600"


def test_load_merges_secrets_back_transparently(cfg_dir):
    """上游一律读 cfg['cookies']，剥离必须对它们无感。"""
    registry.save_site_config(dict(SAMPLE))

    loaded = registry.load_site_config("example.com")
    assert loaded["cookies"] == SAMPLE["cookies"]
    assert loaded["endpoints"][0]["name"] == "quote"


def test_load_works_without_secrets_file(cfg_dir):
    """没有 cookie 的站点（如 mguba）不该因为缺 secrets 文件而挂。"""
    cfg = {k: v for k, v in SAMPLE.items() if k not in ("cookies", "cookies_updated_at")}
    registry.save_site_config(cfg)

    assert not (cfg_dir / "example.com.secrets.json").exists()
    loaded = registry.load_site_config("example.com")
    assert loaded.get("cookies", "") == ""
    assert loaded["endpoints"][0]["name"] == "quote"


def test_suffix_match_also_merges_secrets(cfg_dir):
    """按主域名后缀命中子域配置时，也要把该子域的 cookie 带出来。"""
    cfg = dict(SAMPLE, domain="api.example.com")
    registry.save_site_config(cfg)

    loaded = registry.load_site_config("example.com")
    assert loaded["domain"] == "api.example.com"
    assert loaded["cookies"] == SAMPLE["cookies"]


def test_secrets_sidecar_is_not_mistaken_for_a_site_config(cfg_dir):
    """后缀扫描必须跳过 *.secrets.json，否则会把凭据文件当成站点配置读。"""
    registry.save_site_config(dict(SAMPLE))

    loaded = registry.load_site_config("com")
    assert loaded is None or loaded.get("domain") == "example.com"


def test_resave_does_not_leak_cookies_back_into_main(cfg_dir):
    """load → 改 → save 的往返：cookie 被合并进 cfg 后，再存必须重新剥离。"""
    registry.save_site_config(dict(SAMPLE))
    loaded = registry.load_site_config("example.com")
    loaded["endpoints"][0]["status"] = 403
    registry.save_site_config(loaded)

    main = (cfg_dir / "example.com.json").read_text()
    assert "SUPER_SECRET_TOKEN" not in main
    assert json.loads((cfg_dir / "example.com.secrets.json").read_text())["cookies"] == SAMPLE["cookies"]


def test_real_configs_contain_no_cookies():
    """兜底：仓库里真实的站点配置主文件不许含 cookie 键。"""
    real_dir = pathlib.Path(__file__).resolve().parents[1] / "configs" / "scrapers"
    if not real_dir.is_dir():
        pytest.skip("没有站点配置目录")

    leaked = []
    for f in real_dir.glob("*.json"):
        if f.name.endswith(".secrets.json"):
            continue
        cfg = json.loads(f.read_text())
        if "cookies" in cfg:
            leaked.append(f.name)
        eps = cfg.get("endpoints")
        if isinstance(eps, dict) and "cookies" in eps:   # gbapi 的嵌套结构 bug
            leaked.append(f"{f.name}(nested)")
    assert not leaked, f"这些站点配置仍含 cookie，不能进版本库: {leaked}"


# ── 端点内部的令牌（2026-07-24 补：只剥顶层 cookies 漏了这条路）──────────────

# 实锤原型：侦查 finance.yahoo.com 抓到的端点，URL 查询串带着会话令牌
# `?.crumb=FBRGbvClsEA`，`default_params` 里又存了一份，一路进了版本库。
TOKEN_SAMPLE = {
    "domain": "tokensite.com",
    "page_url": "https://tokensite.com/quote",
    "endpoints": [
        {
            "name": "chart",
            "url": "https://tokensite.com/api/chart?symbol=GC%3DF&.crumb=CRUMB_LEAK_1",
            "url_base": "https://tokensite.com/api/chart",
            "default_params": {"symbol": "GC=F", ".crumb": "CRUMB_LEAK_1"},
            "gate_headers": {"referer": "https://tokensite.com/",
                             "authorization": "Bearer BEARER_LEAK_2"},
            "method": "GET", "is_json": True, "status": 200,
        },
    ],
}


def test_endpoint_tokens_never_written_to_main_config(cfg_dir):
    """URL 查询参数 / default_params / gate_headers 三处的令牌都不许进主配置。"""
    registry.save_site_config(dict(TOKEN_SAMPLE))

    main = (cfg_dir / "tokensite.com.json").read_text()
    assert "CRUMB_LEAK_1" not in main, "URL/参数里的会话令牌漏进主配置了"
    assert "BEARER_LEAK_2" not in main, "gate_headers 里的鉴权头漏进主配置了"


def test_endpoint_tokens_go_to_secrets_sidecar(cfg_dir):
    registry.save_site_config(dict(TOKEN_SAMPLE))

    secrets = json.loads((cfg_dir / "tokensite.com.secrets.json").read_text())
    assert "endpoint_secrets" in secrets
    stash = secrets["endpoint_secrets"]["chart"]
    assert stash["default_params"][".crumb"] == "CRUMB_LEAK_1"
    assert stash["gate_headers"]["authorization"] == "Bearer BEARER_LEAK_2"


def test_endpoint_tokens_restored_on_load(cfg_dir):
    """剥离必须对上游无感：load 回来的 cfg 要跟存进去的一模一样。"""
    registry.save_site_config(dict(TOKEN_SAMPLE))

    ep = registry.load_site_config("tokensite.com")["endpoints"][0]
    src = TOKEN_SAMPLE["endpoints"][0]
    assert ep["url"] == src["url"]
    assert ep["default_params"] == src["default_params"]
    assert ep["gate_headers"] == src["gate_headers"]


def test_non_credential_params_stay_in_main_config(cfg_dir):
    """别误伤：普通业务参数（symbol / range 之类）必须留在主配置里，那是逆向成果。"""
    registry.save_site_config(dict(TOKEN_SAMPLE))

    main = json.loads((cfg_dir / "tokensite.com.json").read_text())
    ep = main["endpoints"][0]
    assert ep["default_params"]["symbol"] == "GC=F"
    assert "symbol=GC" in ep["url"]
    assert ep["gate_headers"]["referer"] == "https://tokensite.com/"


def test_endpoints_without_tokens_produce_no_sidecar(cfg_dir):
    """没令牌的站点不该凭空多出一个 secrets 文件（东财那些就是这种）。"""
    clean = {k: v for k, v in SAMPLE.items() if k not in ("cookies", "cookies_updated_at")}
    registry.save_site_config(clean)
    assert not (cfg_dir / "example.com.secrets.json").exists()
