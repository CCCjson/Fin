"""macOS 原生系统通知。

优先用 terminal-notifier（`brew install terminal-notifier`）：支持 -open 点击跳转
一个 URL，参数走列表传给 subprocess，不用手动拼字符串转义。没装则降级回 osascript
`display notification`——能弹但点了只是关掉，没有点击跳转能力。

只在 backend 直接跑在本机 macOS 时生效；异常一律吞掉，通知失败不能影响主流程
（以后 backend 若挪到容器/云端，这里会静默跳过，不报错）。
"""
import os
import shutil
import subprocess

from loguru import logger


def _escape_applescript(s: str) -> str:
    """AppleScript 字符串字面量转义：先转义反斜杠，再转义双引号，防止内容里的引号
    break 出 `display notification "..."` 这段脚本（仅 osascript 兜底路径用得到，
    terminal-notifier 走列表参数不需要手动转义）。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _enabled() -> bool:
    return os.getenv("NEWS_MAC_NOTIFY_ENABLED", "true").strip().lower() not in ("0", "false", "off")


def notify(title: str, message: str, subtitle: str = "", sound: bool = True,
           open_url: str = "") -> None:
    """发一条 macOS 系统通知。非阻塞失败即忽略。

    macOS 横幅本身会截断过长文本（title 一行/message 两行左右，且截断点系统说了算，
    截哪不好看没法控制）——调用方应该已经把 message 截到合理长度再传进来，这里不
    重复截断。subtitle 适合放稳定的短标识（如股票代码），不会跟 message 一起被切。
    open_url：点击通知时用默认浏览器打开的地址（仅 terminal-notifier 支持，装了才生效）。
    """
    if not _enabled():
        return

    notifier = shutil.which("terminal-notifier")
    try:
        if notifier:
            args = [notifier, "-title", title, "-message", message]
            if subtitle:
                args += ["-subtitle", subtitle]
            if sound:
                args += ["-sound", "default"]
            if open_url:
                args += ["-open", open_url]
            subprocess.run(args, check=False, timeout=5, capture_output=True)
        else:
            script = (
                f'display notification "{_escape_applescript(message)}" '
                f'with title "{_escape_applescript(title)}"'
            )
            if subtitle:
                script += f' subtitle "{_escape_applescript(subtitle)}"'
            if sound:
                script += ' sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5,
                            capture_output=True)
    except Exception as e:  # noqa: BLE001 — 通知失败绝不影响主流程
        logger.debug(f"macOS 通知发送失败（忽略）: {e}")
