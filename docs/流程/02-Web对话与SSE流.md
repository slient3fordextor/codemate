# Web 对话与 SSE 流

## 页面初始化

`GET /` 返回的页面加载 `/static/app.js`。脚本从 `localStorage` 恢复：

- `codemate.sessionId`：当前会话 ID；
- `codemate.operationMode`：操作模式，默认 `confirm`。

页面预置模型列表和操作模式列表。选择预置模型只改页面表单值，不会自动写入服务端；点击“保存模型”才调用模型配置 API。

## 发送消息流程

```text
用户提交表单或按 Ctrl/Cmd + Enter
  -> 忽略空消息或生成中的重复提交
  -> 立即渲染用户气泡
  -> 新建 AbortController，禁用发送与新会话按钮
  -> 收集 session_id、message、current_file、selected_text、operation_mode
  -> 可选加入 model、temperature、max_tokens
  -> POST /api/v1/chat/completions，Accept: text/event-stream
  -> 逐块读取 response.body
  -> 解析以空行分隔的 SSE 事件
  -> 按事件更新页面
```

请求固定传递 `stream: true`。尽管前端创建了 `AbortController`，当前页面没有取消按钮，也没有调用 `abort()` 的交互入口。

## SSE 事件处理

| 事件 | 前端行为 |
| --- | --- |
| `message.start` | 若携带 `session_id`，写入内存变量和 `localStorage`。 |
| `message.delta` | 将 `content` 追加到助手气泡，并滚动到底部。 |
| `message.done` | 当前无额外页面处理。 |
| `usage.update` | 当前不显示 token 用量。 |
| `error` | 用服务端 `message` 覆盖助手气泡，状态显示“出错”。 |

读取结束后，页面会识别 Markdown 围栏代码块。代码块可复制；看起来像 Shell 命令的块额外提供“解释”按钮，它只会把解释请求预填到输入框，不会在浏览器或服务器执行命令。

## 新会话流程

点击新会话按钮会清空本地 `sessionId`、删除 `localStorage` 中的键、清空当前消息列表并显示新的欢迎语。服务端内存仓库中旧会话不立即删除，将按 LRU 容量规则自然淘汰。

## 失败分支

- HTTP 非成功或没有响应体：页面显示 `请求失败：HTTP <状态码>`。
- 网络异常、流读取异常：页面显示异常消息或通用失败文案。
- SSE 格式不完整或 JSON 不合法：该事件被忽略，流继续读取。
- 请求结束：无论成功失败，恢复按钮可用状态；成功时状态显示“就绪”。
