"""出站 OAuth 授权 URL 的公共暂存与流式事件投递。

同一个授权链接会走三条通道，保证本地与云端都能把链接呈现给用户：

1. 控制台打印（CLI / 服务端日志调试）；
2. /auth/pending 轮询（本地运行时直连场景的旧版通道，保留兼容）；
3. SSE 流内事件（Cloudflare Pages + AgentArts Gateway 场景），通过
   contextvar 把每个请求专属的事件队列注入到 agent 执行线程，
   on_auth_url 触发时立即投递 auth_required 事件，无需轮询共享状态。
"""
from __future__ import annotations

import contextvars
import logging
import queue
import threading
import time
from typing import Any

from agentarts.sdk.runtime.context import AgentArtsRuntimeContext

logger = logging.getLogger("pending_auth")

_PENDING: dict[str, tuple[str, str, float]] = {}  # user_id -> (provider, auth_url, created_at)
_PENDING_LOCK = threading.Lock()
_TTL_SECONDS = 300

# 当前请求的事件队列（SSE 流使用；None 表示非流式调用）
_AUTH_EVENT_QUEUE: contextvars.ContextVar[queue.Queue | None] = contextvars.ContextVar(
    "hw_agentarts_auth_event_queue",
    default=None,
)


def bind_auth_event_queue(event_queue: queue.Queue) -> contextvars.Token:
    """把当前请求的事件队列绑定到本上下文中（SSE 流开始前调用）。"""
    return _AUTH_EVENT_QUEUE.set(event_queue)


def reset_auth_event_queue(token: contextvars.Token) -> None:
    """恢复队列 contextvar（SSE 流结束/异常时调用）。"""
    _AUTH_EVENT_QUEUE.reset(token)


def capture_pending_auth_url(auth_url: str, provider: str = "OAuth2") -> None:
    """SDK on_auth_url 回调：按当前用户暂存授权链接，同时打印到控制台。"""
    user_id = AgentArtsRuntimeContext.get_user_id() or "default-user"
    with _PENDING_LOCK:
        _PENDING[user_id] = (provider, auth_url, time.time())
    logger.warning(
        "oauth.auth_required provider=%s user_id=%s url=%s",
        provider,
        user_id,
        auth_url,
    )
    event_queue = _AUTH_EVENT_QUEUE.get()
    if event_queue is not None:
        event_queue.put({"provider": provider, "auth_url": auth_url})


def pop_pending_auth_url(user_id: str) -> dict[str, Any] | None:
    """取出即消费：返回 {provider, auth_url}；超时或不存在返回 None。"""
    with _PENDING_LOCK:
        entry = _PENDING.pop(user_id, None)
    if not entry:
        return None
    provider, auth_url, created_at = entry
    if time.time() - created_at > _TTL_SECONDS:
        logger.warning("oauth.auth_url.expired provider=%s", provider)
        return None
    logger.info(
        "oauth.auth_url.consumed provider=%s age_s=%.1f",
        provider,
        time.time() - created_at,
    )
    return {"provider": provider, "auth_url": auth_url}
