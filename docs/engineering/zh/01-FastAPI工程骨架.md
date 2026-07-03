# FastAPI 工程骨架

## 目标

建立 CodeMate 后端的最小 FastAPI 工程结构，让项目从示例脚本转为可运行的 API 服务。

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

## 应用入口职责

`app/main.py` 只负责：

- 创建 `FastAPI` 实例。
- 注册 API 路由。
- 注册中间件。
- 注册异常处理器。
- 注册启动和关闭生命周期。

业务逻辑不应写入 `app/main.py`。

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

## 骨架验收标准

- 可以通过 `uvicorn app.main:app --reload` 启动。
- 访问 `/api/v1/health` 返回成功。
- OpenAPI 文档可以通过 `/docs` 访问。
- API 层没有直接读取文件、调用模型或访问数据库的业务逻辑。

## 风险点

- 如果早期不建立清晰目录，后续模型调用、文件索引和任务状态容易混在 API 层。
- 如果不做 API 版本化，Web、CLI、IDE 插件接入后会增加兼容成本。
