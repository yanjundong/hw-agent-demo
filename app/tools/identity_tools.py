"""
AgentArts Identity 出站凭据演示工具
================================================================================

参考 personal-assistant 项目的 Agent Identity 设计，演示三种 Outbound
Credential 模式。所有真实密钥都不写入代码或 .env，只在 AgentArts Identity
控制台创建 Credential Provider，代码里只保存 provider name：

1. API Key   -> @require_api_key       静态密钥（M2M），实现保留，暂未启用
2. OAuth2    -> @require_access_token  用户委托（USER_FEDERATION），如 GitHub
3. STS Token -> @require_sts_token     云资源临时凭证（M2M），如华为云 IAM

装饰器放置约定（与 personal-assistant 一致）：
- public tool 只暴露业务参数，不暴露 token / api_key / sts_credentials
- @require_xxx 只放在 private boundary（下划线函数），运行时由 SDK 注入凭据
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from agentarts.sdk import require_access_token, require_api_key, require_sts_token
from agentarts.sdk.identity.types import StsCredentials
from huaweicloudsdkcore.auth.credentials import GlobalCredentials
from huaweicloudsdkiam.v5 import IamClient, ListUsersV5Request
from langchain_core.tools import tool

from app.tools.pending_auth import capture_pending_auth_url

# ================================================================================
# 配置：provider name 从 .env 读取（真实凭据在 AgentArts Identity 控制台创建）
# ================================================================================

# --- API Key（暂未启用，预留配置）---
# API_KEY_PROVIDER = os.getenv("IDENTITY_API_KEY_PROVIDER", "hw-demo-api-key")
# API_KEY_ENDPOINT = os.getenv(
#     "IDENTITY_API_KEY_ENDPOINT",
#     "https://httpbin.org/anything",
# )

# --- GitHub OAuth2 (User Federation) ---
GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_PROVIDER_NAME = os.getenv("GITHUB_PROVIDER_NAME", "github-provider")
GITHUB_SCOPES = [
    scope.strip()
    for scope in os.getenv("GITHUB_SCOPES", "repo,read:user").split(",")
    if scope.strip()
]
GITHUB_OAUTH_CALLBACK_URL = os.getenv("GITHUB_OAUTH_CALLBACK_URL") or None

# --- Huawei Cloud IAM (STS) ---
IAM_USERS_PROVIDER = os.getenv("IAM_USERS_PROVIDER_NAME", "iam-users-readonly")
IAM_USERS_AGENCY_SESSION = os.getenv(
    "IAM_USERS_AGENCY_SESSION_NAME",
    "hw-agent-demo-iam-readonly",
)
IAM_USERS_REGION = os.getenv("IAM_USERS_REGION", "cn-southwest-2")
IAM_USERS_ENDPOINT = os.getenv(
    "IAM_USERS_ENDPOINT",
    f"https://iam.{IAM_USERS_REGION}.myhuaweicloud.com",
)


# ================================================================================
# 工具一：GitHub OAuth2（@require_access_token, USER_FEDERATION）
# ================================================================================


@tool
def github_list_repositories(limit: int = 20) -> dict[str, Any]:
    """列出当前用户可访问的 GitHub 仓库，演示 OAuth2 User Federation。

    使用 AgentArts Identity 的 OAuth2 provider（GITHUB_PROVIDER_NAME），
    以当前用户委托身份调用 GitHub API。未授权时会打印授权链接。
    """
    try:
        payload = _github_request(
            "GET",
            "/user/repos",
            params={"per_page": limit, "sort": "updated"},
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"GitHub 调用失败: {exc}",
            "provider_name": GITHUB_PROVIDER_NAME,
        }

    if not isinstance(payload, list):
        return {"ok": False, "error": "GitHub API 返回格式异常"}

    repositories = [
        item.get("full_name")
        for item in payload
        if isinstance(item, dict) and item.get("full_name")
    ]
    return {
        "ok": True,
        "provider_name": GITHUB_PROVIDER_NAME,
        "count": len(repositories),
        "repositories": repositories,
    }


@require_access_token(
    provider_name=GITHUB_PROVIDER_NAME,
    scopes=GITHUB_SCOPES,
    on_auth_url=lambda url: capture_pending_auth_url(url, "GitHub"),
    auth_flow="USER_FEDERATION",
    callback_url=GITHUB_OAUTH_CALLBACK_URL,
)
def _github_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> Any:
    """Private boundary：仅在此处接收 SDK 注入的 OAuth2 access token。"""
    if not access_token:
        raise RuntimeError("access_token was not injected by require_access_token")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(base_url=GITHUB_API_BASE_URL, timeout=30.0) as client:
        response = client.request(method, path, headers=headers, params=params)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()


# ================================================================================
# 工具二：华为云 IAM（@require_sts_token）
# ================================================================================


def _credentials_to_global_credentials(sts: StsCredentials) -> GlobalCredentials:
    return (
        GlobalCredentials(sts.access_key_id, sts.secret_access_key)
        .with_security_token(sts.security_token)
    )


def _build_iam_client(sts: StsCredentials) -> IamClient:
    return (
        IamClient.new_builder()
        .with_credentials(_credentials_to_global_credentials(sts))
        .with_endpoint(IAM_USERS_ENDPOINT)
        .build()
    )


@tool
def huaweicloud_list_iam_users(limit: int = 20) -> dict[str, Any]:
    """列出华为云 IAM 用户/子用户，演示 @require_sts_token 出站凭据。

    使用 AgentArts Identity 的 STS provider（IAM_USERS_PROVIDER_NAME）获取
    临时 AK/SK + Security Token，只读调用华为云 IAM V5 API。
    """
    try:
        response = _list_iam_users(limit=limit)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"IAM 调用失败: {exc}",
            "provider_name": IAM_USERS_PROVIDER,
        }

    users = []
    for item in (getattr(response, "users", None) or []):
        users.append(
            {
                "id": getattr(item, "user_id", None) or getattr(item, "id", None),
                "name": getattr(item, "user_name", None)
                or getattr(item, "name", None),
                "enabled": getattr(item, "enabled", None),
            }
        )
    return {
        "ok": True,
        "provider_name": IAM_USERS_PROVIDER,
        "region": IAM_USERS_REGION,
        "count": len(users),
        "users": users,
    }


@require_sts_token(
    provider_name=IAM_USERS_PROVIDER,
    agency_session_name=IAM_USERS_AGENCY_SESSION,
    into="sts_credentials",
)
def _list_iam_users(
    *,
    limit: int | None = None,
    sts_credentials: StsCredentials | None = None,
) -> Any:
    """Private boundary：仅在此处接收 SDK 注入的 STS 临时凭据。"""
    if not sts_credentials:
        raise RuntimeError("sts_credentials was not injected by require_sts_token")
    client = _build_iam_client(sts_credentials)
    request = ListUsersV5Request(limit=limit)
    return client.list_users_v5(request)


# ================================================================================
# 工具注册列表（供 app/tools/__init__.py 的 build_tools() 汇总）
# ================================================================================

IDENTITY_TOOLS = [
    github_list_repositories,
    huaweicloud_list_iam_users,
]

# API Key 工具（@require_api_key）暂未启用：实现简单，需要时按文件头部
# 说明恢复即可（public tool + private boundary + httpx 调用，约 40 行）。
