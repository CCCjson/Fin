"""
爬取失败诊断 — 把「爬不动」归类成人话原因 + 可执行建议。

Jason 要求：每次遇到无法爬取的站点，反馈**为什么**失败、**该怎么做**。
所有逆向/抓取工具（scrape / read_url）失败时都调这里产出
`{reason, suggestion}`，回灌给 MoneyBill/用户，而不是甩一句"失败"。
"""
from typing import Optional
from urllib.parse import urlsplit


def _domain(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except Exception:
        return ""


def diagnose(status: Optional[int] = None, error: Optional[str] = None,
             body: Optional[str] = None, url: str = "") -> dict:
    """归类抓取失败，返回 {reason, suggestion, status}。"""
    b = (body or "")[:2000].lower()
    e = (str(error) or "").lower()

    def R(reason: str, suggestion: str) -> dict:
        return {"reason": reason, "suggestion": suggestion, "status": status}

    # 1. 连接层（超时/拒连/代理/DNS/SSL）
    if any(k in e for k in ("timeout", "timed out")):
        return R("连接超时", "网络或代理慢/不通。检查 HTTP_PROXY_MODE；海外站确认 Shadowrocket "
                 "出口(KNOWLEDGE_OVERSEAS_PROXY)、国内站确认直连或快代理池；稍后重试。")
    if any(k in e for k in ("refused", "proxyerror", "connection", "resolve", "getaddrinfo", "ssl", "certificate")):
        return R("连接失败（代理/DNS/SSL）", "连不上目标。检查代理配置(HTTP_PROXY_MODE / KNOWLEDGE_OVERSEAS_PROXY)；"
                 "国内源确认已清死代理走直连；确认域名可达。")

    # 2. 人机验证 / 商业反爬盾
    if any(k in b for k in ("cloudflare", "cf-chl", "turnstile", "datadome", "perimeterx",
                            "captcha", "验证码", "人机验证", "滑块", "slider", "点击验证", "geetest")):
        return R("撞上人机验证 / 商业反爬盾（Cloudflare/DataDome/验证码等）",
                 "开有头浏览器人工过一次（KNOWLEDGE_PLAYWRIGHT_HEADED_ON_CHALLENGE=true，会弹真浏览器让你点），"
                 "cookie 落盘后自动复用；若是激进盾（连真浏览器都拦）大概率无解，建议换数据源。")

    # 3. 登录墙 / 登录态失效
    if status == 401 or any(k in b for k in ("请登录", "登录后", "未登录", "登录失效", "sign in",
                                             "log in", "please login", "unauthorized", "need login")):
        return R("需要登录 / 登录态失效",
                 "调 login_site(该站 url) —— 会弹出浏览器让 Jason 手动登录一次，登录后自动抓 cookie 存配置，"
                 "之后 scrape 该站就能取数了。（对只是 cookie 短期过期、登录还在的站，scrape 会自动重侦查刷新，不必人工登。）")

    # 4. 限速 / 软封
    if status in (429, 202) or any(k in b for k in ("rate limit", "too many", "访问频繁", "frequent", "请求过于频繁")):
        return R(f"触发限速 / 软封（{status or '429/202'}）",
                 "降低抓取频率；配快代理池轮换 IP（.env kuaidaili_api）；DDG 软封会自动退避 45s，稍后重试即可。")

    # 5. 门禁被拒
    if status == 403:
        return R("被拒（403）：门禁头/cookie 失效，或被识别为爬虫",
                 "① scrape 遇 403 已会自动重侦查刷新 gate 头 + cookie 后重试；② 仍不行则多半是要登录 → 调 login_site 人工登一次再 scrape。")

    # 6. 服务端 / 其它状态码
    if status and status >= 500:
        return R(f"站点服务器错误（{status}）", "对方服务端问题，稍后重试；持续报错则该接口可能已下线/迁移。")
    if status and status not in (200, None):
        return R(f"非预期状态码（{status}）", "接口可能已变更或参数不对。核对 params 是否正确；换个 url 让 scrape 重侦查更新站点配置。")

    # 7. 有响应但非目标内容（盾页/静态渲染/无结构化数据/空正文）
    return R("拿到响应但不是目标内容（可能是盾页/纯静态渲染/无结构化接口/正文为空）",
             "① 想要 JSON 却没有：该页可能纯静态或数据画在 canvas 上，改用 read_url 抓 HTML 正文；"
             "② 想要正文却空：改用 scrape 让浏览器过盾拿结构化接口数据；③ 确认该站确实有你要的数据。")


def format_feedback(diag: dict, url: str = "") -> str:
    """把诊断拼成一句给用户看的反馈：为什么 + 怎么做。"""
    dom = _domain(url)
    who = f"「{dom}」" if dom else "该站点"
    return (f"⚠️ 爬取{who}失败。\n"
            f"原因：{diag.get('reason', '未知')}\n"
            f"建议：{diag.get('suggestion', '换个数据源或稍后重试')}")
