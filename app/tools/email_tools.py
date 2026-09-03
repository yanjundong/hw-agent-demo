"""
Microsoft 365 邮件工具（OAuth2 User Federation 出站凭据）
================================================================================

参考 personal-assistant 项目的 email_tools.py：
- public tool 只暴露业务参数（to/subject/body/cc）
- @require_access_token 只放在 private boundary（_m365_email_request）
- Demo 简化：取消二次确认，调用即发送
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from agentarts.sdk import require_access_token
from langchain_core.tools import tool

from app.tools.pending_auth import capture_pending_auth_url

M365_EMAIL_PROVIDER = os.getenv("M365_EMAIL_PROVIDER_NAME", "m365-email-provider")
EMAIL_SCOPES = (
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
)
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
    return f"[{resp.status_code}] {resp.reason_phrase or 'Unknown error'}"


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
    request_headers = {"Authorization": f"Bearer {access_token}"}
    if headers:
        request_headers.update(headers)
    with httpx.Client(base_url=GRAPH_BASE_URL, timeout=30.0) as client:
        return client.request(
            method,
            path,
            headers=request_headers,
            params=params,
            json=json,
        )


EMAIL_TOOLS = [send_email]
