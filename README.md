# HW AgentArts Demo

基于华为云 **AgentArts** 高代码平台的智能体演示项目，聚焦 **InBound 入站认证** 与 **OutBound 出站凭据** 两大能力：

- **入站（InBound）**：Microsoft Entra ID（Azure AD）登录。本地模式由后端自建 OAuth2.0 授权码流程并签发会话 JWT；公网模式由前端 MSAL 获取 Entra `id_token`，AgentArts Gateway 以 `CUSTOM_JWT` authorizer 校验并注入用户身份
- **出站（OutBound）**：通过 AgentArts Identity 注入 OAuth2 用户委托、STS 临时凭据与 M2M 应用身份，调用 GitHub、华为云 IAM、Microsoft 365 邮件和飞书开放平台，全程不接触真实密钥

采用华为云标准运行时 [`AgentArtsRuntimeApp`](https://support.huaweicloud.com/highcode-agentarts/) 封装：`@app.entrypoint` 暴露 `POST /invocations`、`@app.ping` 健康检查、`handler.run()` 启动，可直接部署到 AgentArts 平台。

> Demo 已验收通过，后续演进方向（前后端分离 / GitHub Actions 自动化部署 / AgentArts 组件集成与产品化）见文末「后续工作方向」章节。

## 功能特性

| 方向 | 能力 | 说明 |
| --- | --- | --- |
| 入站 | Microsoft Entra ID 登录 | OAuth2.0 授权码流程，支持个人/组织账户，未配置凭据时自动进入开发模式 |
| 出站 | GitHub 仓库 | `@require_access_token`（USER_FEDERATION）以用户身份列出仓库 |
| 出站 | 华为云 IAM | `@require_sts_token` 只读列出 IAM 用户 |
| 出站 | Microsoft 365 邮件 | `@require_access_token`（USER_FEDERATION）调用即发送，授权链接实时显示在界面 |
| 出站 | 飞书应用信息 | `@require_access_token`（M2M）以应用身份查询应用详情 |

授权体验：GitHub / Microsoft 365 需要用户授权时，运行时通过 **SSE 流**即时推送 `auth_required` 事件，授权链接同时打印到服务控制台并显示为 Web 界面授权卡片，授权完成后原请求自动继续。

## 技术栈

- Python 3.10+ / 华为云标准运行时 `AgentArtsRuntimeApp`（基于 Starlette）
- LangGraph（工具调用循环）+ LangChain OpenAI 兼容接口（DeepSeek 模型）
- agentarts-sdk（记忆/Identity 之外的平台能力按需启用；本项目未启用记忆、沙箱与 MCP 网关）
- 前端：单页静态 HTML/CSS/JS（深色科技风）；公网形态部署于 Cloudflare Pages，Pages Function 做同源 `/invocations` 代理

## 目录结构

```
hw-agent-demo/
├── main.py                  # 统一入口：python main.py 启动 Web / --test 自测
├── app/
│   ├── config.py            # .env 加载、模型与全局配置
│   ├── oauth2.py            # InBound OAuth2.0（Entra ID 授权码 + 会话 JWT）
│   ├── webapp.py            # AgentArtsRuntimeApp 应用、/invocations、扩展路由
│   ├── agent.py             # TravelAgent：LangGraph 图 + system prompt
│   └── tools/
│       ├── __init__.py      # build_tools() 工具注册工厂
│       ├── identity_tools.py    # GitHub OAuth2 / 华为云 IAM STS
│       ├── email_tools.py       # Microsoft 365 邮件
│       ├── feishu_tools.py      # 飞书 M2M
│       └── pending_auth.py      # 授权链接公共暂存（控制台 + 界面）
├── static/index.html        # 前端页面（Landing / 登录 / 聊天）
├── functions/invocations.js # Cloudflare Pages Function：同源代理到 Gateway
├── wrangler.toml            # Cloudflare Pages 项目配置
├── requirements.txt
├── Dockerfile               # AgentArts 平台标准镜像
├── .agentarts_config.yaml   # agentarts CLI 部署配置
└── .env                     # 本地配置（不入镜像）
```

## 快速开始

### 1. 环境准备

```bash
python3 -m venv venv
# Windows: venv\Scripts\activate
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 .env

从 `.env.example` 复制为 `.env`（或按以下键自行创建），至少配置：

```dotenv
# 模型（DeepSeek OpenAI 兼容接口）
MODEL_API_KEY=your-model-api-key
MODEL_NAME=deepseek-v4-flash
MODEL_URL=https://api.deepseek.com

# AgentArts 区域（Identity 出站凭据使用）
HUAWEICLOUD_SDK_REGION=cn-southwest-2

# 本地 Identity 鉴权（本地联调需要，云上自动使用 WAT 无需配置）
HUAWEICLOUD_SDK_AK=your-ak
HUAWEICLOUD_SDK_SK=your-sk

# 出站凭据 provider 名称（与 AgentArts Identity 控制台创建的一致）
GITHUB_PROVIDER_NAME=github-provider
IAM_USERS_PROVIDER_NAME=iam-users-readonly
M365_EMAIL_PROVIDER_NAME=m365-email-provider
FEISHU_PROVIDER_NAME=feishu-provider

# 入站 Entra ID（不填则进入开发模式）
OAUTH2_CLIENT_ID=
OAUTH2_CLIENT_SECRET=
```

### 3. 启动

```bash
python main.py
```

访问 <http://localhost:8080>：

- 未登录：Landing 页 + 4 个能力卡片 → 点"开始对话"进入登录
- 登录：跳转 Microsoft 完成 OAuth2.0 授权码流程（未配置凭据时自动签发 dev-user）
- 聊天：顶部工具条直接展开 4 个工具提示词，点击自动填入输入框；"清除会话"清空聊天记录

## 入站认证：Microsoft Entra ID

两种运行形态：

- **本地（localhost:8080）**：页面由 `python main.py` 同源托管，后端走 `/auth/login` 服务端 OAuth2.0 授权码流程（本仓库内置实现）
- **公网（Cloudflare Pages + Gateway）**：页面托管在 Pages，前端用 MSAL 获取 Entra `id_token`，由 AgentArts Gateway `CUSTOM_JWT` authorizer 校验；这是与 personal-assistant 一致的平台标准形态

1. Azure 门户 → Microsoft Entra ID → 应用注册 → 新注册
   - 支持的帐户类型：任何组织目录中的帐户和个人 Microsoft 帐户
   - 重定向 URI（Web）：`http://localhost:8080/auth/callback`
   - API 权限：添加 Microsoft Graph 委托权限 **User.Read**
2. "证书和密码"创建客户端密码
3. 填入 `.env`：

```dotenv
OAUTH2_CLIENT_ID=应用(客户端)ID
OAUTH2_CLIENT_SECRET=客户端密码值
```

4. 重启服务。登录跳转 `login.microsoftonline.com`，回调换取 token 后用 Graph `/v1.0/me` 获取显示名，后端签发 HS256 会话 JWT。

> 未配置 `OAUTH2_CLIENT_ID` 时自动进入开发模式（`/auth/login` 直接签发 dev-user 会话），方便先跑通链路。

## 公网 Web 部署（Cloudflare Pages + CUSTOM_JWT）

参考 personal-assistant 的做法：静态前端放到 Cloudflare Pages（免费 HTTPS，`*.pages.dev` 无需备案），用 Pages Function 做同源代理，浏览器零 CORS；入站交给平台 Gateway 的 CUSTOM_JWT，运行时不再需要暴露自建 `/auth/*`。

### 1. Entra 应用注册补充重定向 URI

Azure 门户 → 应用注册 → 认证，为当前应用追加 **Web 平台重定向 URI**：

```text
https://hw-agent-demo.pages.dev
```

### 2. 运行时启用 CUSTOM_JWT 并重新部署

`.agentarts_config.yaml` 已把 authorizer 切为 `CUSTOM_JWT`（discovery_url / allowed_audience / allowed_clients / allowed_scopes 与 Entra 应用一一对应）。在 ARM64 环境重新执行：

```bash
agentarts deploy
```

`CUSTOM_JWT` 的 `discovery_url` 必须和登录用户 token 的租户一致：个人 Microsoft 账号用 `consumers`（对应 `9188040d-6c67-4c5b-b112-36a304b66dad`），组织账号用应用注册所在 Directory (tenant) ID；同时把 [static/index.html](static/index.html) 里 `ENTRA.tenant` 改成相同值（当前为 `consumers`）。

### 3. 部署前端到 Cloudflare Pages

```bash
# 项目根目录
npx wrangler login
npx wrangler pages deploy
```

项目名 `hw-agent-demo`（见 `wrangler.toml`），部署完成后访问 `https://hw-agent-demo.pages.dev`。浏览器登录 → MSAL 跳转 Microsoft → 回跳后进入聊天；`POST /invocations` 由 `functions/invocations.js` 转发到 `AGENTARTS_INVOCATIONS_URL` 指定的 Gateway 域名。

> 出站授权链接走 SSE 事件（`auth_required`），不需要运行时的 `/auth/pending` 路由暴露到公网。

> MSAL 库通过 jsdelivr CDN 加载；若所在网络访问受限，可从 npm 包 `@azure/msal-browser` 中取出 `lib/msal-browser.min.js` 放到 `static/` 下，并把 [static/index.html](static/index.html) 里的 `<script src>` 改成相对路径。

## 出站凭据：AgentArts Identity

在 AgentArts Identity 控制台创建 Credential Provider，代码只保存 provider 名称：

| 工具 | Provider 类型 | auth_flow | 控制台名称 |
| --- | --- | --- | --- |
| GitHub 仓库 | OAuth2（GitHub vendor） | USER_FEDERATION | github-provider |
| 华为云 IAM | STS（委托 iam-users-readonly） | - | iam-users-readonly |
| Microsoft 365 邮件 | OAuth2（Microsoft vendor） | USER_FEDERATION | m365-email-provider |
| 飞书应用信息 | OAuth2（飞书 vendor） | M2M | feishu-provider |

授权流程由 SDK 的 `@require_access_token` 自动处理：

- 未授权：Identity 服务返回授权 URL → 打印控制台 + 前端授权卡片 → 用户点击完成授权 → SDK 轮询到 token 后原请求自动继续
- 已授权：复用 Identity 服务的 token vault，不再重复弹窗
- 想每次强制授权：`.env` 设置 `M365_FORCE_AUTH=true`（GitHub 同理可参考）

## 自测

```bash
python main.py --test
```

按轮次验证模型连通与出站工具（GitHub / IAM / 邮件 / 飞书），需要真实授权时控制台会打印授权链接。

## 部署到华为云 AgentArts

### 前置条件

- **ARM64（鲲鹏）Linux 服务器**，已安装 Docker（CLI 的 `docker build` 不带 `--platform`，必须在 ARM64 环境构建）
- 安装 agentarts-sdk（自带 `agentarts` CLI）

### 步骤

```bash
# 1. 配置华为云凭证（CLI 推 SWR、创建运行时用）
export HUAWEICLOUD_SDK_AK="your-ak"
export HUAWEICLOUD_SDK_SK="your-sk"

# 2. 项目根目录执行部署（cloud 模式默认：构建镜像 -> 推 SWR -> 创建/更新运行时）
agentarts deploy

# 可选参数
agentarts deploy --tag v1.0.0
agentarts deploy --swr-org xxx --swr-repo yyy
```

部署配置见 `.agentarts_config.yaml`：`authorizer_type: CUSTOM_JWT`（公网 Web 形态，Gateway 校验 Entra id_token），环境变量为真实值（CLI 不做 `${VAR}` 占位符展开）；`.env` 不进入镜像，云上由平台注入环境变量，出站凭据由 Gateway 注入的 Workload Access Token 自动获取。

## 后续工作方向（Roadmap）

> 记录日期：2026-09-02
>
> 当前 Demo 已验收通过：具备 Entra ID（CUSTOM_JWT）入站认证、Cloudflare Pages
> 前端、AgentArts Runtime 后端、GitHub / IAM / M365 / 飞书出站凭据，以及 SSE
> 授权卡片交互。后续按三条主线演进：**前后端分离 → GitHub + Actions 自动化 →
> 平台组件集成与产品化**。

### 方向一：前后端代码完全分离

#### 现状

单仓库单目录结构：后端（`app/`、`main.py`、`Dockerfile`、`.agentarts_config.yaml`）
与前端静态页（`static/index.html`）以及 Pages Function（`functions/`）混在一起，
`.env` 同时在本地被后端读取。

#### 目标

两个可独立开发、独立部署的代码单元：

```
hw-agent-demo/
├── backend/                  # AgentArts Runtime（Python）
│   ├── app/
│   ├── main.py
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .agentarts_config.yaml
│   └── .env.example
└── frontend/                 # Cloudflare Pages（静态 UI + Functions）
    ├── index.html            # 或引入 Vite/React 后的 src/
    ├── functions/invocations.js
    ├── wrangler.toml
    └── .env.example          # 仅非机密 VITE_* / Pages vars
```

如果团队与权限边界需要更彻底隔离，也可以拆成两个 Git 仓库（`hw-agent-demo-backend`
与 `hw-agent-demo-frontend`），通过契约文档联动。

#### 拆分要点

1. **契约冻结**（先写文档再动代码）：
   - `POST /invocations`，`Accept: text/event-stream` 时返回 SSE；
   - SSE 事件固定为 `start / auth_required / result / error`；
   - 请求头：`Authorization: Bearer <id_token>`、`x-hw-agentarts-session-id`。
2. **配置分离**：浏览器可见配置（Entra client id / tenant）只在前端；网关地址
   `AGENTARTS_INVOCATIONS_URL` 只进 Pages Function 的 `[vars]`；AK/SK、client
   secret 等只进后端环境变量，全部提供 `.env.example` 而非真实值。
3. **本地联调方式固定**：前端开发服务器或 `wrangler pages dev` 将 `/invocations`
   代理到本地 `python main.py`，后端继续支持模拟网关头
   （`X-HW-AgentGateway-User-Id: dev-user`）。
4. **验收标准**：删除任一目录后另一个仍可独立构建、独立部署、独立写 README；
   契约变更必须同步升级两端并跑通联调用例。

### 方向二：GitHub 托管 + GitHub Actions 自动化部署

#### 前置

- 代码推送到 GitHub（当前目录不是 git 仓库，需要 `git init` + 远程仓库）；
- 开启分支保护（main 要求 PR + 状态检查通过）；
- 仓库 Secrets 清单：

| Secret | 用途 | 生效端 |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | Pages 部署（权限 Pages:Edit） | 前端 workflow |
| `CLOUDFLARE_ACCOUNT_ID` | 指定 Pages 账号 | 前端 workflow |
| `HUAWEICLOUD_SDK_AK` / `HUAWEICLOUD_SDK_SK` | AgentArts CLI 推 SWR / 部署运行时 | 后端 workflow |
| `ECS_SSH_HOST` / `ECS_SSH_USER` / `ECS_SSH_KEY` | ARM64 构建机（鲲鹏 ECS） | 后端 workflow |

#### 前端自动化

`.github/workflows/frontend-deploy.yml`：

- 触发：`paths: ['frontend/**']`，分支 `main`；
- 步骤：checkout → `npx wrangler pages deploy`（`CLOUDFLARE_API_TOKEN` 认证）；
- PR 场景：部署到 preview 分支（`--branch=pr-<number>`）并回写评论链接；
- 冒烟：`curl` 页面 200 + 可选调用 `/invocations` 验证代理连通。

#### 后端自动化

`agentarts deploy` 必须在 **ARM64 + Docker** 环境构建镜像，三条路线：

1. **推荐：把鲲鹏 ECS 注册为 GitHub Actions 自托管 runner**，CI 内直接执行
   `agentarts deploy`，链路完整、日志可见；
2. 或 workflow 通过 SSH 到 ECS 执行部署脚本（简单，但 CI 与构建机耦合弱、
   回滚和日志不如 runner）；
3. GitHub 官方 ARM64 runner（公仓免费、私仓计费）作为备选。

#### 发布策略

- `main` = 生产；push 即自动部署（demo 阶段可接受）；
- 后端变更涉及 authorizer / 环境变量时，提醒人工确认（CUSTOM_JWT、重定向 URI
  这类云端配置无法完全被 workflow 覆盖）；
- 冒烟测试账号与测试 Entra id_token 单独管理，不放仓库。

### 方向三：集成 AgentArts 其它组件，逐步产品化

#### 阶段 A：补齐平台能力

| 能力 | 说明 |
| --- | --- |
| Memory / 会话记忆 | 当前为无状态单轮；引入 LangGraph checkpoint 与会话隔离（参考 personal-assistant `{user_id}:{session_id}`），再接入 AgentArts Memory |
| 安全沙箱 | 需要代码执行/附件解析等场景时启用沙箱，作为工具之一按需注册 |
| MCP Gateway | 类似 personal-assistant 的 GitHub MCP read-only Target，把“网关代理 + STS 签名”沉淀为可复用工具 |
| 敏感操作 Guard | 邮件发送、回复、删除等改为预览 + 二次确认（demo 阶段已去掉，产品化必须补回） |
| 流式体验 | 当前结果整段返回；后续可把 LLM token 级增量与 reasoning 事件加入 SSE 协议 |

#### 阶段 B：工程化加固

- **密钥管理**：环境变量明文 → 平台密钥管理 / CSMS，`.agentarts_config.yaml`
  不再携带真实 secret；
- **身份收敛**：CUSTOM_JWT 从单应用走向多租户（audience / tenant 白名单化），
  前端补齐 token 过期与多账号切换；
- **可观测性**：日志结构化 + trace/metrics 告警接入，记录每次 invocations 的
  user/session/工具调用/耗时；
- **配额与成本**：并发上限、token 用量、出站调用频控；
- **发布与灰度**：多环境（dev/staging/prod）、回滚策略、部署审批。

#### 阶段 C：产品化

- 域名：备案域名 + CDN/自定义域名替换 `*.pages.dev`；
- 合规与隐私：日志脱敏（邮箱/姓名）、数据保留策略、用户授权记录可审计；
- 运营：管理后台、用量统计、帮助文档、版本说明；
- 商业模式验证：围绕“入站 + 出站身份”场景沉淀可复制模板，输出标准接入文档。

### 建议执行顺序

1. **方向一（约 1–2 天）**：契约文档 + 目录拆分，纯重构不引入新依赖；
2. **方向二（约 1–2 天）**：GitHub 入库、workflow 打通前端部署，后端先以
   SSH/自托管 runner 跑通 `agentarts deploy`；
3. **方向三**：按业务优先级逐项接入（建议先做 Memory 与 Guard，再做工程化与产品化）。

### 风险与注意点

- ARM64 构建环境是后端 CI 的主要约束，先解决 runner 再谈全自动；
- CUSTOM_JWT 的 CI 冒烟需要真实测试账号 id_token，链路偏复杂，demo 阶段可退化为
  控制台调试 + Pages 人工回归；
- 前后端拆仓库会引入版本联动成本，契约与联调用例要先行。
