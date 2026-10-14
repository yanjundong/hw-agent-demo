"""TravelAgent：AgentArts LangGraph 智能体核心（模型连接 + OutBound 工具循环）。"""

import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.config import config
from app.logging_config import preview
from app.tools import build_tools

logger = logging.getLogger("agent")


class AgentState(MessagesState):
    iteration: int


def should_continue(state: AgentState) -> str:
    """条件边判断：如果 LLM 返回了工具调用，则继续执行 tools 节点。"""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "continue"
    return "end"


SYSTEM_PROMPT = """\
你是智能体助手，可以通过工具调用 GitHub、华为云 IAM、Microsoft 365 邮件和
飞书开放平台等外部能力。本 Demo 只提供 OutBound 出站身份演示工具，不包含
高德地图、代码沙箱与记忆库。
根据用户意图选择正确的工具；只有用户请求确实需要外部数据或操作时才调用工具，
纯聊天场景直接回答。

## GitHub 仓库工具（OAuth2 用户委托）
- github_list_repositories：列出当前用户可访问的 GitHub 仓库。
当用户询问"我的 GitHub 仓库""我在 GitHub 上有哪些项目"等涉及用户个人
GitHub 账号的请求时使用。
不用于查询代码仓库内容、搜索代码或加星（当前 demo 未提供这些工具）。
如果工具返回授权链接，先把链接发给用户并说明需要完成授权。

## 华为云 IAM 工具（STS 临时凭据）
- huaweicloud_list_iam_users：列出华为云 IAM 用户/子用户。
当用户询问华为云 IAM 用户、子用户、账号下的用户列表时使用。
只读操作，不涉及 AK/SK 或 Token 泄露；其他华为云服务（如 ECS、OBS）当前
不提供工具，不要编造。

## Microsoft 365 邮件工具（OAuth2 用户委托）
- send_email：向指定收件人发送邮件（Demo 简化版，调用即发送，无二次确认）。
当用户要求"给某某发邮件""发送一封邮件"等请求时使用，参数为收件人列表、
主题和正文。
收件人缺失或不确定时先向用户确认收件人地址；不用于读取、搜索或回复邮件
（当前 demo 未提供这些工具）。

## 飞书应用信息工具（OAuth2 M2M 应用身份）
- feishu_get_app_info：查询飞书应用详情（应用名称、描述、权限范围、支持的
  token 类型等），app_id 固定为 "me"，即查询当前应用自身信息。
当用户询问飞书应用信息、应用详情、应用权限（如"查一下飞书应用的信息"）
时使用。该工具以应用自身身份（tenant_access_token）调用飞书开放平台，
不需要用户授权。

## 行为准则
- 使用中文回复，友好、专业、简洁
- 工具返回错误时如实转述，不要编造结果
- 不清楚用户意图时先澄清，不猜测调用工具"""


class TravelAgent:
    """Agent 核心：初始化模型、注册 OutBound 工具、构建 LangGraph 图并执行对话。"""

    def __init__(self):
        self._llm = ChatOpenAI(
            model=config.model.name,
            base_url=config.model.url,
            api_key=config.model.api_key,
            temperature=0.1,  # 较低的温度值保障工具调用的 JSON 格式稳定
        )
        # 工具列表只构建一次：model_node 每轮都会被 ReAct 循环调用，
        # 若在这里重复调用 build_tools() 会重复打印注册日志并重复导入模块。
        self._tools = build_tools()
        self.graph = self._build_graph()
        self.system_prompt = SYSTEM_PROMPT

    def _build_graph(self):
        def model_node(state: AgentState) -> AgentState:
            llm_with_tools = self._llm.bind_tools(self._tools)
            started = time.perf_counter()
            logger.info(
                "llm.request model=%s messages=%d tools=%d",
                config.model.name,
                len(state["messages"]),
                len(self._tools),
            )
            response = llm_with_tools.invoke(state["messages"])
            tool_calls = [
                str(call.get("name"))
                for call in (getattr(response, "tool_calls", None) or [])
            ]
            usage = getattr(response, "usage_metadata", None) or {}
            logger.info(
                "llm.response duration_ms=%.1f tool_calls=%s input_tokens=%s output_tokens=%s",
                (time.perf_counter() - started) * 1000,
                ",".join(tool_calls) or "-",
                usage.get("input_tokens", "-"),
                usage.get("output_tokens", "-"),
            )
            return {"messages": [response], "iteration": state.get("iteration", 0) + 1}

        workflow = StateGraph(AgentState)
        workflow.add_node("agent", model_node)
        workflow.add_node("tools", ToolNode(self._tools))

        workflow.set_entry_point("agent")
        workflow.add_conditional_edges(
            "agent", should_continue, {"continue": "tools", "end": END}
        )
        workflow.add_edge("tools", "agent")

        # 本 Demo 不启用云端记忆，编译时无需 checkpointer（每轮对话独立）
        return workflow.compile()

    def run(self, user_input: str, session_id=None, user_id: str = "default-user") -> str:
        """主运行方法：单轮对话 + OutBound 工具循环。

        session_id / user_id 仅保留参数兼容 Web 层（用于日志与响应回显），
        不参与状态持久化（Demo 未启用记忆库）。
        """
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_input),
        ]
        started = time.perf_counter()
        logger.info(
            "agent.run.start session=%s input_len=%d input=%s",
            session_id or "-",
            len(user_input),
            preview(user_input, 200),
        )
        result = self.graph.invoke(
            {"messages": messages},
            recursion_limit=config.max_iterations,
        )
        result_messages = result.get("messages", [])
        called_tools = sorted(
            {
                str(call.get("name"))
                for message in result_messages
                for call in (getattr(message, "tool_calls", None) or [])
            }
        )
        answer = result_messages[-1].content
        logger.info(
            "agent.run.done duration_ms=%.1f messages=%d iterations=%s tools=%s answer_len=%d",
            (time.perf_counter() - started) * 1000,
            len(result_messages),
            result.get("iteration", "-"),
            ",".join(called_tools) or "-",
            len(str(answer)),
        )
        return answer
