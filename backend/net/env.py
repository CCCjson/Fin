"""
进程代理 env 的管理：启动期同步 + 运行期临时覆盖。

- apply_proxy_env()：启动时调一次（在 transformers/huggingface 首次 import 之前），
  direct 模式（Clash 关）时清掉残留死代理变量，让读 env 代理的库（akshare、
  huggingface_hub、requests…）也能直连。
- proxy_env(url)：上下文管理器，临时把 HTTP(S)_PROXY 设为 url（None 则清空），
  退出时**精确恢复**原值。给 domestic_akshare 注入/清除代理用。
"""
import os
from collections.abc import Iterator
from contextlib import contextmanager

from net.clash import resolve_proxy

_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy")


def apply_proxy_env() -> str | None:
    """把进程的代理 env 同步到解析模式：direct 模式清掉残留死代理变量。

    必须在 transformers/huggingface_hub **首次 import 之前**调用才能根治
    「HF 校验走死代理→http client 坏掉」的坑。返回生效的 proxy。
    """
    proxy = resolve_proxy()
    if proxy is None:                      # 直连：清掉死代理（Clash 关但 env 残留 7897 会害人）
        for v in _PROXY_ENV_VARS:
            os.environ.pop(v, None)
    return proxy


@contextmanager
def proxy_env(url: str | None) -> Iterator[None]:
    """临时设置 HTTP(S)_PROXY 为 url（None=清空直连），退出时精确恢复原值。

    仅覆盖 http/https（大小写两种）；ALL_PROXY 不动。用于给自己读 env 代理的库
    （akshare）注入快代理 IP 或强制直连。注意：改的是进程全局 env，调用方需串行化。
    """
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            if url:
                os.environ[k] = url
            else:
                os.environ.pop(k, None)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
