"""Inbound OAuth2.0 认证：授权码流程（Authorization Code Flow）。

纯 Starlette 实现（无 FastAPI 依赖），以 Route 列表导出，由
AgentArtsRuntimeApp 挂载，与华为云 SDK 标准运行时保持一致。

流程：
    1. 用户访问 /auth/login -> 重定向到 OAuth Provider 授权页
    2. Provider 授权后回调 /auth/callback?code=...&state=...
    3. 后端用 code 换取 access token，再调 userinfo 获取用户信息
    4. 后端签发自有 HS256 会话 JWT（含 sub/name/exp），重定向回前端
    5. 前端请求携带 Authorization: Bearer <session-jwt>，
       /invocations 校验签名与有效期后派生 user_id

默认 Provider 为 Microsoft Entra ID；也可通过 .env 配置任意标准 OAuth2 Provider。
未配置 OAUTH2_CLIENT_ID 时自动进入本地开发模式（/auth/login 直接签发
dev-user 会话，方便先跑通全链路）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from urllib.parse import quote, urlencode

import httpx
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

# ================================================================================
# 配置（.env）
# ================================================================================

CLIENT_ID = os.getenv("OAUTH2_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("OAUTH2_CLIENT_SECRET", "").strip()
AUTHORIZE_URL = os.getenv(
    "OAUTH2_AUTHORIZE_URL",
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
)
TOKEN_URL = os.getenv(
    "OAUTH2_TOKEN_URL",
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
)
USERINFO_URL = os.getenv(
    "OAUTH2_USERINFO_URL",
    "https://graph.microsoft.com/v1.0/me",
)
CALLBACK_URL = os.getenv(
    "OAUTH2_CALLBACK_URL",
    "http://localhost:8080/auth/callback",
)
OAUTH2_SCOPES = os.getenv("OAUTH2_SCOPES", "openid profile email User.Read")

# 会话 JWT 签名密钥（生产环境务必改为随机长字符串）
JWT_SECRET = os.getenv("OAUTH2_JWT_SECRET", "dev-jwt-secret-change-me").encode()
SESSION_TTL_SECONDS = int(os.getenv("OAUTH2_SESSION_TTL_SECONDS", "7200"))
STATE_TTL_SECONDS = 600

DEV_MODE = not CLIENT_ID


# ================================================================================
# 工具函数
# ================================================================================


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def _hmac(data: str) -> str:
    return hmac.new(JWT_SECRET, data.encode(), hashlib.sha256).hexdigest()


def _make_state() -> str:
    """生成带签名与过期时间的 OAuth2 state，防止 CSRF。"""
    payload = {"nonce": str(uuid.uuid4()), "exp": int(time.time()) + STATE_TTL_SECONDS}
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_hmac(body)}"


def _verify_state(state: str) -> bool:
    """校验 state 签名与有效期。"""
    try:
        body, signature = state.rsplit(".", 1)
        if not hmac.compare_digest(signature, _hmac(body)):
            return False
        claims = json.loads(_b64url_decode(body))
        return int(claims.get("exp", 0)) > int(time.time())
    except (ValueError, KeyError, json.JSONDecodeError):
        return False


def _exchange_code(code: str) -> str:
    """用授权码换取 access token。"""
    data = {
        # 微软 v2.0 token 端点强制要求显式 grant_type（GitHub 不需要）
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": CALLBACK_URL,
        "scope": OAUTH2_SCOPES,
    }
    resp = httpx.post(
        TOKEN_URL,
        data=data,
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    resp.raise_for_status()
    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail=f"OAuth2 token 交换失败: {token_data.get('error', 'unknown')}",
        )
    return access_token


def _fetch_userinfo(access_token: str) -> dict[str, str]:
    """调用 Provider userinfo 接口获取用户身份。"""
    resp = httpx.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    # 兼容微软 Graph /me（id/displayName/userPrincipalName）与标准 OIDC
    # userinfo（sub/name/preferred_username）两种响应结构。
    sub = str(data.get("id") or data.get("sub") or data.get("login") or "")
    name = str(
        data.get("displayName")
        or data.get("name")
        or data.get("userPrincipalName")
        or data.get("preferred_username")
        or data.get("login")
        or sub
    )
    if not sub:
        raise HTTPException(status_code=502, detail="OAuth2 userinfo 缺少用户标识")
    return {"sub": sub, "name": name}


def _issue_session_token(sub: str, name: str) -> str:
    """签发自有 HS256 会话 JWT。"""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": sub,
        "name": name,
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
    }
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = _b64url_encode(hmac.new(JWT_SECRET, signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


def verify_session_token(token: str) -> dict[str, str] | None:
    """校验会话 JWT，返回 {sub, name}；无效返回 None。"""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected = _b64url_encode(
            hmac.new(JWT_SECRET, signing_input.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected, signature_b64):
            return None
        claims = json.loads(_b64url_decode(payload_b64))
        if int(claims.get("exp", 0)) <= int(time.time()):
            return None
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            return None
        return {"sub": sub, "name": str(claims.get("name", sub))}
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def extract_bearer_user(request: Request) -> dict[str, str] | None:
    """从 Authorization Bearer 提取并校验会话 JWT。"""
    authorization = request.headers.get("authorization", "").strip()
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return verify_session_token(token.strip())


# ================================================================================
# 路由处理函数（Starlette，无 FastAPI 依赖）
# ================================================================================


async def auth_login(request: Request) -> Response:
    """发起 OAuth2 授权码登录。"""
    if DEV_MODE:
        # 本地开发模式：未配置 OAuth App，直接签发 dev-user 会话
        token = _issue_session_token("dev-user", "dev-user")
        return RedirectResponse(url=f"/?token={token}&name=dev-user")

    state = _make_state()
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK_URL,
        "response_type": "code",
        "scope": OAUTH2_SCOPES,
        "state": state,
    }
    return RedirectResponse(url=f"{AUTHORIZE_URL}?{urlencode(params)}")


async def auth_callback(request: Request) -> Response:
    """OAuth2 授权回调：换 token -> userinfo -> 签发会话 JWT。"""
    query = request.query_params
    error = query.get("error")
    if error:
        return RedirectResponse(url=f"/?auth_error={quote(error)}")

    code = query.get("code")
    state = query.get("state")
    if not code or not state or not _verify_state(state):
        return RedirectResponse(url="/?auth_error=invalid_state")

    try:
        access_token = _exchange_code(code)
        user = _fetch_userinfo(access_token)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(url=f"/?auth_error={quote(str(exc))}")

    session_token = _issue_session_token(user["sub"], user["name"])
    return RedirectResponse(
        url=f"/?token={session_token}&name={quote(user['name'])}"
    )


async def auth_me(request: Request) -> Response:
    """返回当前登录用户（供前端展示）。"""
    user = extract_bearer_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "未登录或会话已过期"})
    return JSONResponse(
        content={"sub": user["sub"], "name": user["name"], "dev_mode": DEV_MODE}
    )


# 由 AgentArtsRuntimeApp 挂载的路由列表
routes = [
    Route("/auth/login", auth_login, methods=["GET"]),
    Route("/auth/callback", auth_callback, methods=["GET"]),
    Route("/auth/me", auth_me, methods=["GET"]),
]
