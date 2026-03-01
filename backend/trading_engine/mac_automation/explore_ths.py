"""
Phase 1 探路脚本 — 探索同花顺 Mac 客户端的 Accessibility 控件树
用法: conda run -n quant python -m trading_engine.mac_automation.explore_ths
"""
import subprocess
import sys
import time
from typing import Optional

# ── 1. 先用 AppleScript 做基础检测 ──────────────────────────────

def run_applescript(script: str) -> str:
    """执行 AppleScript 并返回输出"""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def check_ths_running() -> bool:
    """检测同花顺是否运行"""
    script = '''
    tell application "System Events"
        set appList to name of every process
    end tell
    return appList
    '''
    result = run_applescript(script)
    return "同花顺" in result


def get_ths_windows() -> str:
    """获取同花顺的窗口列表"""
    script = '''
    tell application "System Events"
        tell process "同花顺"
            set windowNames to name of every window
        end tell
    end tell
    return windowNames
    '''
    return run_applescript(script)


def activate_ths():
    """激活同花顺窗口"""
    script = '''
    tell application "同花顺" to activate
    delay 0.5
    '''
    run_applescript(script)


# ── 2. 用 Accessibility API 探索控件树 ─────────────────────────

def explore_accessibility_tree():
    """使用 pyobjc Accessibility API 探索同花顺控件树"""
    try:
        import AppKit
        import ApplicationServices as AS
    except ImportError:
        print("[ERROR] pyobjc 未安装，请运行: pip install pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices")
        return

    # 检查辅助功能权限
    trusted = AS.AXIsProcessTrusted()
    print(f"\n{'='*60}")
    print(f"辅助功能权限: {'✅ 已授权' if trusted else '❌ 未授权'}")
    print(f"{'='*60}")

    if not trusted:
        print("\n⚠️  需要授权辅助功能权限！")
        print("   步骤: 系统设置 → 隐私与安全性 → 辅助功能")
        print("   添加你的终端应用 (Terminal / iTerm2 / VS Code)")
        print("\n   尝试自动弹出授权提示...")
        # 尝试触发授权弹窗
        AS.AXIsProcessTrustedWithOptions({
            "AXTrustedCheckOptionPrompt": True
        })
        print("   请在弹出的对话框中授权，然后重新运行此脚本。")
        return

    # 找到同花顺进程
    workspace = AppKit.NSWorkspace.sharedWorkspace()
    running_apps = workspace.runningApplications()

    ths_app = None
    for app in running_apps:
        name = app.localizedName()
        if name and "同花顺" in name:
            ths_app = app
            break

    if not ths_app:
        print("[ERROR] 同花顺未运行！请先启动同花顺 Mac 客户端。")
        return

    pid = ths_app.processIdentifier()
    print(f"\n同花顺进程: PID={pid}, Name={ths_app.localizedName()}")
    print(f"Bundle ID: {ths_app.bundleIdentifier()}")

    # 获取 AXUIElement
    app_ref = AS.AXUIElementCreateApplication(pid)

    # 读取应用级属性
    print(f"\n{'─'*60}")
    print("应用级属性:")
    print(f"{'─'*60}")

    err, role = AS.AXUIElementCopyAttributeValue(app_ref, "AXRole", None)
    print(f"  AXRole: {role}" if not err else f"  AXRole: (error {err})")

    err, title = AS.AXUIElementCopyAttributeValue(app_ref, "AXTitle", None)
    print(f"  AXTitle: {title}" if not err else f"  AXTitle: (error {err})")

    # 获取所有窗口
    err, windows = AS.AXUIElementCopyAttributeValue(app_ref, "AXWindows", None)
    if err:
        print(f"\n[ERROR] 无法获取窗口列表 (error code: {err})")
        print("  可能原因: 辅助功能权限不足，或同花顺窗口被隐藏")
        return

    if not windows:
        print("\n[WARN] 同花顺没有打开的窗口")
        return

    print(f"\n窗口数量: {len(windows)}")

    # 遍历每个窗口
    for i, win in enumerate(windows):
        err, win_title = AS.AXUIElementCopyAttributeValue(win, "AXTitle", None)
        err2, win_role = AS.AXUIElementCopyAttributeValue(win, "AXRole", None)
        err3, win_pos = AS.AXUIElementCopyAttributeValue(win, "AXPosition", None)
        err4, win_size = AS.AXUIElementCopyAttributeValue(win, "AXSize", None)

        print(f"\n{'='*60}")
        print(f"窗口 [{i}]: {win_title or '(无标题)'}")
        print(f"  Role: {win_role}")
        if win_pos:
            pos = (AS.AXValueGetValue(win_pos, AS.kAXValueCGPointType, None)
                   if hasattr(AS, 'AXValueGetValue') else win_pos)
            print(f"  Position: {win_pos}")
        if win_size:
            print(f"  Size: {win_size}")
        print(f"{'='*60}")

        # 递归遍历子控件（限深度3层，避免太多输出）
        explore_children(win, depth=0, max_depth=3, prefix="  ")


def explore_children(element, depth: int, max_depth: int, prefix: str):
    """递归探索子控件"""
    import ApplicationServices as AS

    if depth >= max_depth:
        return

    err, children = AS.AXUIElementCopyAttributeValue(element, "AXChildren", None)
    if err or not children:
        return

    for i, child in enumerate(children):
        if i >= 30:  # 每层最多显示 30 个子控件
            remaining = len(children) - 30
            print(f"{prefix}... 还有 {remaining} 个子控件未显示")
            break

        err, role = AS.AXUIElementCopyAttributeValue(child, "AXRole", None)
        err2, title = AS.AXUIElementCopyAttributeValue(child, "AXTitle", None)
        err3, value = AS.AXUIElementCopyAttributeValue(child, "AXValue", None)
        err4, desc = AS.AXUIElementCopyAttributeValue(child, "AXDescription", None)
        err5, role_desc = AS.AXUIElementCopyAttributeValue(child, "AXRoleDescription", None)
        err6, identifier = AS.AXUIElementCopyAttributeValue(child, "AXIdentifier", None)
        err7, subrole = AS.AXUIElementCopyAttributeValue(child, "AXSubrole", None)

        # 构建显示信息
        parts = []
        if role:
            parts.append(f"Role={role}")
        if subrole:
            parts.append(f"SubRole={subrole}")
        if title:
            parts.append(f"Title=\"{title}\"")
        if value is not None and str(value).strip():
            val_str = str(value)[:80]  # 截断长值
            parts.append(f"Value=\"{val_str}\"")
        if desc:
            parts.append(f"Desc=\"{desc}\"")
        if role_desc:
            parts.append(f"RoleDesc=\"{role_desc}\"")
        if identifier:
            parts.append(f"ID=\"{identifier}\"")

        info = ", ".join(parts)
        print(f"{prefix}[{i}] {info}")

        # 递归
        explore_children(child, depth + 1, max_depth, prefix + "  ")


# ── 3. 用 AppleScript 探索 UI 元素（补充方案）─────────────────

def explore_via_applescript():
    """通过 AppleScript 的 System Events 探索 UI 元素"""
    print(f"\n{'='*60}")
    print("通过 AppleScript System Events 探索:")
    print(f"{'='*60}")

    # 获取主窗口的 UI 元素概览
    script = '''
    tell application "System Events"
        tell process "同花顺"
            set frontWindow to front window
            set windowName to name of frontWindow
            set uiElements to every UI element of frontWindow

            set result to "Window: " & windowName & linefeed

            repeat with elem in uiElements
                try
                    set elemRole to role of elem
                    set elemDesc to description of elem
                    set result to result & "  " & elemRole & " — " & elemDesc & linefeed
                end try
            end repeat

            return result
        end tell
    end tell
    '''
    try:
        result = run_applescript(script)
        print(result)
    except Exception as e:
        print(f"  AppleScript 探索失败: {e}")

    # 探索菜单栏
    script2 = '''
    tell application "System Events"
        tell process "同花顺"
            set menuList to name of every menu bar item of menu bar 1
        end tell
    end tell
    return menuList
    '''
    try:
        menus = run_applescript(script2)
        print(f"\n菜单栏: {menus}")
    except Exception as e:
        print(f"  获取菜单栏失败: {e}")


# ── 4. 主流程 ──────────────────────────────────────────────────

def main():
    print("🔍 同花顺 Mac 客户端 — Accessibility 探路脚本")
    print(f"{'='*60}")

    # Step 1: 检测同花顺
    print("\n[Step 1] 检测同花顺运行状态...")
    if not check_ths_running():
        print("❌ 同花顺未运行！请先启动 /Applications/同花顺.app")
        sys.exit(1)
    print("✅ 同花顺正在运行")

    # Step 2: 获取窗口列表
    print("\n[Step 2] 获取窗口列表...")
    windows = get_ths_windows()
    print(f"  窗口: {windows}")

    # Step 3: 激活同花顺
    print("\n[Step 3] 激活同花顺窗口...")
    activate_ths()
    time.sleep(1)  # 等待窗口切到前台

    # Step 4: Accessibility API 深度探索
    print("\n[Step 4] Accessibility API 深度探索...")
    explore_accessibility_tree()

    # Step 5: AppleScript 补充探索
    print("\n[Step 5] AppleScript 补充探索...")
    explore_via_applescript()

    print(f"\n{'='*60}")
    print("🎯 探路完成！根据上面的输出，可以判断:")
    print("   1. Accessibility API 能否定位到交易相关的输入框和按钮")
    print("   2. 需要用哪种方式来实现自动化操作")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
