# 华为云 AgentArts 部署方案

本文档说明如何把本 Demo 部署到华为云 AgentArts 平台（后端运行时）与
Cloudflare Pages（公网 Web 前端），端到端可被真实用户通过浏览器访问。

## 1. 部署架构

```
浏览器（https://hw-agent-demo.pages.dev）
  ├─ MSAL 获取 Microsoft Entra id_token（CUSTOM_JWT 入站）
  └─ Pages Function 同源代理 /invocations（零 CORS）
        ▼
AgentArts 平台
  │  控制台 / API 调用
  ▼
Gateway（CUSTOM_JWT 校验 id_token，注入 WAT / 用户身份 header）
  ▼
AgentArts Runtime（ARM64 容器，镜像来自 SWR）
  ├── /invocations   → @app.entrypoint → TravelAgent（LangGraph）
  │                     ├─ JSON（CLI / 平台调试）
  │                     └─ SSE（Web 前端：授权链接 auth_required 实时推送）
  ├── /ping          → 健康检查
  └── /auth/*        → Entra ID 本地登录（自建路由，仅 localhost 直连形态）
  │
  ├── 出站：AgentArts Identity（WAT 换 token）
  │     ├── github-provider      OAuth2 USER_FEDERATION
  │     ├── iam-users-readonly   STS
  │     ├── m365-email-provider  OAuth2 USER_FEDERATION
  │     └── feishu-provider      OAuth2 M2M
  └── 模型：DeepSeek（api.deepseek.com）
```

代码侧已完成的工作：

- 标准运行时封装：[webapp.py](app/webapp.py) 使用 `AgentArtsRuntimeApp` + `@app.entrypoint`
- 平台标准镜像：[Dockerfile](Dockerfile)（非 root 用户、`DOCKER_CONTAINER=true`、`python main.py` 启动）
- CLI 部署配置：[.agentarts_config.yaml](.agentarts_config.yaml)（SWR、运行时、环境变量、authorizer）
- 构建上下文排除：[.dockerignore](.dockerignore)（venv / .env / .git 不进镜像）

## 2. 平台侧准备（控制台）

部署前确认以下资源已创建：

| 资源 | 状态/操作 |
| --- | --- |
| 记忆库 / 沙箱 / MCP 网关 | 本 Demo 未使用，无需创建 |
| Identity: github-provider | 已创建（OAuth2，USER_FEDERATION） |
| Identity: iam-users-readonly | 已创建（STS，委托 iam-users-readonly） |
| Identity: m365-email-provider | 确认已创建（OAuth2，USER_FEDERATION，Mail.Send 等权限） |
| Identity: feishu-provider | 已创建（OAuth2，M2M） |
| Entra 应用注册 | 已创建（本地方向已可用） |
| SWR 组织/仓库 | 配置了自动创建，无需手动 |
| 运行时委托 | `DefaultAgentArtsRuntimeAgency`（默认，可保留） |

## 3. ARM64 构建环境

AgentArts Runtime 为 **ARM64（鲲鹏）** 架构，且 `agentarts deploy` 的 `docker build` 不带 `--platform`，**必须在 ARM64 Linux 上构建镜像**。

> 走 GitHub Actions 时无需自备构建机：`.github/workflows/backend-agentarts.yml`
> 已使用 GitHub 官方 ARM64 runner（`ubuntu-24.04-arm`）。本节适用于手工部署、
> 自托管 runner 或 SSH 部署的场景。

准备一台鲲鹏 ECS（如 Ubuntu 22.04 ARM64）：

```bash
# 安装 Docker
sudo apt update && sudo apt install -y docker.io
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors":[
    "https://docker.m.daocloud.net",
    "https://dockerproxy.net",
    "https://mirror.baidubce.com"
  ]
}
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker

export DOCKER_BUILDKIT=0

# Python venv
sudo apt install python3
sudo apt install -y python3.12-venv
```

## 4. 上传代码

把项目源码传到 ECS（不用传 venv / .env / .git）：

## 5. 配置 CLI 与凭证

```bash
# 进入项目目录，如cd /opt/hw-agent-demo
python3 -m venv venv
source venv/bin/activate
pip install agentarts-sdk -i https://repo.huaweicloud.com/repository/pypi/simple --trusted-host repo.huaweicloud.com

export HUAWEICLOUD_SDK_AK="your-access-key"
export HUAWEICLOUD_SDK_SK="your-secret-key"
```

## 6. 部署前回填配置

编辑 `.agentarts_config.yaml`：

1. `swr_config`：如不想用默认组织/仓库，改成你自己的（`organization` / `repository`）
2. `identity_configuration`：已配置 `CUSTOM_JWT`，discovery_url 固定为租户 `d72bac4d-feb6-432b-b2be-79502440c674`（只允许该租户账号，与前端 `ENTRA.tenant` 一致），`allowed_clients / allowed_scopes` 留空；改动后必须重新 `agentarts deploy`
3. `environment_variables` 中 `OAUTH2_CLIENT_ID/SECRET` 仅供 **localhost 自建登录** 使用，云上 CUSTOM_JWT 形态不读取，无需修改

> 环境变量是明文写入 yaml（CLI 不做 `${VAR}` 展开），Demo 阶段可接受；正式环境建议使用平台密钥管理。

## 7. 执行部署

```bash
agentarts deploy
```

CLI 自动完成：构建 ARM64 镜像 → 推 SWR → 创建/更新 AgentArts 运行时。可选参数：

```bash
agentarts deploy --tag v1.0.0          # 镜像版本
agentarts deploy --swr-org my-org --swr-repo my-repo
agentarts deploy --skip-build          # 仅用已有镜像创建运行时
```

## 8. 验证

部署完成后：

1. **控制台**：AgentArts → 运行时 → hw-agent-demo，状态应为"运行中"，可看到公网访问地址
2. **平台调试**：在运行时详情页打开调试/测试，发送：
   ```json
   {"message": "你好，请用一句话自我介绍"}
   ```
3. **命令行**：authorizer 为 CUSTOM_JWT，CLI 需要携带有效的 Microsoft Entra `id_token`（可在已登录的 Pages 页面 DevTools → Network 里复制 `Authorization`，或改用控制台调试）
4. **入站登录**：浏览器打开 `https://hw-agent-demo.pages.dev` → 开始对话 → Microsoft 登录（Entra 应用需已添加 Pages 域名重定向 URI）
5. **出站工具**：在 Pages 聊天界面分别测试 GitHub / IAM / 邮件 / 飞书；首次调用时授权链接以授权卡片实时显示，点击完成授权后请求自动继续

## 9. 前端 Web 部署：Cloudflare Pages（CUSTOM_JWT + MSAL）

AgentArts Gateway 只暴露受控 API 路径（直接访问 defaultgw 域名的 `/` 会返回
`404 No matching policy`），不会托管运行时里的自定义页面。因此前端单独部署到
Cloudflare Pages：静态页免费 HTTPS（`*.pages.dev`，无需备案），Pages Function
做同源 `/invocations` 代理，浏览器零 CORS；入站认证由浏览器 MSAL 取 Entra
`id_token`、Gateway `CUSTOM_JWT` 校验，与 personal-assistant 保持一致。

### 9.1 相关文件

| 文件 | 作用 |
| --- | --- |
| [wrangler.toml](wrangler.toml) | Pages 项目名 `hw-agent-demo`；`AGENTARTS_INVOCATIONS_URL` = 第 7 节部署出的网关调用地址 |
| [functions/invocations.js](functions/invocations.js) | Pages Function：把浏览器同源 `POST /invocations` 转发到网关；仅透传 `authorization / content-type / accept / x-hw-agentarts-session-id` |
| [static/index.html](static/index.html) | 单页 UI；非 localhost 域名自动启用 MSAL 登录；`ENTRA.clientId / tenant` 在此配置 |

### 9.2 前置条件

- Node.js 18+ 与 Cloudflare 账号（免费版即可）
- 第 7 节已完成：运行时以 `CUSTOM_JWT` 部署（`.agentarts_config.yaml` 已配好）
- Entra 应用注册已追加 Web 平台重定向 URI：`https://hw-agent-demo.pages.dev`
  （保留 `http://localhost:8080/auth/callback` 供本地模式使用）

### 9.3 部署步骤

```bash
# 1. 项目根目录登录 Cloudflare
npx wrangler login

# 2. 部署（wrangler 按 pages_build_output_dir = ./static 上传，
#    并自动把 functions/ 作为 Pages Function 一起发布）
npx wrangler pages deploy
# 首次运行若提示选择 production branch，填 main 即可
```

如把仓库接入 GitHub/GitLab，也可以在 Pages 控制台创建项目并开启
“Build outputs directory = static”的自动构建，两种方式等价。

### 9.4 验证清单

1. 打开 `https://hw-agent-demo.pages.dev`，应看到落地页（未登录）
2. 点“登录”跳转 Microsoft，授权后回跳并进入聊天界面
3. 发送“你好，请用一句话做个自我介绍”，应流式返回结果
4. 发送“列出我的 GitHub 仓库”：若未授权，界面实时出现授权卡片，点开后完成
   GitHub 授权，原请求自动继续
5. 同样验证 IAM（STS，免授权）、邮件（M365 授权卡片）与飞书（M2M，免授权）

### 9.5 与 localhost 模式的差异

| 维度 | localhost:8080 | Cloudflare Pages |
| --- | --- | --- |
| 静态页托管 | `python main.py` 同源托管 | Pages 静态托管 + Function 代理 |
| 入站登录 | 后端 `/auth/login` 服务端 OAuth2 | 浏览器 MSAL 拿 id_token，网关 CUSTOM_JWT 校验 |
| 出站授权链接 | 控制台打印 + 界面卡片（SSE） | 界面卡片（SSE，经网关透传） |
| 用途 | 快速开发调试 | 公网演示/真实用户访问 |

## 10. 更新与销毁

```bash
agentarts deploy                      # 代码/配置变更后重新部署（更新运行时）
agentarts destroy --agent hw-agent-demo   # 删除运行时（保留 SWR 镜像）
```

## 11. 常见问题

| 现象 | 原因/处理 |
| --- | --- |
| 容器启动失败：端口占用 | 镜像内 8080 被占用？确认 `AGENT_RUN_PORT` 未被平台注入为其他值 |
| 登录失败 `AADSTS50011` | `https://hw-agent-demo.pages.dev` 未加入 Entra 应用注册的重定向 URI（Web/SPA 平台） |
| 云上登录退回 dev mode | 仅发生在 localhost：`OAUTH2_CLIENT_ID` 为空时 `/auth/login` 签发 dev-user；云上走 CUSTOM_JWT，与该变量无关 |
| 出站工具报未授权/凭据错误 | 确认 Identity provider 已创建且名称与 yaml 一致；云上凭据走 WAT，无需 AK/SK |
| 模型调用 Connection error | 运行时网络策略需放通 `api.deepseek.com` 出网 |
| 构建 x86 镜像部署失败 | 必须在 ARM64 环境构建（第 3 节） |
| 直接访问 defaultgw 域名根路径返回 `404 No matching policy` | 正常现象：网关不托管静态页；前端请访问 `https://hw-agent-demo.pages.dev` |
| Pages 打开后点登录无反应 | MSAL 库经 jsdelivr CDN 加载失败（网络受限）；下载 npm 包内 `lib/msal-browser.min.js` 放到 `static/` 并改为本地相对路径引用 |
| Pages Function 返回 500 “proxy is not configured” | `wrangler.toml` 的 `AGENTARTS_INVOCATIONS_URL` 未配置或与运行时实际域名不一致 |
| 聊天 401 未登录 | id_token 过期时前端会自动静默续期；长时间未使用则需重新登录；确认运行时已按 CUSTOM_JWT 重新部署 |
