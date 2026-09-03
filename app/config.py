"""应用配置：模型与平台组件配置（读取 .env）。"""
import os

import urllib3
from dotenv import load_dotenv

# 从项目根目录加载 .env 到进程环境
load_dotenv()

# 禁用内网自签名证书的 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ModelConfig:
    """模型配置类。"""

    def __init__(self):
        self.name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
        self.url = os.getenv("MODEL_URL", "https://api.modelarts-maas.com/openai/v1")
        self.api_key = os.getenv("MODEL_API_KEY")
        if not self.api_key:
            raise ValueError("[错误] 未找到 MODEL_API_KEY！请在 .env 文件中进行配置。")
        self.model_type = os.getenv("MODEL_TYPE", "openai")


class AgentConfig:
    """Agent 全局配置类，整合模型与平台组件配置。"""

    def __init__(self):
        self.model = ModelConfig()

        # 区域配置（AgentArts Identity 出站凭据使用）
        self.region = os.getenv("HUAWEICLOUD_SDK_REGION", "cn-southwest-2")

        # 全局递归控制（防止大模型无限调用工具陷入死循环）
        self.max_iterations = int(os.getenv("MAX_ITERATIONS", "50"))
        self.session_ttl = int(os.getenv("SESSION_TTL", "3600"))


config = AgentConfig()
