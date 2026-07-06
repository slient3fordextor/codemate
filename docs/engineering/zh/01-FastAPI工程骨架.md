# FastAPI 工程骨架

## 目标

建立 CodeMate 后端的最小 FastAPI 工程结构，让项目从示例脚本转为可运行的 API 服务。

本步骤关注“脚手架”，不是实现完整业务。它要先回答两个问题：

1. 后续代码应该放在哪里。
2. 每一层架构负责什么、不负责什么。

更完整的分层说明见 [00-脚手架架构说明.md](00-脚手架架构说明.md)。

## 建议目录

```text
app/
  __init__.py
  main.py
  api/
    __init__.py
    v1/
      __init__.py
      router.py
      health.py
      chat.py
      projects.py
      models.py
      tasks.py
  core/
    __init__.py
    config.py
    logging.py
    errors.py
    middleware.py
  schemas/
    __init__.py
  services/
    __init__.py
  adapters/
    __init__.py
  storage/
    __init__.py
tests/
  __init__.py
```

## 目录用途

| 目录或文件 | 用途 | 不应该承担的职责 |
| --- | --- | --- |
| `app/main.py` | 创建应用、注册路由、中间件、异常处理和生命周期 | 不写业务逻辑，不直接调用模型或数据库 |
| `app/api` | HTTP 接口层，负责请求和响应 | 不拼 prompt，不读文件，不直接调用外部模型 SDK |
| `app/api/v1/router.py` | 汇总 v1 路由 | 不写具体接口逻辑 |
| `app/schemas` | API 请求和响应结构 | 不放数据库连接和业务编排 |
| `app/services` | 业务编排层 | 不直接暴露 HTTP 细节 |
| `app/adapters` | 外部系统适配层，例如模型 API、本地文件系统 | 不承载产品流程编排 |
| `app/storage` | 持久化边界，例如会话、消息、任务和反馈 | 不处理 HTTP 请求 |
| `app/core` | 配置、日志、错误、中间件等基础设施 | 不放具体业务模块 |
| `tests` | 验证脚手架和业务契约 | 不依赖真实第三方模型 API |

## 应用入口职责

`app/main.py` 只负责：

- 创建 `FastAPI` 实例。
- 注册 API 路由。
- 注册中间件。
- 注册异常处理器。
- 注册启动和关闭生命周期。

业务逻辑不应写入 `app/main.py`。

这样设计是为了让应用组装和业务实现解耦。后续无论新增对话服务、文件索引还是任务状态，都不需要反复修改入口文件。

## 路由分层

API 使用版本化前缀：

```text
/api/v1/health
/api/v1/chat
/api/v1/projects
/api/v1/models
/api/v1/tasks
```

`app/api/v1/router.py` 负责汇总各模块路由。

这样设计是为了给 Web 工作台、CLI 和 IDE 插件提供稳定 API 契约。未来如果接口发生破坏性变化，可以新增 `/api/v2`，而不是直接破坏已有客户端。

## 当前脚手架最小实现

当前阶段只需要实现：

- `GET /api/v1/health`：验证服务可用。
- `GET /api/v1/version`：返回应用版本和 API 版本。
- `x-request-id`：为每个请求提供追踪 ID。
- 统一错误响应结构：为后续业务错误打基础。

暂时不需要实现：

- 真实模型调用。
- 项目文件读取。
- 数据库存储。
- SSE 对话流。

这些能力应在脚手架稳定后按后续文档逐步加入。

## 骨架验收标准

- 可以通过 `uvicorn app.main:app --reload` 启动。
- 访问 `/api/v1/health` 返回成功。
- OpenAPI 文档可以通过 `/docs` 访问。
- API 层没有直接读取文件、调用模型或访问数据库的业务逻辑。

## 风险点

- 如果早期不建立清晰目录，后续模型调用、文件索引和任务状态容易混在 API 层。
- 如果不做 API 版本化，Web、CLI、IDE 插件接入后会增加兼容成本。
