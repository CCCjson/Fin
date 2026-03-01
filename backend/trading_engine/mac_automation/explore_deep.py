"""
Phase 1.1 深度探测 — 同花顺 Mac 客户端
探测方向:
  1. 列出 AXApplication 的所有属性（看有没有隐藏窗口）
  2. 尝试 CGWindowList 直接获取窗口信息
  3. 用 AppleScript 探索菜单结构
  4. 检测同花顺底层框架 (Qt? CEF? Cocoa?)
"""
import subprocess
import sys
import json


def run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        return f"[ERROR] {result.stderr.strip()}"
    return result.stdout.strip()


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def explore_all_ax_attributes():
    """列出 AXApplication 的全部属性"""
    section("1. AXApplication 全部属性")

    import ApplicationServices as AS
    import AppKit

    workspace = AppKit.NSWorkspace.sharedWorkspace()
    for app in workspace.runningApplications():
        if app.localizedName() and "同花顺" in app.localizedName():
            pid = app.processIdentifier()
            break
    else:
        print("同花顺未运行")
        return

    app_ref = AS.AXUIElementCreateApplication(pid)

    # 获取所有支持的属性名
    err, attr_names = AS.AXUIElementCopyAttributeNames(app_ref, None)
    if err:
        print(f"  获取属性名失败: error={err}")
        return

    print(f"  属性数量: {len(attr_names)}")
    for attr in sorted(attr_names):
        err, val = AS.AXUIElementCopyAttributeValue(app_ref, attr, None)
        if err:
            print(f"  {attr}: (error {err})")
        elif isinstance(val, (list, tuple)):
            print(f"  {attr}: [{len(val)} items]")
            # 展开列表中的前几个
            for j, item in enumerate(val[:5]):
                if hasattr(item, '__class__') and 'AXUIElement' in str(type(item)):
                    ie, irole = AS.AXUIElementCopyAttributeValue(item, "AXRole", None)
                    ie2, ititle = AS.AXUIElementCopyAttributeValue(item, "AXTitle", None)
                    ie3, idesc = AS.AXUIElementCopyAttributeValue(item, "AXDescription", None)
                    print(f"    [{j}] Role={irole}, Title={ititle}, Desc={idesc}")
                else:
                    print(f"    [{j}] {str(item)[:100]}")
        else:
            val_str = str(val)[:120]
            print(f"  {attr}: {val_str}")

    # 尝试 AXFocusedWindow
    print(f"\n  -- 尝试 AXFocusedWindow --")
    err, focused = AS.AXUIElementCopyAttributeValue(app_ref, "AXFocusedWindow", None)
    if not err and focused:
        print(f"  ✅ AXFocusedWindow 存在！")
        err2, attr_names2 = AS.AXUIElementCopyAttributeNames(focused, None)
        if not err2:
            print(f"  属性: {list(attr_names2)}")
            for a in attr_names2:
                e, v = AS.AXUIElementCopyAttributeValue(focused, a, None)
                if not e:
                    if isinstance(v, (list, tuple)):
                        print(f"    {a}: [{len(v)} items]")
                    else:
                        print(f"    {a}: {str(v)[:100]}")
    else:
        print(f"  ❌ AXFocusedWindow 不存在 (error={err})")

    # 尝试 AXMainWindow
    print(f"\n  -- 尝试 AXMainWindow --")
    err, main_win = AS.AXUIElementCopyAttributeValue(app_ref, "AXMainWindow", None)
    if not err and main_win:
        print(f"  ✅ AXMainWindow 存在！深入探索...")
        deep_explore_element(main_win, max_depth=4)
    else:
        print(f"  ❌ AXMainWindow 不存在 (error={err})")


def explore_cg_window_list():
    """通过 CGWindowListCopyWindowInfo 获取窗口信息（绕过 Accessibility）"""
    section("2. CGWindowList 窗口信息（底层 API）")

    import Quartz

    # 获取所有窗口（包括不在 AX 中的）
    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll,
        Quartz.kCGNullWindowID
    )

    ths_windows = []
    for win in window_list:
        owner = win.get("kCGWindowOwnerName", "")
        if "同花顺" in str(owner) or "10jqka" in str(owner).lower():
            ths_windows.append(win)

    print(f"  找到 {len(ths_windows)} 个同花顺窗口:")
    for i, w in enumerate(ths_windows):
        print(f"\n  [{i}]")
        print(f"    Owner: {w.get('kCGWindowOwnerName')}")
        print(f"    Name: {w.get('kCGWindowName', '(无名)')}")
        print(f"    WindowID: {w.get('kCGWindowNumber')}")
        print(f"    Layer: {w.get('kCGWindowLayer')}")
        bounds = w.get('kCGWindowBounds', {})
        print(f"    Bounds: x={bounds.get('X')}, y={bounds.get('Y')}, "
              f"w={bounds.get('Width')}, h={bounds.get('Height')}")
        print(f"    OnScreen: {w.get('kCGWindowIsOnscreen')}")
        print(f"    Alpha: {w.get('kCGWindowAlpha')}")
        print(f"    OwnerPID: {w.get('kCGWindowOwnerPID')}")


def explore_menus():
    """探索同花顺的菜单结构（找交易相关入口）"""
    section("3. 菜单结构探索")

    # 逐个菜单探索
    menus_to_explore = ["同花顺", "窗口", "快捷键", "操作"]
    for menu_name in menus_to_explore:
        script = f'''
        tell application "System Events"
            tell process "同花顺"
                try
                    set menuItems to name of every menu item of menu 1 of menu bar item "{menu_name}" of menu bar 1
                    return "{menu_name}: " & (menuItems as text)
                on error errMsg
                    return "{menu_name}: [Error] " & errMsg
                end try
            end tell
        end tell
        '''
        result = run_applescript(script)
        print(f"  {result}")


def explore_app_framework():
    """探测同花顺使用的 UI 框架"""
    section("4. UI 框架检测")

    # 检查 bundle 内容
    import os
    app_path = "/Applications/同花顺.app"
    frameworks_path = os.path.join(app_path, "Contents", "Frameworks")
    macos_path = os.path.join(app_path, "Contents", "MacOS")

    print("  Frameworks 目录:")
    if os.path.exists(frameworks_path):
        for f in sorted(os.listdir(frameworks_path)):
            print(f"    {f}")
    else:
        print("    (不存在)")

    print("\n  MacOS 目录:")
    if os.path.exists(macos_path):
        for f in sorted(os.listdir(macos_path)):
            print(f"    {f}")

    # 检查 Info.plist
    plist_path = os.path.join(app_path, "Contents", "Info.plist")
    if os.path.exists(plist_path):
        result = subprocess.run(
            ["plutil", "-p", plist_path],
            capture_output=True, text=True
        )
        print(f"\n  Info.plist 关键信息:")
        for line in result.stdout.split("\n"):
            lower = line.lower()
            if any(k in lower for k in ["version", "identifier", "name", "principal", "executable"]):
                print(f"    {line.strip()}")

    # 检查是否有 QtWebEngine / CEF
    result2 = subprocess.run(
        ["find", app_path, "-name", "*.framework", "-maxdepth", 4],
        capture_output=True, text=True
    )
    if result2.stdout.strip():
        print(f"\n  内嵌 Frameworks:")
        for line in result2.stdout.strip().split("\n"):
            name = line.split("/")[-1]
            print(f"    {name}")


def deep_explore_element(element, max_depth=3, depth=0, prefix="    "):
    """深度探索一个 AX 元素"""
    import ApplicationServices as AS

    if depth >= max_depth:
        return

    err, children = AS.AXUIElementCopyAttributeValue(element, "AXChildren", None)
    if err or not children:
        return

    for i, child in enumerate(children):
        if i >= 20:
            print(f"{prefix}... 还有 {len(children) - 20} 个子元素")
            break

        err, role = AS.AXUIElementCopyAttributeValue(child, "AXRole", None)
        err2, title = AS.AXUIElementCopyAttributeValue(child, "AXTitle", None)
        err3, value = AS.AXUIElementCopyAttributeValue(child, "AXValue", None)
        err4, desc = AS.AXUIElementCopyAttributeValue(child, "AXDescription", None)
        err5, identifier = AS.AXUIElementCopyAttributeValue(child, "AXIdentifier", None)
        err6, subrole = AS.AXUIElementCopyAttributeValue(child, "AXSubrole", None)

        parts = [f"Role={role}"]
        if subrole: parts.append(f"Sub={subrole}")
        if title: parts.append(f"Title=\"{title}\"")
        if value is not None and str(value).strip():
            parts.append(f"Val=\"{str(value)[:60]}\"")
        if desc: parts.append(f"Desc=\"{desc}\"")
        if identifier: parts.append(f"ID=\"{identifier}\"")

        # 检查是否可交互
        err7, actions = AS.AXUIElementCopyActionNames(child, None)
        if not err7 and actions:
            parts.append(f"Actions={list(actions)}")

        print(f"{prefix}[{i}] {', '.join(parts)}")
        deep_explore_element(child, max_depth, depth + 1, prefix + "  ")


def try_keyboard_shortcut():
    """尝试通过快捷键打开交易面板"""
    section("5. 尝试快捷键操作")

    # 先激活同花顺
    run_applescript('tell application "同花顺" to activate')
    import time
    time.sleep(1)

    # 尝试常见的同花顺快捷键
    shortcuts = [
        ("F12", "交易/委托"),
        ("F1", "帮助/成交明细"),
        ("F3", "即时分析"),
    ]

    print("  ⚠️  以下快捷键仅列出供参考，不会自动执行")
    print("  （避免在不了解的情况下触发交易操作）")
    for key, desc in shortcuts:
        print(f"  {key} → {desc}")

    print("\n  请手动在同花顺中打开交易面板，然后重新运行此脚本")
    print("  这样可以扫描到交易面板的控件")


def main():
    print("🔬 同花顺 Mac 客户端 — 深度探测")

    explore_all_ax_attributes()
    explore_cg_window_list()
    explore_menus()
    explore_app_framework()
    try_keyboard_shortcut()

    section("总结")
    print("  根据以上信息可以判断:")
    print("  1. 同花顺的 UI 框架 (Cocoa / Qt / CEF)")
    print("  2. Accessibility API 的支持程度")
    print("  3. 最适合的自动化方案")


if __name__ == "__main__":
    main()
