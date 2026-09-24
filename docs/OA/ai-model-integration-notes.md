# AI 模型集成、消息协议与流式输出说明

## 背景

当前 CodeMate 后端已经具备 MVP 级别的 AI 对话集成能力。整体链路是：

```text
前端/客户端
  -> POST /api/v1/chat/completions
  -> ChatCompletionRequest 消息协议
  -> ChatService 组装内部 ModelRequest
  -> ModelAdapter 调用具体模型
  -> ModelChunk 分块返回
  -> SSE 事件流返回给客户端
```

这套设计的核心目标是：API 层不直接依赖某一个模型厂商，而是通过统一的模型适配器协议对接不同 provider。

## 当前已完成内容

### 1. 统一聊天请求协议

相关文件：

- `app/schemas/chat.py`

当前请求模型为 `ChatCompletionRequest`，支持字段包括：

- `session_id`：会话 ID，后续用于多轮对话和历史管理。
- `message`：简单单轮用户输入。
- `messages`：完整消息列表，支持多轮上下文。
- `current_file`：当前文件路径。
- `selected_text`：用户选中的代码或文本。
- `stream`：是否流式输出，当前默认值为 `true`。
- `model`：指定模型名称。
- `temperature`：采样温度。
- `max_tokens`：最大输出 token 数。
- `metadata`：扩展元数据。

`ChatMessage` 当前支持以下角色：

```text
system
user
assistant
tool
```

请求校验规则：`message` 和 `messages` 至少需要提供一个，否则 FastAPI/Pydantic 会返回 `422`。

### 2. IDE 上下文注入

相关文件：

- `app/services/chat.py`

当请求中包含 `current_file` 或 `selected_text` 时，`ChatService._build_messages()` 会将它们组装成一条 `system` 消息，追加到消息列表中。

示例输入：

```json
{
  "message": "解释这段代码",
  "current_file": "app/main.py",
  "selected_text": "create_app()"
}
```

内部会接近转换为：

```text
system: Current file: app/main.py

Selected text:
create_app()

user: 解释这段代码
```

这样模型就能结合当前文件和选中文本回答问题。

### 3. 模型适配器抽象

相关文件：

- `app/adapters/models/base.py`
- `app/adapters/models/factory.py`
- `app/adapters/models/mock.py`
- `app/adapters/models/openai_compatible.py`

项目内部定义了统一的模型请求和响应分块：

```text
ModelRequest
  messages
  model
  stream
  temperature
  max_tokens
  metadata

ModelChunk
  type
  content
  input_tokens
  output_tokens
  finish_reason
  raw
```

模型适配器统一暴露：

```python
stream_chat(request: ModelRequest) -> AsyncIterator[ModelChunk]
```

当前支持 provider：

- `mock`：本地开发和测试使用。
- `openai_compatible`：调用 OpenAI 兼容格式的 `/chat/completions` 接口。
- `ollama`：当前复用 OpenAI-compatible 适配器。

### 4. SSE 流式输出

相关文件：

- `app/api/v1/chat.py`
- `app/services/chat.py`

接口：

```text
POST /api/v1/chat/completions
```

返回类型：

```text
Content-Type: text/event-stream
```

当前流式事件类型：

```text
message.start
message.delta
message.done
usage.update
task.update
error
```

示例：

```text
event: message.delta
data: {"content":"这里的函数用于..."}
```

模型生成过程中的错误会被包装为统一 SSE error 事件：

```text
event: error
data: {"code":"MODEL_PROVIDER_TIMEOUT","message":"Model provider request timed out","request_id":"req_..."}
```

### 5. 基础测试覆盖

相关文件：

- `tests/test_chat.py`

当前测试覆盖了：

- mock 模型可以返回 SSE 流。
- 响应头为 `text/event-stream`。
- 输出中包含 `message.start`、`message.delta`、`message.done`。
- 带 `messages`、`current_file`、`selected_text` 的请求可以被接受。
- 缺少 `message` 和 `messages` 时返回 `422`。

## 当前存在的问题与待完善点

### 1. `stream` 参数尚未真正生效

请求协议中已经有：

```python
stream: bool = True
```

但当前接口始终返回 `StreamingResponse`，`ChatService` 也始终构造：

```python
stream=True
```

也就是说，客户端即使传：

```json
{"message": "hello", "stream": false}
```

服务端当前仍然会按流式输出处理。

建议后续补充：

- `stream=true`：返回 SSE。
- `stream=false`：返回普通 JSON，如 `ChatCompletionResponse`。

### 2. `model_max_retries` 已实现，仍需扩大兼容测试

配置文件中已有：

```python
model_max_retries: int = 2
```

`OpenAICompatibleModelAdapter` 和 `AnthropicClaudeModelAdapter` 已在“尚未输出任何内容”时，针对网络错误、429 和 5xx 按该配置进行有限重试。

后续应继续覆盖以下场景：

- 网络临时失败。
- provider 返回 429。
- provider 返回 5xx。
- 连接中断但请求尚未开始产生 token。

注意：如果已经开始向客户端输出 token，再重试可能导致重复内容，需要谨慎处理。

### 3. 客户端断开后的取消机制还不完整

文档中已经提到需要支持客户端断开后的服务端取消，但当前代码没有显式检查：

```python
await request.is_disconnected()
```

当前主要依赖 ASGI/FastAPI 在连接断开时停止消费生成器。后续建议在流式循环中主动检测连接状态，避免模型仍在后台继续生成，造成资源浪费和模型费用浪费。

### 4. `task.update` 事件类型仍是预留

当前协议定义了：

```text
task.update
```

但实际业务中尚未产生任务状态事件。

后续如果支持代码修改、文件写入、测试执行等长任务，可以用该事件推送：

- 当前任务阶段。
- 正在处理的文件。
- 工具调用状态。
- 任务完成进度。

### 5. usage 统计依赖 provider 返回

当前 `usage.update` 主要从模型 provider 的原始响应中读取：

```text
prompt_tokens
completion_tokens
```

如果 provider 不返回 usage，服务端暂时没有自己的 token 估算逻辑。

建议后续：

- 对 OpenAI-compatible provider 保留原始 usage。
- 对不返回 usage 的 provider 做兼容处理。
- 必要时引入 tokenizer 估算输入和输出 token。

### 6. OpenAI-compatible SSE 解析较基础

当前 `_parse_sse_line()` 只处理以 `data: ` 开头的单行 SSE。

后续需要关注：

- 多行 `data:` 的情况。
- provider 返回非标准字段的兼容性。
- 空 delta。
- tool call delta。
- function call delta。
- usage 在最后一个 chunk 中返回的情况。

### 7. `ollama` provider 目前只是复用 OpenAI-compatible

`factory.py` 中：

```python
if config.provider == "ollama":
    return OpenAICompatibleModelAdapter(config)
```

这适用于 Ollama 的 OpenAI-compatible 接口，但如果后续要调用 Ollama 原生 API，应该单独实现一个 `OllamaModelAdapter`。

### 8. 错误码还可以继续细分

当前主要错误包括：

- `MODEL_PROVIDER_TIMEOUT`
- `MODEL_PROVIDER_ERROR`

后续可以增加更细粒度的错误码：

- `MODEL_PROVIDER_AUTH_ERROR`
- `MODEL_PROVIDER_RATE_LIMITED`
- `MODEL_PROVIDER_BAD_REQUEST`
- `MODEL_PROVIDER_UNAVAILABLE`
- `MODEL_STREAM_PARSE_ERROR`

这样前端可以更准确地展示错误和重试建议。

## 建议优先级

### P0

- 明确 `stream=false` 是否需要在 MVP 支持。
- 主动处理客户端断开连接，避免后台继续消耗模型资源。
- 补充真实 OpenAI-compatible provider 的集成测试或手动验证步骤。

### P1

- 实现 `model_max_retries`。
- 细化 provider 错误码。
- 增强 SSE 解析兼容性。
- 明确 usage 缺失时的处理策略。

### P2

- 实现 `task.update`。
- 为 Ollama 原生接口增加独立适配器。
- 支持 tool call / function call 流式协议。

## 总结

当前 AI 集成已经具备基本可用的工程骨架：

- 请求协议已经定义。
- 流式输出已经跑通。
- 模型 provider 被适配器隔离。
- mock 测试可以稳定验证 SSE 行为。

后续重点不是推翻现有设计，而是在现有抽象上补齐非流式返回、取消机制、重试机制、usage 统计和更复杂的模型流式事件。
