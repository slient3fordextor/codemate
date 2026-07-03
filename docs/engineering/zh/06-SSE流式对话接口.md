# SSE 流式对话接口

## 目标

定义 CodeMate 对话接口的流式输出协议，支撑首 Token 延迟、用户取消、错误处理和任务状态追踪。

## 推荐接口

```text
POST /api/v1/chat/completions
```

请求示例：

```json
{
  "session_id": "ses_...",
  "message": "解释当前文件的作用",
  "current_file": "src/main.py",
  "selected_text": null,
  "stream": true
}
```

## SSE 事件类型

建议使用明确事件类型：

```text
message.start
message.delta
message.done
usage.update
task.update
error
```

事件示例：

```text
event: message.delta
data: {"content":"这里的函数用于..."}
```

## 错误事件

流式过程中的错误也应保持统一结构：

```text
event: error
data: {"code":"MODEL_PROVIDER_TIMEOUT","message":"Model provider request timed out","request_id":"req_..."}
```

## 取消机制

MVP 可以先支持客户端断开连接后的服务端取消。后续可增加：

```text
POST /api/v1/tasks/{task_id}/cancel
```

## 指标采集

对话服务应记录：

- 首 Token 延迟。
- 完整响应耗时。
- 输入 token。
- 输出 token。
- 模型 provider。
- 是否用户取消。
- 是否成功完成。

## 验收标准

- 客户端能逐步接收输出。
- 模型错误能以 SSE error 事件返回。
- 客户端断开后服务端停止继续生成。
- 可以使用 mock 模型适配器测试流式输出。

## 风险点

- 如果流式协议不稳定，前端、CLI、IDE 插件会各自适配，后续维护成本高。
- 如果不能取消长请求，用户体验和模型成本都会变差。
