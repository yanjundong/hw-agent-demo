"""工具注册工厂：汇总 OutBound 身份演示工具（InBound 认证见 app/oauth2.py）。

所有工具在注册时统一包一层日志，LTS 里可直接检索：
    tool.start   工具名 + 入参（限长）
    tool.done    工具名 + 耗时 + 结果摘要（限长）
    tool.error   工具名 + 耗时 + 异常栈
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any

from langchain_core.tools import StructuredTool

from app.logging_config import preview

logger = logging.getLogger("tools")


def _instrument(tool: Any) -> Any:
    """给单个工具加 start/done/error 日志，保留原始 args_schema 与描述。"""
    inner = getattr(tool, "func", None)
    if not isinstance(tool, StructuredTool) or inner is None:
        logger.warning("tool.instrument.skipped name=%s", getattr(tool, "name", "?"))
        return tool

    name = tool.name

    @functools.wraps(inner)
    def wrapper(**kwargs: Any) -> Any:
        logger.info("tool.start name=%s args=%s", name, preview(kwargs, 200))
        started = time.perf_counter()
        try:
            result = inner(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "tool.error name=%s duration_ms=%.1f error=%s",
                name,
                (time.perf_counter() - started) * 1000,
                preview(exc, 200),
            )
            raise
        logger.info(
            "tool.done name=%s duration_ms=%.1f result=%s",
            name,
            (time.perf_counter() - started) * 1000,
            preview(result, 400),
        )
        return result

    return StructuredTool.from_function(
        func=wrapper,
        name=name,
        description=tool.description,
        args_schema=tool.args_schema,
    )


def build_tools():
    """构建工具列表，供 LangGraph ToolNode / bind_tools 使用。"""
    tools = []

    # AgentArts Identity 出站凭据演示工具（OAuth2 / STS，API Key 暂未启用）
    try:
        from app.tools.identity_tools import IDENTITY_TOOLS

        tools.extend(IDENTITY_TOOLS)
        logger.info(
            "tools.registered group=identity names=%s",
            ",".join(t.name for t in IDENTITY_TOOLS),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("tools.load.failed group=identity error=%s", preview(exc, 200))

    # Microsoft 365 邮件工具
    try:
        from app.tools.email_tools import EMAIL_TOOLS

        tools.extend(EMAIL_TOOLS)
        logger.info(
            "tools.registered group=email names=%s",
            ",".join(t.name for t in EMAIL_TOOLS),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("tools.load.failed group=email error=%s", preview(exc, 200))

    # 飞书 M2M 工具（OAuth2 client credentials 出站凭据）
    try:
        from app.tools.feishu_tools import FEISHU_TOOLS

        tools.extend(FEISHU_TOOLS)
        logger.info(
            "tools.registered group=feishu names=%s",
            ",".join(t.name for t in FEISHU_TOOLS),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("tools.load.failed group=feishu error=%s", preview(exc, 200))

    return [_instrument(tool) for tool in tools]
