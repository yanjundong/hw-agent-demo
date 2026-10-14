"""AgentArts Runtime Web 服务（华为云标准 AgentArtsRuntimeApp 封装）。

平台契约（由 AgentArtsRuntimeApp 提供）：
    POST /invocations  -> @app.entrypoint 注册的 Agent 主入口
    GET  /ping         -> @app.ping 健康检查
    WS   /ws           -> 标准运行时 WebSocket（本 demo 未使用）

AgentArtsRuntimeApp 自动完成：
    - 从 Gateway header 注入 AgentArtsRuntimeContext（WAT / session / user_id）
    - 并发控制（Semaphore + ThreadPool）与同步/异步 handler 调度
    - SSE 流式响应与 JSON 序列化
    - uvicorn 启动（handler.run）

Demo 自有的扩展路由（挂载到同一应用）：
    /auth/login|callback|me  Inbound OAuth2.0 授权码流程
    /auth/pending            邮件出站授权的 URL 轮询
    /（静态前端页面）
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agentarts.sdk import AgentArtsRuntimeApp, RequestContext
from agentarts.sdk.runtime.context import AgentArtsRuntimeContext
from agentarts.sdk.runtime.model import (
    ACCESS_TOKEN_HEADER,
    SESSION_HEADER,
    USER_ID_HEADER,
)
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from app.agent import TravelAgent
from app.logging_config import preview, setup_logging
from app.oauth2 import extract_bearer_user, routes as oauth2_routes
from app.tools.pending_auth import (
    bind_auth_event_queue,
    pop_pending_auth_url,
    reset_auth_event_queue,
)

setup_logging()
logger = logging.getLogger("webapp")

_agent: TravelAgent | None = None
_agent_init_error: str | None = None


@asynccontextmanager
async def lifespan(app: AgentArtsRuntimeApp):
    """启动时初始化 TravelAgent 单例，失败不阻塞服务启动。"""
    global _agent, _agent_init_error
    started = time.perf_counter()
    try:
        _agent = await asyncio.to_thread(TravelAgent)
        logger.info(
            "agent.init.done duration_ms=%.1f tools=%d version=%s region=%s",
            (time.perf_counter() - started) * 1000,
            len(getattr(_agent, "_tools", None) or []),
            os.getenv("AGENT_VERSION", "dev"),
            os.getenv("HUAWEICLOUD_SDK_REGION", "-"),
        )
    except Exception as exc:  # noqa: BLE001
        _agent_init_error = str(exc)
        logger.exception("agent.init.failed /invocations 将返回 503")
    yield
    logger.info("agent.shutdown")
    _agent = None


# ================================================================================
# 华为云标准运行时应用
# ================================================================================
app = AgentArtsRuntimeApp(lifespan=lifespan)


@app.ping
def ping() -> str:
    """平台健康检查（SDK 会包装为 {"status": "Healthy", ...} 响应）。"""
    return "Healthy"


def _require_inbound_user(request: Request) -> tuple[str, str]:
    """入站身份：Gateway header（生产）优先，其次 OAuth2 会话 JWT；fail-closed 401。"""
    gateway_user_id = request.headers.get(USER_ID_HEADER, "").strip()
    if gateway_user_id:
        # 生产：AgentArts Gateway 已校验 JWT 并注入可信身份与 WAT
        AgentArtsRuntimeContext.set_user_id(gateway_user_id)
        wat = request.headers.get(ACCESS_TOKEN_HEADER, "").strip()
        if wat:
            AgentArtsRuntimeContext.set_workload_access_token(wat)
        return gateway_user_id, gateway_user_id

    # 平台 Gateway 已用 authorizer（如 API_KEY）鉴权，但未注入用户 ID 时，
    # 以 Workload Access Token 为可信标记兜底放行（WAT 仅能由平台注入，外部无法伪造）。
    wat = request.headers.get(ACCESS_TOKEN_HEADER, "").strip()
    if wat:
        AgentArtsRuntimeContext.set_workload_access_token(wat)
        AgentArtsRuntimeContext.set_user_id("gateway-user")
        return "gateway-user", "gateway-user"

    # 本地/自建 OAuth2：校验后端签发的会话 JWT
    user = extract_bearer_user(request)
    if user:
        AgentArtsRuntimeContext.set_user_id(user["sub"])
        return user["sub"], user["name"]

    raise ValueError("unauthorized")


async def _stream_agent_run(
    message: str,
    session_id: str,
    user_id: str,
    workload_access_token: str | None,
    request_id: str | None = None,
):
    """SSE 流式执行 Agent。

    出站 USER_FEDERATION 授权会阻塞等待用户操作（SDK 内部轮询 token），
    这里通过 contextvar 事件队列把 on_auth_url 触发的授权链接即时推给
    前端渲染授权卡片，而不是等整轮跑完才返回。

    事件格式（每行一个 data: JSON）：
        {"type": "start", "session_id": ...}
        {"type": "auth_required", "provider": ..., "auth_url": ...}
        {"type": "result", "status": "success", "response": ..., "session_id": ...}
        {"type": "error", "message": ..., "session_id": ...}
    """
    # 流在平台 Gateway 转发后执行于新 task 上下文，显式恢复身份与会话，
    # 供 agent 线程中的 Identity SDK / on_auth_url 读取。
    AgentArtsRuntimeContext.set_user_id(user_id)
    AgentArtsRuntimeContext.set_session_id(session_id)
    if request_id:
        AgentArtsRuntimeContext.set_request_id(request_id)
    if workload_access_token:
        AgentArtsRuntimeContext.set_workload_access_token(workload_access_token)

    auth_events: queue.Queue = queue.Queue()
    bind_token = bind_auth_event_queue(auth_events)
    started = time.perf_counter()
    run_task = asyncio.create_task(
        asyncio.to_thread(_agent.run, message, session_id, user_id)
    )
    logger.info("stream.start message_len=%d", len(message))
    try:
        yield {"type": "start", "session_id": session_id}
        while not run_task.done():
            await asyncio.sleep(0.5)
            while True:
                try:
                    event = auth_events.get_nowait()
                except queue.Empty:
                    break
                logger.info(
                    "auth.required provider=%s url=%s",
                    event["provider"],
                    event["auth_url"],
                )
                yield {
                    "type": "auth_required",
                    "provider": event["provider"],
                    "auth_url": event["auth_url"],
                }

        # run 结束后清空残余事件，避免授权完成瞬间仍推送卡片
        while True:
            try:
                event = auth_events.get_nowait()
            except queue.Empty:
                break
            logger.info(
                "auth.required provider=%s url=%s",
                event["provider"],
                event["auth_url"],
            )
            yield {
                "type": "auth_required",
                "provider": event["provider"],
                "auth_url": event["auth_url"],
            }

        response = run_task.result()
        logger.info(
            "stream.done status=success duration_ms=%.1f response_len=%d",
            (time.perf_counter() - started) * 1000,
            len(str(response)),
        )
        yield {
            "type": "result",
            "status": "success",
            "response": response,
            "session_id": session_id,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "stream.error duration_ms=%.1f error=%s",
            (time.perf_counter() - started) * 1000,
            preview(exc, 200),
        )
        yield {
            "type": "error",
            "message": f"执行出错: {exc}",
            "session_id": session_id,
        }
    finally:
        reset_auth_event_queue(bind_token)


def _resolve_session_id(request: Request, payload: dict[str, Any]) -> str:
    """会话 ID 解析顺序：Gateway header -> payload -> 自动生成。"""
    raw = (
        request.headers.get(SESSION_HEADER, "").strip()
        or (payload.get("session_id") or "")
        or str(uuid.uuid4())
    )
    AgentArtsRuntimeContext.set_session_id(raw)
    return raw


@app.entrypoint
def handle_request(
    payload: dict[str, Any],
    context: RequestContext | None = None,
) -> Any:
    """AgentArts 标准入口：POST /invocations。

    返回 dict 时由运行时序列化为 JSON；返回 Response 对象时直接透传
    （用于 401/503 等错误状态）。
    """
    request = context.request if context is not None else None
    if request is None:
        return JSONResponse(status_code=400, content={"detail": "缺少请求上下文"})

    if _agent is None:
        detail = (
            f"服务未正确初始化：{_agent_init_error}"
            if _agent_init_error
            else "Agent 未初始化"
        )
        return JSONResponse(status_code=503, content={"detail": detail})

    message = str((payload or {}).get("message") or "").strip()
    if not message:
        return JSONResponse(status_code=400, content={"detail": "message is required"})

    # ── 入站 OAuth2.0 认证：Gateway header 或 Bearer 会话 JWT，fail-closed ──
    try:
        user_id, user_name = _require_inbound_user(request)
    except ValueError:
        logger.warning(
            "invocation.rejected reason=unauthorized accept=%s",
            request.headers.get("accept", ""),
        )
        return JSONResponse(
            status_code=401,
            content={"detail": "未登录：请先完成 OAuth2 登录（/auth/login）"},
        )

    session_id = _resolve_session_id(request, payload)
    wants_sse = "text/event-stream" in request.headers.get("accept", "").lower()
    logger.info(
        "invocation.received stream=%s user_name=%s message_len=%d message=%s",
        wants_sse,
        user_name,
        len(message),
        preview(message, 200),
    )

    # SSE：前端（Cloudflare Pages / 本地 UI）请求流式响应，授权链接即时推送
    if wants_sse:
        wat = request.headers.get(ACCESS_TOKEN_HEADER, "").strip() or None
        request_id = request.headers.get("X-Request-Id", "").strip() or None
        return _stream_agent_run(message, session_id, user_id, wat, request_id)

    started = time.perf_counter()
    try:
        response = _agent.run(message, session_id, user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "invocation.error duration_ms=%.1f error=%s",
            (time.perf_counter() - started) * 1000,
            preview(exc, 200),
        )
        return {
            "response": f"执行出错: {exc}",
            "status": "error",
            "session_id": session_id,
        }

    logger.info(
        "invocation.done status=success duration_ms=%.1f response_len=%d",
        (time.perf_counter() - started) * 1000,
        len(str(response)),
    )
    result: dict[str, Any] = {
        "response": response,
        "status": "success",
        "session_id": session_id,
    }
    pending = pop_pending_auth_url(user_id)
    if pending:
        result["auth_url"] = pending["auth_url"]
        result["auth_provider"] = pending["provider"]
    return result


# ================================================================================
# Demo 扩展路由（挂载到标准运行时）
# ================================================================================


async def pending_auth(request: Request) -> JSONResponse:
    """前端轮询：返回当前用户待完成的 OAuth2 授权链接（取出即消费，防止残留重复提示）。"""
    try:
        user_id, _ = _require_inbound_user(request)
    except ValueError:
        return JSONResponse(status_code=401, content={"detail": "未登录"})
    return JSONResponse(pop_pending_auth_url(user_id) or {"auth_url": None, "provider": None})


# 1. Inbound OAuth2.0 路由
for oauth2_route in oauth2_routes:
    app.add_route(
        oauth2_route.path,
        oauth2_route.endpoint,
        methods=list(oauth2_route.methods),
    )

# 2. 邮件出站授权 URL 轮询
app.add_route("/auth/pending", pending_auth, methods=["GET"])

# 3. 托管前端静态页面：访问 http://host:port/ 打开 Web Chat。
#    必须放在所有 API 路由之后，否则 catch-all 会拦截 /invocations、/ping。
STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
