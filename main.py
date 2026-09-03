"""
hw-agent-demo 统一入口

    python main.py          -> 启动 AgentArts Web 服务（/ping + /invocations）
    python main.py --test   -> 运行本地自动化测试脚本（原测试逻辑）
"""
import argparse
import os
import uuid


def run_self_test():
    """本地自动化测试：验证模型连通与 OutBound 出站凭据工具（GitHub / IAM / 邮件 / 飞书）。"""
    from app.agent import TravelAgent

    print("正在初始化智能体助手 (InBound OAuth2 + OutBound 身份演示工具)...")
    try:
        agent = TravelAgent()
    except Exception as e:
        print(f"\n[初始化失败] 请检查 .env 文件配置: {e}")
        return

    # 生成标准 UUID 会话 ID（Demo 无记忆，仅用于日志回显）
    session_id = str(uuid.uuid4())
    print(f"当前分配的会话 ID (标准 UUID): {session_id}\n")

    # =====================================================================
    # 第一轮测试：基础人设与大模型连通性
    # 目的：验证模型能一句话完成符合人设的自我介绍
    # =====================================================================
    # print("=" * 60)
    # prompt1 = "你好，请用一句话做个自我介绍。"
    # print(f"用户: {prompt1}")
    # response1 = agent.run(prompt1, session_id=session_id)
    # print(f"\nAgent 回复: \n{response1}")

    # =====================================================================
    # 第二轮测试：高德 MCP 工具调用 (天气查询)
    # 目的：验证模型能否在公有云网关下，成功调用天气接口并返回真实天气。
    # =====================================================================
    # print("\n" + "=" * 60)
    # prompt2 = "帮我查一下现在杭州的天气怎么样？"
    # print(f"用户: {prompt2}")
    # response2 = agent.run(prompt2, session_id=session_id)
    # print(f"\nAgent 回复: \n{response2}")

    # =====================================================================
    # 第三轮测试：安全沙箱工具调用 (Python执行)
    # 目的：验证大模型能否利用 Python 代码沙箱安全计算复杂数理问题。
    # =====================================================================
    # print("\n" + "=" * 60)
    # prompt3 = "请用 Python 帮我计算一下 12345 乘以 6789 的结果是多少？"
    # print(f"用户: {prompt3}")
    # response3 = agent.run(prompt3, session_id=session_id)
    # print(f"\nAgent 回复: \n{response3}")

    # =====================================================================
    # 第四轮测试：OAuth2 出站凭据（@require_access_token, USER_FEDERATION）
    # 目的：验证用户委托授权 GitHub 后，以用户身份列出仓库。
    # 前置条件：Identity 控制台已创建 github-provider；
    # 首次运行会打印授权链接，请在浏览器完成授权。
    # =====================================================================
    # print("\n" + "=" * 60)
    # prompt5 = "请列出我的 GitHub 仓库。"
    # print(f"用户: {prompt5}")
    # response5 = agent.run(prompt5, session_id=session_id)
    # print(f"\nAgent 回复: \n{response5}")

    # =====================================================================
    # 第五轮测试：STS Token 出站凭据（@require_sts_token）
    # 目的：验证临时 AK/SK + Security Token 只读访问华为云 IAM。
    # 前置条件：Identity 控制台已创建 iam-users-readonly STS provider。
    # =====================================================================
    # print("\n" + "=" * 60)
    # prompt6 = "请列出华为云的所有 IAM 用户。"
    # print(f"用户: {prompt6}")
    # response6 = agent.run(prompt6, session_id=session_id)
    # print(f"\nAgent 回复: \n{response6}")
    # print("=" * 60)

    # =====================================================================
    # 第六轮测试：Microsoft 365 邮件发送（OAuth2，Demo 简化版无二次确认）
    # 目的：验证 send_email 调用即发送。
    # 前置条件：Identity 控制台已创建 m365-email-provider；
    # 首次调用会打印授权链接，请在浏览器完成授权。
    # =====================================================================
    # print("\n" + "=" * 60)
    # prompt7 = (
    #     "请调用 send_email 给 your@email.com 发送一封测试邮件，"
    #     "主题为\"测试邮件\"，正文为\"你好\"。"
    # )
    # print(f"用户: {prompt7}")
    # response7 = agent.run(prompt7, session_id=session_id)
    # print(f"\nAgent 回复: \n{response7}")
    # print("=" * 60)

    # =====================================================================
    # 第七轮测试：飞书应用信息查询（OAuth2 M2M 出站凭据）
    # 目的：验证 @require_access_token 的 M2M 模式（client credentials）。
    # 前置条件：Identity 控制台已创建 feishu-provider（OAuth2, M2M），
    # 运行环境已配置华为云 AK/SK；首次运行由 AgentArts Identity 自动
    # 换取 tenant_access_token，无需人工授权。
    # =====================================================================
    print("\n" + "=" * 60)
    prompt8 = "请查询当前飞书应用的应用信息。"
    print(f"用户: {prompt8}")
    response8 = agent.run(prompt8, session_id=session_id)
    print(f"\nAgent 回复: \n{response8}")
    print("=" * 60)


def run_web_server():
    """启动 AgentArts Runtime Web 服务（华为云标准 AgentArtsRuntimeApp）。"""
    port = int(os.getenv("AGENT_RUN_PORT", "8080"))
    print(f"启动 AgentArts Web 服务: http://0.0.0.0:{port}")
    print("接口: GET /ping | POST /invocations | WS /ws")
    from app.webapp import handle_request

    # 华为云 SDK 标准启动方式：entrypoint 函数自带 .run()
    handle_request.run(host="0.0.0.0", port=port)


def main():
    parser = argparse.ArgumentParser(description="hw-agent-demo 统一入口")
    parser.add_argument(
        "--test",
        action="store_true",
        help="运行本地自动化测试脚本（默认启动 Web 服务）",
    )
    args = parser.parse_args()

    if args.test:
        run_self_test()
    else:
        run_web_server()


if __name__ == "__main__":
    main()
