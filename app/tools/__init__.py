"""工具注册工厂：汇总 OutBound 身份演示工具（InBound 认证见 app/oauth2.py）。"""
import logging

logger = logging.getLogger(__name__)


def build_tools():
    """构建工具列表，供 LangGraph ToolNode / bind_tools 使用。"""
    tools = []

    # AgentArts Identity 出站凭据演示工具（OAuth2 / STS，API Key 暂未启用）
    try:
        from app.tools.identity_tools import IDENTITY_TOOLS

        tools.extend(IDENTITY_TOOLS)
        print("[Identity] 已注册出站凭据演示工具: GitHub OAuth2 / 华为云 IAM STS")
    except Exception as e:
        print(f"[Warning] 出站凭据工具加载失败，已跳过: {e}")

    # Microsoft 365 邮件工具
    try:
        from app.tools.email_tools import EMAIL_TOOLS

        tools.extend(EMAIL_TOOLS)
        print("[Email] 已注册邮件工具: send_email")
    except Exception as e:
        print(f"[Warning] 邮件工具加载失败，已跳过: {e}")

    # 飞书 M2M 工具（OAuth2 client credentials 出站凭据）
    try:
        from app.tools.feishu_tools import FEISHU_TOOLS

        tools.extend(FEISHU_TOOLS)
        print("[Feishu] 已注册飞书 M2M 工具: feishu_get_app_info")
    except Exception as e:
        print(f"[Warning] 飞书工具加载失败，已跳过: {e}")

    return tools
