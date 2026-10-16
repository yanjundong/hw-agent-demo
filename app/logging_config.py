"""统一日志配置（面向华为云 LTS 日志采集）。

设计要点：
1. 单行输出、`key=value` 结构化风格，便于在 LTS 里检索与做关键词告警；
2. 自动附带上下文 `user=` / `session=` / `req=`，一次调用的日志可以完整串联
   （值取自 AgentArtsRuntimeContext，由 Gateway header 或本地会话注入）；
3. 输出到 stdout，AgentArts Runtime 容器日志即可被 LTS 采集，无需额外配置；
4. 通过环境变量 `LOG_LEVEL` 调整级别（默认 INFO，排障时可临时设为 DEBUG）。
"""
from __future__ import annotations

import logging
import os
import sys

from agentarts.sdk.runtime.context import AgentArtsRuntimeContext

DASH = "-"

LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] "
    "user=%(ctx_user)s session=%(ctx_session)s req=%(ctx_request)s %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

class RuntimeContextFilter(logging.Filter):
    """把当前请求的用户/会话/请求 ID 注入每条日志。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.ctx_user = AgentArtsRuntimeContext.get_user_id() or DASH
        record.ctx_session = AgentArtsRuntimeContext.get_session_id() or DASH
        record.ctx_request = AgentArtsRuntimeContext.get_request_id() or DASH
        return True


class PrintHandler(logging.Handler):
    """用 print(..., flush=True) 输出日志。

    平台（容器 stdout 采集 / LTS）对 print 输出的兼容性最好，
    这里把根 logger 的输出通道统一换成 print，同时保留级别与上下文格式。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            print(self.format(record), flush=True)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def setup_logging() -> None:
    """初始化根日志器；重复调用安全，且会强制接管根 logger 上的既有 handler。

    之所以每次调用都重新接管：agentarts SDK / uvicorn 会在导入或启动阶段
    抢先注册自己的 handler，导致启动期日志变成另一种格式。
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    stream = sys.stdout
    # 非 TTY（容器 / 管道 → LTS 采集）时强制 UTF-8，避免中文日志乱码
    if not getattr(stream, "isatty", lambda: False)():
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass

    handler = PrintHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    handler.addFilter(RuntimeContextFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # agentarts 包自带一个 stderr StreamHandler（旧格式），会让同一条日志输出两遍，
    # 这里移除它并让日志统一传播到根 logger。
    sdk_logger = logging.getLogger("agentarts")
    for existing in list(sdk_logger.handlers):
        sdk_logger.removeHandler(existing)
    sdk_logger.propagate = True

    # 第三方库降噪：只保留告警与错误，避免刷屏淹没关键点位
    for noisy in (
        "httpx",
        "httpcore",
        "openai._base_client",
        "urllib3",
        "pyasn1",
        "agentarts.token_poller",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def preview(value: object, limit: int = 300) -> str:
    """把任意对象压成单行、限长的日志片段，防止大 JSON 淹没日志。"""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(truncated total={len(text)})"
