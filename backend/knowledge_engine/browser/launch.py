"""
浏览器地基 — Playwright 启动封装（反检测 + 持久化会话 + 智能升级有头）。

蓝本：worldcup2026/scrapers/sofascore_hybrid.py 的 `_start` / `_pass_cloudflare`，
抽成站点无关 + 加两件事：
  1. launch_persistent_context 持久化 user-data-dir（过一次挑战后 cookie 落盘复用）
  2. 智能升级：headless 检测到挑战 → 重启有头让 Jason 手动过 → cookie 落盘

★ 并发约束：sync Playwright 不能在 asyncio event-loop 线程里跑（会抛
  "sync API inside asyncio loop"）。所有浏览器操作必须经 run_off_loop() 派发到
  独立 worker 线程执行——工具层（tools.py）在边界处包一次即可。
"""
import concurrent.futures
import os
import time
from typing import Any, Callable, Optional

from loguru import logger

from knowledge_engine.config import (
    get_playwright_channel,
    get_playwright_headed_on_challenge,
    get_playwright_timeout,
    get_playwright_user_data_dir,
)
from knowledge_engine.browser import sessions

# 反检测启动参数（各项目通用，藏 navigator.webdriver 等自动化特征）
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
STEALTH_INIT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 站点挑战/盾的 title 标志词（命中即认为在挑战页）
CHALLENGE_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verifying you are human",
    "请稍候",
    "安全验证",
)
# Cloudflare turnstile / 挑战 iframe
CHALLENGE_IFRAME_SEL = "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']"


def run_off_loop(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在无 asyncio 事件循环的独立线程里执行 fn，阻塞取结果。

    sync Playwright 在有运行中事件循环的线程会直接抛错；工具被 agent executor
    以 sync 调用、但可能处于 async 上下文，故统一派发到干净线程执行。
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: fn(*args, **kwargs)).result()


def looks_like_challenge(page) -> bool:
    """判断当前页是否卡在站点挑战/盾。"""
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    if any(m in title for m in CHALLENGE_TITLE_MARKERS):
        return True
    try:
        if page.query_selector(CHALLENGE_IFRAME_SEL):
            return True
    except Exception:
        pass
    return False


class BrowserSession:
    """持久化上下文会话，支持 goto 时智能升级有头过挑战。

    用法（务必在 run_off_loop 内使用）：
        with BrowserSession(proxy=...) as sess:
            page = sess.goto(url)          # 自动过盾（必要时升级有头）
            ... page.on("request") / page.evaluate ...
    """

    def __init__(
        self,
        proxy: Optional[dict] = None,
        headless: bool = True,
        timeout_ms: Optional[int] = None,
        user_data_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        self.proxy = proxy                       # Playwright 格式 {server, username?, password?}
        self.headless = headless
        self.timeout_ms = timeout_ms or get_playwright_timeout()
        # 显式传 user_data_dir 优先；否则按 session_id 分配独立 profile 子目录
        # （并发场景下多个会话不再共享同一个 profile，避免 Chromium 的 profile 锁互相打架）。
        self.session_id = session_id or sessions.new_session_id()
        self.user_data_dir = user_data_dir or sessions.profile_dir(self.session_id)
        self.channel = get_playwright_channel()
        self._pw = None
        self._context = None
        self._page = None

    def __enter__(self):
        self._start(self.headless)
        return self

    def __exit__(self, *exc):
        self.close()

    def _launch_context(self, headless: bool):
        os.makedirs(self.user_data_dir, exist_ok=True)
        kwargs = dict(
            user_data_dir=self.user_data_dir,
            headless=headless,
            args=LAUNCH_ARGS,
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        if self.proxy:
            kwargs["proxy"] = self.proxy
        # 并发闸门：真正拉起 Chromium 进程前必须先拿到（限制同时存活的浏览器进程数）
        with sessions.gate():
            # channel=chrome 用系统真实 Chrome（反检测更强）；失败回退内置 chromium
            try:
                ctx = self._pw.chromium.launch_persistent_context(channel=self.channel, **kwargs)
            except Exception as e:
                logger.warning(f"channel={self.channel} 启动失败，回退内置 chromium：{str(e)[:100]}")
                ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        ctx.add_init_script(STEALTH_INIT)
        return ctx

    def _start(self, headless: bool):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._context = self._launch_context(headless)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def _relaunch_headed(self):
        """关掉 headless、以有头重开（复用同一 user-data-dir）。"""
        logger.info("升级有头浏览器，请在弹出的窗口里手动过挑战…")
        try:
            self._context.close()
        except Exception:
            pass
        self.headless = False
        self._context = self._launch_context(headless=False)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def goto(self, url: str, wait_until: str = "domcontentloaded"):
        """打开 url，过站点挑战（必要时升级有头等人工过）。返回 page。"""
        page = self._page
        page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)
        if not self._wait_challenge(page):
            # headless 没过 + 允许升级有头 → 重开有头，人工过（最长 180s）
            if self.headless and get_playwright_headed_on_challenge():
                self._relaunch_headed()
                page = self._page
                page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)
                self._wait_challenge(page, max_wait=180)
            else:
                logger.warning(f"挑战可能未通过：{url}")
        return page

    def _wait_challenge(self, page, max_wait: int = 15) -> bool:
        """轮询等待挑战通过。通过/本就无挑战返回 True，超时返回 False。"""
        for _ in range(max_wait):
            if not looks_like_challenge(page):
                return True
            time.sleep(1)
        return not looks_like_challenge(page)

    @property
    def page(self):
        return self._page

    def close(self):
        for obj, closer in (
            (self._context, "close"),
            (self._pw, "stop"),
        ):
            if obj is None:
                continue
            try:
                getattr(obj, closer)()
            except Exception as e:
                logger.debug(f"关闭浏览器资源失败：{str(e)[:80]}")
