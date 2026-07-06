# MCP 主流集成设计

## 背景

MCP，全称 Model Context Protocol，是一种用于连接 AI 应用和外部上下文、工具、数据源的协议。

MCP 官方架构通常分为三类角色：

```text
Host 应用
  管理多个 MCP Client
  负责用户授权、安全策略、模型集成、上下文聚合

MCP Client
  一个 client 连接一个 MCP server
  负责协议协商、消息路由、订阅、通知

MCP Server
  暴露 tools / resources / prompts
  可以是本地进程，也可以是远程服务
```

MCP 是基于 JSON-RPC 的有状态会话协议。一个 Host 可以管理多个 Client，每个 Client 通常和一个 MCP Server 建立一条连接。

## 主流设计一：AI 应用作为 MCP Host

这是 Cursor、Claude Desktop、IDE 插件、AI Agent 后端常见模式。

```text
AI 应用 / IDE / Agent Backend
  -> MCP Host
    -> MCP Client: filesystem server
    -> MCP Client: git server
    -> MCP Client: database server
    -> MCP Client: browser/search server
```

特点：

- AI 应用负责管理用户会话、模型调用、工具权限和上下文注入。
- 每接入一个 MCP Server，就由 Host 管理一个对应的 MCP Client。
- LLM 不直接访问 MCP Server，必须通过 Host 调度。
- Host 可以对工具调用做权限控制、审计和用户确认。

适用场景：

- IDE 代码助手。
- 本地 Agent 应用。
- 企业内部 AI 助手。
- 需要多个外部工具协作的 AI 后端。

## 主流设计二：MCP Gateway / MCP Hub

这是企业级或平台型系统常见模式。

```text
AI 应用
  -> MCP Gateway
    -> GitHub MCP Server
    -> Jira MCP Server
    -> PostgreSQL MCP Server
    -> Internal Docs MCP Server
```

Gateway 统一处理：

- 鉴权。
- 权限策略。
- 审计日志。
- Server 注册与发现。
- Tool 白名单。
- 限流。
- 租户隔离。
- 凭证管理。

优点：

- 便于集中管理多个 MCP Server。
- 更适合企业多用户、多团队、多租户场景。
- 后端应用不需要直接维护大量 server 连接细节。

缺点：

- 前期复杂度更高。
- 需要额外的服务治理、权限模型和运维能力。

适用场景：

- 企业内部 MCP 平台。
- SaaS 型 AI 产品。
- 多团队共享工具生态。
- 需要统一审计和安全策略的环境。

## 主流设计三：应用自身暴露 MCP Server

这种模式是让自己的产品也成为一个 MCP Server，供其他 AI 客户端调用。

```text
Claude / Cursor / ChatGPT / 其他 Host
  -> 产品自己的 MCP Server
    -> tools/list
    -> tools/call
    -> resources/list
    -> resources/read
    -> prompts/list
    -> prompts/get
```

可以暴露的能力示例：

```text
tools:
  run_tests
  search_code
  explain_symbol
  apply_patch
  get_project_tree

resources:
  file:///project/README.md
  file:///project/app/main.py
  git://current/diff

prompts:
  code_review
  explain_error
  generate_tests
```

优点：

- 产品能力可以被其他 AI 客户端复用。
- 可以形成工具生态。
- 对外集成边界清晰。

缺点：

- 需要设计稳定的工具、资源和权限边界。
- 需要考虑外部 Host 的调用安全。
- 需要维护 MCP Server 协议兼容性。

## 主流设计四：MCP Client + 本地 stdio Server

本地开发工具常见使用 stdio transport。

```text
AI 应用
  -> 启动子进程: filesystem-mcp-server
  -> stdin 发送 JSON-RPC
  -> stdout 接收 JSON-RPC
```

优点：

- 本地工具接入简单。
- 不需要额外 HTTP 服务。
- 适合文件系统、Git、本地命令、SQLite 等本地资源。

缺点：

- 进程生命周期管理复杂。
- 需要处理 server 崩溃、重启、日志和超时。
- 多用户部署时隔离成本更高。

适用场景：

- 本地 IDE 插件。
- 个人开发助手。
- 单用户本地 Agent。
- 文件系统和 Git 这类本地工具。

## 主流设计五：MCP Client + 远程 Streamable HTTP Server

远程 MCP Server 适合云服务和企业系统。

```text
AI 应用
  -> Streamable HTTP Transport
  -> Remote MCP Server
```

典型远程服务：

- GitHub。
- Notion。
- Linear。
- Jira。
- 数据库网关。
- 企业内部知识库。

优点：

- 适合云端部署。
- 便于鉴权和统一访问控制。
- 更容易集成 SaaS 服务。

缺点：

- 需要处理网络错误、重连、认证过期。
- 远程工具调用延迟更高。
- 需要更严格的数据泄露防护。

## MCP 三类核心能力

### Tools

Tools 是可以执行动作的能力。

示例：

```text
tools/list
tools/call
```

常见工具：

- 搜索代码。
- 查询数据库。
- 调用外部 API。
- 运行测试。
- 创建 issue。
- 写文件。

工具通常需要定义：

- `name`
- `description`
- `inputSchema`

### Resources

Resources 是可以读取的上下文。

示例：

```text
resources/list
resources/read
```

常见资源：

- 文件内容。
- Git diff。
- 数据库 schema。
- 文档页面。
- 项目结构。

资源通常使用 URI 标识：

```text
file:///project/README.md
git://current/diff
db://schema/users
```

### Prompts

Prompts 是 MCP Server 暴露的提示词模板或工作流入口。

示例：

```text
prompts/list
prompts/get
```

常见 prompt：

- code_review
- explain_error
- generate_tests
- summarize_project

## 安全设计重点

MCP 不是让模型无限制调用外部工具的机制。主流设计都会在 Host 或 Gateway 层增加安全策略。

重点风险：

- 路径穿越。
- 读取 `.env`、SSH key、token 等敏感文件。
- 执行任意 shell 命令。
- 恶意 MCP Server 的工具描述注入。
- 跨 server 数据泄露。
- 未经用户确认就写文件或调用外部 API。
- 工具返回内容污染模型上下文。

推荐控制方式：

```text
MCP server tools
  -> registry 登记
  -> policy 过滤
  -> capability 分级
  -> 用户/项目授权
  -> 必要时用户确认
  -> 审计日志
  -> 再暴露给模型
```

常见权限分级：

```text
read_only       自动允许
write_project   需要项目授权
execute_command 需要用户确认
external_api    需要服务授权
dangerous       默认禁用
```

## 总结

MCP 主流集成有五种常见形态：

- AI 应用作为 MCP Host。
- 企业级 MCP Gateway / Hub。
- 应用自身暴露 MCP Server。
- 本地 stdio MCP Server。
- 远程 Streamable HTTP MCP Server。

对于开发工具和代码助手，最常见的路线是：

```text
先做 MCP Host + Client Manager
  -> 支持 stdio
  -> 支持 Streamable HTTP
  -> 接入 tools/resources
  -> 加权限和审计
  -> 后续再暴露自己的 MCP Server
```
