# CodeMate MCP 集成设计

## 目标

CodeMate 当前是一个 FastAPI 后端项目，已经具备基础 AI 对话接口、模型适配器抽象和 SSE 流式输出能力。

MCP 集成的目标是让 CodeMate 可以连接外部工具和上下文服务，例如：

- 文件系统。
- Git。
- 数据库。
- 文档知识库。
- Issue 系统。
- 浏览器或搜索服务。
- 企业内部 API。

推荐优先把 CodeMate 设计为：

```text
MCP Host + MCP Client Manager
```

也就是 CodeMate 先消费外部 MCP Server，再逐步考虑把自己也暴露成 MCP Server。

## 推荐架构

```text
用户请求
  -> Chat API
  -> ChatService
  -> ModelAdapter
  -> 模型判断是否需要工具
  -> ToolRouter
  -> MCPClientManager
  -> MCP Client
  -> MCP Server
  -> 工具结果返回模型
  -> SSE 输出给客户端
```

整体分层：

```text
app/mcp/
  config.py
  registry.py
  manager.py
  client.py
  protocol.py
  tool_router.py
  resources.py
  prompts.py
  security.py
  transports/
    stdio.py
    streamable_http.py
```

## 模块职责

### `config.py`

负责 MCP Server 配置解析。

建议配置结构：

```text
servers:
  filesystem:
    transport: stdio
    command: npx
    args:
      - -y
      - "@modelcontextprotocol/server-filesystem"
      - "."

  internal_docs:
    transport: streamable_http
    url: https://example.com/mcp
    headers:
      Authorization: Bearer xxx
```

第一阶段可以先用环境变量或本地配置文件，后续再接数据库。

### `registry.py`

负责维护已注册的 MCP Server。

主要职责：

- 记录 server 名称。
- 记录 transport 类型。
- 记录连接参数。
- 记录启用/禁用状态。
- 记录 capability 白名单。

### `manager.py`

负责管理多个 MCP Client。

主要职责：

- 启动 server 连接。
- 初始化协议握手。
- 维护 client 生命周期。
- 处理断线重连。
- 汇总 tools/resources/prompts。
- 根据 server 名称路由调用。

示例概念：

```python
class MCPClientManager:
    async def start_all(self) -> None:
        ...

    async def list_tools(self) -> list[MCPTool]:
        ...

    async def call_tool(self, server: str, name: str, arguments: dict) -> MCPToolResult:
        ...
```

### `client.py`

封装单个 MCP Client。

一个 `MCPClient` 对应一个 MCP Server 连接。

主要职责：

- `initialize`
- `tools/list`
- `tools/call`
- `resources/list`
- `resources/read`
- `prompts/list`
- `prompts/get`
- JSON-RPC request/response 匹配

### `protocol.py`

定义 MCP/JSON-RPC 数据结构。

建议包含：

- `JSONRPCRequest`
- `JSONRPCResponse`
- `JSONRPCError`
- `MCPTool`
- `MCPResource`
- `MCPPrompt`
- `MCPToolResult`

这样可以避免业务层到处手写 dict。

### `tool_router.py`

负责把 MCP tools 转换成模型可调用工具，并把模型工具调用路由回 MCP。

职责：

```text
MCP tool schema
  -> 模型 tool/function schema
  -> 模型返回 tool call
  -> ToolRouter 定位 server/tool
  -> MCP tools/call
  -> 工具结果返回模型
```

需要解决的问题：

- 不同 server 的 tool 名称冲突。
- 工具 schema 转换。
- 工具调用权限判断。
- 工具结果截断和清洗。

推荐内部 tool 名称格式：

```text
mcp__{server_name}__{tool_name}
```

例如：

```text
mcp__git__status
mcp__filesystem__read_file
mcp__docs__search
```

### `resources.py`

封装 MCP resources 能力。

第一阶段建议支持：

- `resources/list`
- `resources/read`

后续可以支持：

- resource templates
- resource subscribe
- resource updated notification

在 CodeMate 中，resources 可以用于补充模型上下文，例如：

- 当前文件。
- README。
- Git diff。
- API 文档。
- 数据库 schema。

### `prompts.py`

封装 MCP prompts 能力。

第一阶段可以只做发现和读取：

- `prompts/list`
- `prompts/get`

后续可以把 prompts 映射为 CodeMate 的快捷操作，例如：

- 代码审查。
- 解释报错。
- 生成测试。
- 总结项目。

### `security.py`

负责安全策略。

这是 MCP 集成中最重要的模块之一。

建议最少包含：

- server 白名单。
- tool 白名单。
- 路径访问限制。
- 敏感文件过滤。
- 写操作确认。
- 命令执行确认。
- 外部 API 调用确认。
- 工具结果长度限制。
- 审计日志。

推荐权限分级：

```text
read_only       自动允许
write_project   需要项目授权
execute_command 需要用户确认
external_api    需要服务授权
dangerous       默认禁用
```

## Transport 设计

### stdio transport

适合本地工具。

```text
CodeMate
  -> subprocess
  -> stdin/stdout JSON-RPC
  -> MCP Server
```

适合接入：

- filesystem server。
- git server。
- sqlite server。
- 本地命令工具。

需要处理：

- 子进程启动失败。
- stdout JSON-RPC 解析。
- stderr 日志。
- 进程退出。
- 超时。
- 关闭清理。

### Streamable HTTP transport

适合远程服务。

```text
CodeMate
  -> HTTP POST /mcp
  -> HTTP GET /mcp 监听事件流
  -> Remote MCP Server
```

适合接入：

- GitHub。
- Jira。
- Notion。
- Linear。
- 企业知识库。
- 内部 API 网关。

需要处理：

- 鉴权。
- 网络错误。
- 重连。
- session id。
- token 过期。
- 请求超时。

## 与当前 AI 模型模块的关系

当前项目已有模型适配器：

```text
app/adapters/models/
  base.py
  mock.py
  openai_compatible.py
  anthropic.py
  factory.py
```

MCP 不应该直接塞进某个 provider adapter 里。

推荐边界：

```text
ModelAdapter
  只负责和模型 provider 通信

MCP 模块
  只负责和 MCP Server 通信

ChatService / AgentService
  负责编排模型与工具调用
```

后续如果要支持真正的 tool call，需要在 `ChatService` 之上或旁边增加一个更像 agent 编排层的服务：

```text
app/services/agent.py
```

它负责：

- 构造带 tools 的模型请求。
- 解析模型 tool call。
- 调 MCP 工具。
- 把工具结果追加回 messages。
- 继续调用模型直到完成。
- 通过 SSE 把过程推给前端。

## 推荐实施阶段

### 第一阶段：MCP Client MVP

目标：CodeMate 能连接一个或多个 MCP Server，并能手动调用工具。

范围：

- `stdio` transport。
- `initialize`。
- `tools/list`。
- `tools/call`。
- `resources/list`。
- `resources/read`。
- 基础配置。
- 基础错误处理。

暂不做：

- 复杂 OAuth。
- Sampling。
- Elicitation。
- 自动工具选择。
- 远程 server。

### 第二阶段：接入 ChatService / AgentService

目标：模型可以调用 MCP tools。

范围：

- MCP tools 转模型 tools。
- 模型 tool call 解析。
- 工具调用结果回填模型上下文。
- SSE 输出工具调用过程。
- 只读工具自动执行。
- 写操作需要用户确认。

### 第三阶段：支持远程 Streamable HTTP

目标：支持云端和企业 MCP Server。

范围：

- Streamable HTTP transport。
- header/token 配置。
- session 管理。
- 远程 server 错误处理。
- 重连策略。

### 第四阶段：CodeMate 暴露 MCP Server

目标：其他 AI 客户端可以调用 CodeMate 能力。

可暴露能力：

```text
tools:
  search_code
  read_project_file
  get_project_tree
  run_tests
  explain_symbol

resources:
  file://...
  git://current/diff

prompts:
  code_review
  explain_error
  generate_tests
```

## 第一阶段建议目录

MVP 可以先实现较小范围：

```text
app/mcp/
  __init__.py
  config.py
  protocol.py
  client.py
  manager.py
  security.py
  transports/
    __init__.py
    stdio.py
```

测试：

```text
tests/test_mcp_protocol.py
tests/test_mcp_stdio_transport.py
tests/test_mcp_client.py
tests/test_mcp_manager.py
```

## 风险与注意事项

### 1. 不要让模型直接决定所有工具调用

模型只能提出工具调用意图，最终是否执行应由 CodeMate 的策略层决定。

### 2. 不要把所有 MCP tools 原样暴露给模型

需要先经过：

```text
白名单
权限分级
项目授权
用户确认
```

### 3. 工具结果需要清洗和截断

MCP Server 返回的内容可能很长，也可能包含提示词注入内容。进入模型上下文前需要限制长度并标记来源。

### 4. 本地 stdio server 要限制工作目录

例如 filesystem server 不应该默认访问整个用户主目录。

### 5. 远程 MCP Server 要注意凭证泄露

不要把 API key、cookie、token 写入日志，也不要把它们传给模型。

## 总结

CodeMate 的 MCP 集成建议路线：

```text
第一步：做 MCP Host + Client Manager
第二步：先支持 stdio、本地工具和 tools/resources
第三步：接入 AgentService，让模型能安全调用工具
第四步：支持 Streamable HTTP 远程服务
第五步：把 CodeMate 自身能力暴露成 MCP Server
```

这条路线和当前项目已有的模型适配器设计兼容。模型 provider 继续由 `app/adapters/models/` 管，MCP 工具生态单独放在 `app/mcp/`，最终由 agent 编排层统一调度。
