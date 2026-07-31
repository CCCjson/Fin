"""死代理 env 不许在 import 之后复活。

## 这个 bug 长什么样（2026-07-27~31 实测，代价是 4 天港美股没更新）

`api/main.py` 文件头调了 `apply_proxy_env()` 清掉残留死代理（Clash 关时
`.env` 里的 `HTTP_PROXY=http://127.0.0.1:7897` 指向一个没在监听的端口）。
但**紧接着的 `from api.routes import ...` 会把它踩回来** —— 那条 import 链
拉起 `acquisition/config.py`，那里有 `load_dotenv(override=True)`。

于是整个后端进程带着一个指向死端口的代理跑，所有读 env 的 HTTP 库
（yfinance 的 curl_cffi、requests、akshare…）默认都往那儿发。

**最坏的部分是它一点都不像代理问题**：yfinance 报的是
`'NoneType' object is not subscriptable`（内部 `data['chart']['result']`，
因为拿到了空响应），排查时全都往「Yahoo 限速 / 网络不通」方向想。

同一个机理在 `tests/conftest.py` 里以 DATABASE_URL 的形式记过一次
（那份文档也明写「这样的 `override` 全项目有六七处」）。

## 这套测试守什么

1. `load_dotenv(override=True)` 确实会让代理 env 复活（机理还在，别以为修好了）
2. `api/main.py` 的 startup 里必须**再调一次** `apply_proxy_env()`
3. `apply_proxy_env()` 在判定走代理时**不许**乱清（否则会打断 Clash 用户）
"""
import ast
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.baseline

_BACKEND = Path(__file__).resolve().parent.parent.parent
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


@pytest.fixture
def clean_proxy_env(monkeypatch):
    for k in _PROXY_KEYS + ("ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


class TestApplyProxyEnvBehaviour:
    def test_clears_dead_proxy_when_direct(self, clean_proxy_env, monkeypatch):
        """判直连时必须清掉残留代理 —— 否则读 env 的库全往死端口发。"""
        from net import env as net_env

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        monkeypatch.setattr(net_env, "resolve_proxy", lambda: None)

        net_env.apply_proxy_env()

        assert not any(os.environ.get(k) for k in _PROXY_KEYS), (
            "判定直连却没清掉死代理 —— yfinance/requests 会继续往死端口发"
        )

    def test_keeps_proxy_when_clash_alive(self, clean_proxy_env, monkeypatch):
        """判定要走代理时**不许**清 —— 清了会打断 Clash 用户的出网。"""
        from net import env as net_env

        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
        monkeypatch.setattr(net_env, "resolve_proxy", lambda: "http://127.0.0.1:7897")

        net_env.apply_proxy_env()

        assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:7897"


class TestLoadDotenvStillResurrects:
    def test_override_load_dotenv_brings_proxy_back(self, clean_proxy_env):
        """机理仍在：`load_dotenv(override=True)` 会把 .env 的代理踩回来。

        这条**不是**在测坏行为该被修掉 —— `override=True` 全项目六七处，改它们
        风险远大于收益。这条测试是**给未来的人看的**：别以为清一次就完事了，
        任何在 import 链之后才生效的清理都可能被下一次 override 抹掉。
        """
        from dotenv import load_dotenv

        env_file = _BACKEND / ".env"
        if not env_file.exists():
            pytest.skip("本机没有 .env")
        raw = env_file.read_text(encoding="utf-8", errors="ignore")
        if "HTTP_PROXY=" not in raw:
            pytest.skip(".env 里没有 HTTP_PROXY，无从复现")

        assert not os.environ.get("HTTP_PROXY")
        load_dotenv(env_file, override=True)
        assert os.environ.get("HTTP_PROXY"), (
            "机理消失了？如果 .env 真的删掉了 HTTP_PROXY，把这条测试也删掉"
        )


class TestMainCallsApplyTwice:
    def test_startup_reapplies_proxy_env(self):
        """`api/main.py` 必须调用 `apply_proxy_env` **两次**：
        一次在文件头（赶在 transformers import 之前），一次在 startup
        （收拾被 `load_dotenv(override=True)` 踩回来的）。
        """
        src = (_BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        module_level = 0
        in_startup = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("apply_proxy_env", "_reapply"):
                continue
            module_level += 1

        # startup_event 函数体内单独数一遍
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "startup_event":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        nm = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                        if nm in ("apply_proxy_env", "_reapply"):
                            in_startup += 1

        assert in_startup >= 1, (
            "startup_event 里没有再调一次 apply_proxy_env —— "
            "文件头那次会被 router import 链里的 load_dotenv(override=True) 抹掉，"
            "整个进程会带着死代理跑（实测让港美股连着 4 天没更新）"
        )
        assert module_level - in_startup >= 1, (
            "文件头的 apply_proxy_env 不见了 —— 那次必须赶在 transformers/"
            "huggingface_hub 首次 import 之前，否则 HF 校验会走死代理"
        )
