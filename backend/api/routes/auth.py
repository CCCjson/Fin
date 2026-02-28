"""
认证路由 — 轻量级密码登录 + JWT
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from jose import jwt, JWTError
from loguru import logger
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/auth", tags=["认证"])

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")
JWT_SECRET = os.getenv("JWT_SECRET", "fin-default-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: str


class VerifyResponse(BaseModel):
    valid: bool


def _create_token() -> tuple[str, datetime]:
    """生成 JWT token，有效期 30 天"""
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {
        "sub": "jason",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, expire


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """用户名 + 密码登录，返回 JWT token"""
    if not AUTH_USERNAME or not AUTH_PASSWORD:
        raise HTTPException(status_code=500, detail="服务端未配置登录凭证")

    if req.username != AUTH_USERNAME or req.password != AUTH_PASSWORD:
        logger.warning(f"登录失败：用户名或密码错误 (username={req.username})")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token, expire = _create_token()
    logger.info("登录成功")
    return LoginResponse(
        token=token,
        expires_at=expire.isoformat(),
    )


@router.get("/verify", response_model=VerifyResponse)
async def verify(token: str):
    """校验 token 是否有效"""
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return VerifyResponse(valid=True)
    except JWTError:
        return VerifyResponse(valid=False)
