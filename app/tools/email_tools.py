"""
Microsoft 365 邮件工具（OAuth2 User Federation 出站凭据）
================================================================================

参考 personal-assistant 项目的 email_tools.py：
- public tool 只暴露业务参数（to/subject/body/cc）
- @require_access_token 只放在 private boundary（_m365_email_request）
- Demo 简化：取消二次确认，调用即发送
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import httpx
from agentarts.sdk import require_access_token
from langchain_core.tools import tool

from app.tools.pending_auth import capture_pending_auth_url

logger = logging.getLogger("tools.email")

M365_EMAIL_PROVIDER = os.getenv("M365_EMAIL_PROVIDER_NAME", "m365-email-provider")
_DEFAULT_EMAIL_SCOPES = (
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
)
# 可用 M365_EMAIL_SCOPES 覆盖（逗号分隔，需与 Identity 控制台 provider 配置一致）
EMAIL_SCOPES = tuple(
    scope.strip()
    for scope in os.getenv("M365_EMAIL_SCOPES", "").split(",")
    if scope.strip()
) or _DEFAULT_EMAIL_SCOPES
# 是否每次发送都强制重新走微软 OAuth 授权（默认 false：授权一次后复用
# Identity 服务 vault 中的 token，与 personal-assistant 行为一致）。
M365_FORCE_AUTH = os.getenv("M365_FORCE_AUTH", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}
GRAPH_BASE_URL = os.getenv(
    "GRAPH_BASE_URL",
    "https://graph.microsoft.com/v1.0/me",
).rstrip("/")

def _extract_graph_error(resp: httpx.Response) -> str:
    """从 Microsoft Graph 错误响应中提取可读的错误信息。"""
    try:
        body = resp.json()
        graph_error = body.get("error", {})
        code = graph_error.get("code", "")
        message = graph_error.get("message", "")
        if code or message:
            return f"[{resp.status_code}] {code}: {message}" if code else f"[{resp.status_code}] {message}"
    except (ValueError, AttributeError):
        pass
    # 非 JSON 响应（网关/代理错误页等）时保留正文片段，便于定位 401/403 的真实原因
    try:
        snippet = " ".join(resp.text.split())[:300]
    except Exception:  # noqa: BLE001
        snippet = ""
    base = f"[{resp.status_code}] {resp.reason_phrase or 'Unknown error'}"
    return f"{base} body={snippet}" if snippet else base


def _token_claims(access_token: str) -> dict[str, Any]:
    """解析 access token 的关键声明（不校验签名），用于定位 401 的真实原因。"""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}
    return {
        key: data[key]
        for key in ("aud", "tid", "scp", "roles", "appid", "exp")
        if data.get(key)
    }


@tool
def send_email(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
) -> dict[str, Any]:
    """发送一封新邮件（Demo 简化版，调用即发送）。

    Args:
        to: 收件人邮箱地址列表
        subject: 邮件主题
        body: 邮件正文（纯文本）
        cc: 抄送邮箱地址列表，可选
    """
    if not to:
        return {"sent": False, "message_id": None, "error": "至少需要一个收件人"}
    if not subject or not subject.strip():
        return {"sent": False, "message_id": None, "error": "邮件主题不能为空"}
    if not body or not body.strip():
        return {"sent": False, "message_id": None, "error": "邮件正文不能为空"}

    try:
        message: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to
            ],
        }
        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]

        resp = _m365_email_request(
            "POST",
            "/sendMail",
            headers={"Content-Type": "application/json"},
            json={"message": message, "saveToSentItems": True},
        )
        if resp.status_code == 202:
            return {
                "sent": True,
                "message_id": None,
                "error": None,
                "status_code": 202,
            }
        return {
            "sent": False,
            "message_id": None,
            "error": _extract_graph_error(resp),
            "status_code": resp.status_code,
        }
    except Exception as exc:
        return {"sent": False, "message_id": None, "error": f"发送失败: {exc}"}


@require_access_token(
    provider_name=M365_EMAIL_PROVIDER,
    scopes=list(EMAIL_SCOPES),
    on_auth_url=lambda url: capture_pending_auth_url(url, "Microsoft 365"),
    auth_flow="USER_FEDERATION",
    force_authentication=M365_FORCE_AUTH,
)
def _m365_email_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> httpx.Response:
    """Private boundary：仅在此处接收 SDK 注入的 OAuth2 access token。"""
    if not access_token:
        raise RuntimeError("access_token was not injected by require_access_token")
    logger.info(
        "m365.graph.token claims=%s token_len=%d",
        _token_claims(access_token) or "- (opaque token)",
        len(access_token),
    )
    request_headers = {"Authorization": f"Bearer {access_token}"}
    if headers:
        request_headers.update(headers)
    with httpx.Client(base_url=GRAPH_BASE_URL, timeout=30.0) as client:
        response = client.request(
            method,
            path,
            headers=request_headers,
            params=params,
            json=json,
        )
    if response.status_code >= 400:
        logger.warning(
            "m365.graph.error method=%s url=%s status=%s http_version=%s content_type=%s "
            "content_length=%s www_authenticate=%s headers=%s body=%s",
            method,
            response.request.url,
            response.status_code,
            response.http_version,
            response.headers.get("content-type"),
            response.headers.get("content-length"),
            response.headers.get("www-authenticate"),
            dict(response.headers),
            " ".join(response.text.split())[:300],
        )
        _probe_graph(token=access_token)
    else:
        logger.info(
            "m365.graph.response method=%s url=%s status=%s",
            method,
            response.request.url,
            response.status_code,
        )
    return response


def _probe_graph(token: str) -> None:
    """对照探测：同一个 token 调最简单的 GET /me，区分 token 问题与接口问题。"""
    for trust_env in (True, False):
        try:
            with httpx.Client(
                base_url="https://graph.microsoft.com/v1.0",
                timeout=15.0,
                trust_env=trust_env,
            ) as probe_client:
                probe = probe_client.get(
                    "/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
            logger.warning(
                "m365.graph.probe.get_me trust_env=%s status=%s http_version=%s "
                "content_type=%s body=%s",
                trust_env,
                probe.status_code,
                probe.http_version,
                probe.headers.get("content-type"),
                " ".join(probe.text.split())[:200],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "m365.graph.probe.get_me trust_env=%s failed error=%s",
                trust_env,
                exc,
            )


EMAIL_TOOLS = [send_email]
