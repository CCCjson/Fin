"""
API 全局鉴权依赖 — JWT 强制校验

复用 api/routes/auth.py 里已有的密钥（JWT_SECRET）与算法（JWT_ALGORITHM），
不重新发明一套签发/校验逻辑。

- HTTP 路由：用 `require_auth` 作为 FastAPI 依赖，解析 `Authorization: Bearer <token>`
  （浏览器 fetch/axios 走这条）。因为 WebSocket 无法带自定义 header，同时兼容 `?token=`
  查询参数，这样挂在含 WS 路由的 router 上也能用查询参数放行。
- WebSocket 路由：用 `verify_ws_token(websocket)` 在 accept 前手动校验查询参数。

环境开关 `AUTH_ENFORCE`（默认 on，写法沿用项目惯例 turn_monitor.py / tool_groups.py）：
设为 off/0/false 时，所有校验直接放行 —— 紧急回滚开关，仅限本机可信调试。
"""
import os
from typing import Optional

from fastapi import Header, HTTPException, Query, WebSocket
from jose import jwt, JWTError

# 复用 auth.py 的常量，保证签发与校验用同一把钥匙 / 同一种算法
from api.routes.auth import JWT_SECRET, JWT_ALGORITHM

# 已知弱密钥：auth.py 的硬编码默认值 + .env.example 占位符 + 常见占位。
# 强制鉴权时若 JWT_SECRET 仍是其中之一，启动自检会拒绝启动。
_WEAK_SECRETS = {
    "",
    "fin-default-secret",
    "your_jwt_secret_here",
    "changeme",
    "secret",
}


def auth_enforced() -> bool:
    """鉴权是否强制开启。AUTH_ENFORCE=off/0/false 时关闭（紧急回滚开关）。"""
    return os.getenv("AUTH_ENFORCE", "on").lower() not in ("off", "0", "false")


def _decode(token: str) -> dict:
    """用 auth.py 的密钥/算法解码校验 JWT，失败抛 JWTError。"""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def _extract_token(authorization: Optional[str], token: Optional[str]) -> Optional[str]:
    """从 Authorization 头（优先）或 token 查询参数取出裸 token。"""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return token


async def require_auth(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> Optional[dict]:
    """FastAPI 依赖：强制 JWT 鉴权。

    校验 `Authorization: Bearer <token>`；作为兼容也接受 `?token=` 查询参数
    （挂到含 WebSocket 路由的 router 上时，浏览器 WS 只能靠查询参数带 token）。

    Returns:
        解码后的 JWT payload（校验通过）；AUTH_ENFORCE 关闭时返回 None（放行）。

    Raises:
        HTTPException: 401 —— 缺少 token 或 token 无效/过期。
    """
    if not auth_enforced():
        return None
    raw = _extract_token(authorization, token)
    if not raw:
        raise HTTPException(status_code=401, detail="未认证：缺少 Authorization Bearer token")
    try:
        return _decode(raw)
    except JWTError:
        raise HTTPException(status_code=401, detail="认证失败：token 无效或已过期")


async def verify_ws_token(websocket: WebSocket) -> bool:
    """WebSocket 握手前手动校验 `?token=` 查询参数。

    浏览器原生 WebSocket 不能带自定义 header，只能把 token 放查询参数。
    校验失败时调用方应 `await websocket.close(code=1008)`。
    AUTH_ENFORCE 关闭时恒放行。

    Returns:
        True 放行；False 需拒绝连接。
    """
    if not auth_enforced():
        return True
    raw = websocket.query_params.get("token")
    if not raw:
        return False
    try:
        _decode(raw)
        return True
    except JWTError:
        return False


def assert_strong_secret() -> None:
    """启动自检：强制鉴权却仍用弱/默认 JWT_SECRET 时抛错，阻止带弱密钥上线。

    Raises:
        RuntimeError: AUTH_ENFORCE 开启且 JWT_SECRET 仍是已知弱密钥。
    """
    if not auth_enforced():
        return
    if JWT_SECRET.strip() in _WEAK_SECRETS:
        raise RuntimeError(
            "JWT_SECRET 仍为默认/弱密钥，拒绝启动以防局域网/公网裸奔。"
            "请在 backend/.env 配置一个强随机 JWT_SECRET（例如 `openssl rand -hex 32`），"
            "或临时设 AUTH_ENFORCE=off 绕过（仅限本机可信调试）。"
        )
