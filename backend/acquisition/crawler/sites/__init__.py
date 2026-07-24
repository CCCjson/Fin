"""
站点配置真源 + 持久化 — 逆向侦查产出存 acquisition/crawler/sites/<domain>.json
+ 人读 <domain>.md（本包目录即配置真源，get_scrapers_config_dir 指向这里）。

配置 schema：
  {
    "domain": "xueqiu.com",
    "page_url": "https://xueqiu.com/S/SH600519",
    "discovered_at": "2026-07-02T20:30:00",
    "endpoints": [
      {"name": "getstockvote", "url": "https://...", "method": "GET",
       "gate_headers": {"x-requested-with": "..."}, "is_json": true,
       "status": 200, "sample_fields": ["code", "vote", ...]}
    ]
  }

fetch_api 读它直接 curl_cffi 复用；Jason 也能看/手改 .md。
"""
import json
import os
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from typing import Optional

from loguru import logger

from acquisition.config import get_scrapers_config_dir


def domain_of(url: str) -> str:
    """取 url 域名（小写、去 www.）。与 proxy_route.domain_of 同义，避免跨模块依赖。"""
    try:
        net = urlparse(url if "://" in url else f"http://{url}").netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


# 凭据类字段单独存 <domain>.secrets.json（gitignore），不进版本库。
# cookie 是登录态（如 CapitalIQ 付费订阅会话、雪球 xq_a_token），
# 而端点/gate 头/字段样例是逆向工作的成果，应该跟着代码走。
_SECRET_KEYS = ("cookies", "cookies_updated_at")

# ⛔ 顶层 cookies 不是唯一的凭据出口：**令牌还会藏在端点内部**。
# 实锤（2026-07-24）：侦查 finance.yahoo.com 抓到的端点，URL 查询串里带着
# `?.crumb=FBRGbvClsEA`、`default_params` 里也存了一份 —— 那是 Yahoo 的会话
# 令牌，跟着主配置一路进了版本库。只剥顶层 key 挡不住这种。
# 这三处按名字命中就摘走存进 secrets，加载时再贴回来（`_merge_secrets`），
# 上游读到的 cfg 跟以前一模一样，无感。
_CRED_NAME_RE = re.compile(
    r"(crumb|token|auth|secret|passwd|password|session|sid|sign|signature"
    r"|nonce|csrf|xsrf|api[_-]?key|access[_-]?key|cookie)", re.I)
_ENDPOINT_SECRET_FIELDS = ("default_params", "gate_headers")


def _is_cred_name(name: str) -> bool:
    return bool(_CRED_NAME_RE.search(name or ""))


def _redact_endpoints(endpoints: list) -> tuple[list, dict]:
    """摘掉端点里的凭据类参数。

    Args:
        endpoints: 原始端点列表（含真实令牌）。

    Returns:
        `(脱敏后的端点列表, {端点键: 被摘掉的原值})`。第二个给 secrets 文件，
        `_merge_secrets` 负责贴回去。没摘到东西时第二个是空 dict。
    """
    public: list = []
    stash: dict = {}
    for idx, ep in enumerate(endpoints or []):
        if not isinstance(ep, dict):
            public.append(ep)
            continue
        key = str(ep.get("name") or idx)
        ep2 = dict(ep)
        removed: dict = {}

        # 1) URL 查询串里的凭据参数
        for url_field in ("url", "url_base"):
            raw = ep2.get(url_field)
            if not isinstance(raw, str) or "?" not in raw:
                continue
            parts = urlsplit(raw)
            pairs = parse_qsl(parts.query, keep_blank_values=True)
            kept = [(k, v) for k, v in pairs if not _is_cred_name(k)]
            if len(kept) != len(pairs):
                removed[url_field] = raw          # 存整条原始 URL，贴回最省事
                ep2[url_field] = urlunsplit(
                    (parts.scheme, parts.netloc, parts.path,
                     urlencode(kept), parts.fragment))

        # 2) default_params / gate_headers 里的凭据键
        for field in _ENDPOINT_SECRET_FIELDS:
            d = ep2.get(field)
            if not isinstance(d, dict):
                continue
            hit = {k: v for k, v in d.items() if _is_cred_name(k)}
            if hit:
                removed[field] = hit
                ep2[field] = {k: v for k, v in d.items() if k not in hit}

        if removed:
            stash[key] = removed
        public.append(ep2)
    return public, stash


def _restore_endpoints(cfg: dict, stash: dict) -> None:
    """`_redact_endpoints` 的逆操作，就地把令牌贴回 cfg（加载路径用）。"""
    if not stash:
        return
    for idx, ep in enumerate(cfg.get("endpoints") or []):
        if not isinstance(ep, dict):
            continue
        removed = stash.get(str(ep.get("name") or idx))
        if not removed:
            continue
        for field, val in removed.items():
            if field in ("url", "url_base"):
                ep[field] = val
            elif isinstance(ep.get(field), dict) and isinstance(val, dict):
                ep[field].update(val)
            else:
                ep[field] = val


def _json_path(domain: str) -> str:
    return os.path.join(get_scrapers_config_dir(), f"{domain}.json")


def _secrets_path(domain: str) -> str:
    return os.path.join(get_scrapers_config_dir(), f"{domain}.secrets.json")


def _md_path(domain: str) -> str:
    return os.path.join(get_scrapers_config_dir(), f"{domain}.md")


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读站点配置失败 {path}：{e}")
        return None


def _merge_secrets(cfg: Optional[dict], domain: str) -> Optional[dict]:
    """把 <domain>.secrets.json 里的凭据并回 cfg。上游一律读 cfg["cookies"]，无感。"""
    if cfg is None:
        return None
    secrets = _read_json(_secrets_path(domain)) if os.path.exists(_secrets_path(domain)) else None
    if secrets:
        # endpoint_secrets 不是顶层字段，要贴回各端点内部，别 update 进 cfg
        _restore_endpoints(cfg, secrets.pop("endpoint_secrets", None) or {})
        cfg.update(secrets)
    return cfg


def _write_secrets(domain: str, secrets: dict) -> None:
    if not secrets:
        return
    path = _secrets_path(domain)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(secrets, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_site_config(domain: str) -> Optional[dict]:
    """读站点配置；不存在返 None。domain 可传完整 url，自动归一。

    先精确匹配；未命中则做子域后缀匹配（传 eastmoney.com 能命中 quote.eastmoney.com，
    反之亦然）——MoneyBill 常用主域名指代，侦查却按页面完整 host 存。
    """
    domain = domain_of(domain) or domain
    exact = _json_path(domain)
    if os.path.exists(exact):
        return _merge_secrets(_read_json(exact), domain)

    # 后缀匹配：扫配置目录
    cfg_dir = get_scrapers_config_dir()
    if not os.path.isdir(cfg_dir):
        return None
    for fn in os.listdir(cfg_dir):
        if not fn.endswith(".json") or fn.endswith(".secrets.json"):
            continue
        cand = fn[:-5]                              # 去 .json
        if domain.endswith("." + cand) or cand.endswith("." + domain):
            logger.info(f"站点配置后缀匹配：{domain} → {cand}")
            return _merge_secrets(_read_json(os.path.join(cfg_dir, fn)), cand)
    return None


def save_site_config(cfg: dict) -> str:
    """写站点配置（json + md）。cfg 必须含 domain。返回 json 路径。"""
    domain = cfg.get("domain") or domain_of(cfg.get("page_url", ""))
    if not domain:
        raise ValueError("save_site_config: cfg 缺少 domain")
    cfg["domain"] = domain
    cfg.setdefault("discovered_at", datetime.now().isoformat(timespec="seconds"))

    os.makedirs(get_scrapers_config_dir(), exist_ok=True)

    # 凭据剥离到 <domain>.secrets.json；主配置进版本库
    secrets = {k: cfg[k] for k in _SECRET_KEYS if k in cfg}
    public = {k: v for k, v in cfg.items() if k not in _SECRET_KEYS}
    # 端点内部的令牌（URL 查询参数 / default_params / gate_headers）也得摘走，
    # 否则只剥顶层 cookies 会漏（见 _CRED_NAME_RE 上面那段实锤）
    if public.get("endpoints"):
        public["endpoints"], ep_stash = _redact_endpoints(public["endpoints"])
        if ep_stash:
            secrets["endpoint_secrets"] = ep_stash
    _write_secrets(domain, secrets)

    jpath = _json_path(domain)
    tmp = jpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(public, f, ensure_ascii=False, indent=2)
    os.replace(tmp, jpath)                       # 原子替换

    try:
        with open(_md_path(domain), "w", encoding="utf-8") as f:
            f.write(_render_md(cfg))
    except OSError as e:
        logger.debug(f"写 .md 失败（忽略）：{e}")

    logger.info(f"站点配置已保存：{jpath}（{len(cfg.get('endpoints', []))} 个端点）")
    return jpath


def find_endpoint(cfg: dict, name: Optional[str] = None) -> Optional[dict]:
    """按 name 找端点；name 为空时返回第一个 JSON 端点（缺省默认）。"""
    endpoints = cfg.get("endpoints", []) if cfg else []
    if not endpoints:
        return None
    if name:
        for ep in endpoints:
            if ep.get("name") == name:
                return ep
        return None
    for ep in endpoints:                          # 缺省：首个 JSON 端点
        if ep.get("is_json"):
            return ep
    return endpoints[0]


def _render_md(cfg: dict) -> str:
    """生成人读的接口说明书。"""
    lines = [
        f"# {cfg.get('domain', '?')} 逆向接口说明",
        "",
        f"- 侦查页面：{cfg.get('page_url', '')}",
        f"- 侦查时间：{cfg.get('discovered_at', '')}",
        f"- 端点数量：{len(cfg.get('endpoints', []))}",
        "",
        "> 由 scrape 侦查时自动产出；scrape 读同目录 .json 复用取数；本文件供人工查阅/参考。",
        "",
    ]
    for i, ep in enumerate(cfg.get("endpoints", []), 1):
        lines += [
            f"## {i}. {ep.get('name', '(未命名)')}",
            "",
            f"- URL：`{ep.get('url', '')}`",
            f"- 方法：{ep.get('method', 'GET')}　状态：{ep.get('status', '?')}　JSON：{ep.get('is_json', False)}",
        ]
        gate = ep.get("gate_headers", {})
        if gate:
            lines.append(f"- Gate 头：`{', '.join(gate.keys())}`")
        fields = ep.get("sample_fields", [])
        if fields:
            lines.append(f"- 字段样例：{', '.join(str(x) for x in fields[:20])}")
        lines.append("")
    return "\n".join(lines)
