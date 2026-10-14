"""
飞书 M2M 工具（OAuth2 M2M 出站凭据）
================================================================================

演示 @require_access_token 的 M2M 模式：Agent 以应用自身身份（不是用户委托）
调用飞书开放平台 API。凭据由 AgentArts Identity 的 feishu-provider
（OAuth2 Credential Provider，M2M / client credentials）运行时注入，
代码中不保存任何 app_secret。

参考接口：获取应用信息
    GET https://open.feishu.cn/open-apis/application/v6/applications/{app_id}
    Authorization: Bearer <tenant_access_token>
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from agentarts.sdk import require_access_token
from langchain_core.tools import tool

FEISHU_PROVIDER_NAME = os.getenv("FEISHU_PROVIDER_NAME", "feishu-provider")
FEISHU_API_BASE_URL = os.getenv(
    "FEISHU_API_BASE_URL",
    "https://open.feishu.cn/open-apis",
).rstrip("/")
# 固定查询当前应用自身信息："me" 表示 tenant_access_token 所属的应用，
# 无需额外申请"获取应用信息"权限。
FEISHU_APP_ID = "me"

logger = logging.getLogger("tools.feishu")


@tool
def feishu_get_app_info() -> dict[str, Any]:
    """查询飞书应用信息，演示 OAuth2 M2M 出站凭据（应用身份调用飞书 API）。

    使用 AgentArts Identity 的 OAuth2 provider（FEISHU_PROVIDER_NAME，
    auth_flow=M2M）获取 tenant_access_token，以当前应用自身身份读取应用详情
    （app_id 固定为 "me"）。
    """
    try:
        data = _feishu_request(
            "GET",
            f"/application/v6/applications/{FEISHU_APP_ID}",
            params={"lang": "zh_cn"},
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"飞书调用失败: {exc}",
            "provider_name": FEISHU_PROVIDER_NAME,
        }

    # 飞书统一响应结构：{code: 0, msg: "success", data: {...}}
    if data.get("code") != 0:
        logger.warning(
            "feishu.error code=%s msg=%s",
            data.get("code"),
            data.get("msg"),
        )
        return {
            "ok": False,
            "error": f"飞书错误 {data.get('code')}: {data.get('msg')}",
            "provider_name": FEISHU_PROVIDER_NAME,
        }

    body = data.get("data") or {}
    app = body.get("app") or {}
    version = body.get("app_version") or {}
    logger.info(
        "feishu.app_info app_id=%s app_name=%s",
        app.get("app_id") or version.get("app_id"),
        app.get("app_name") or version.get("app_name"),
    )
    return {
        "ok": True,
        "provider_name": FEISHU_PROVIDER_NAME,
        "app_id": app.get("app_id") or version.get("app_id") or FEISHU_APP_ID,
        "app_name": app.get("app_name") or version.get("app_name"),
        "description": app.get("description") or version.get("description"),
        "avatar_url": app.get("avatar_url") or version.get("avatar_url"),
        "status": app.get("status") or version.get("status"),
        "token_types": app.get("token_types") or version.get("token_types") or [],
        "scopes": app.get("app_scopes") or version.get("scopes") or [],
    }


@require_access_token(
    provider_name=FEISHU_PROVIDER_NAME,
    auth_flow="M2M",
)
def _feishu_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> Any:
    """Private boundary：仅在此处接收 SDK 注入的 M2M access token。"""
    if not access_token:
        raise RuntimeError("access_token was not injected by require_access_token")
    # M2M 场景这里应是 tenant_access_token（t- 开头）；只记录前缀与长度，不落完整 token
    logger.info(
        "feishu.token prefix=%s len=%d",
        access_token[:4],
        len(access_token),
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    with httpx.Client(base_url=FEISHU_API_BASE_URL, timeout=30.0) as client:
        response = client.request(method, path, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


FEISHU_TOOLS = [feishu_get_app_info]
