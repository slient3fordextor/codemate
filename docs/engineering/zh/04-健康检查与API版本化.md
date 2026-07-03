# 健康检查与 API 版本化

## 目标

提供基础可观测接口，并从第一版开始固定 API 版本前缀。

## API 前缀

所有业务 API 使用：

```text
/api/v1
```

根路径可以返回简要应用信息，但不承载业务能力。

## 健康检查接口

建议接口：

```text
GET /api/v1/health
```

响应示例：

```json
{
  "status": "ok",
  "app": "CodeMate",
  "version": "0.1.0",
  "checks": {
    "config": "ok",
    "storage": "disabled",
    "model": "not_configured"
  }
}
```

## 版本接口

建议接口：

```text
GET /api/v1/version
```

响应示例：

```json
{
  "name": "CodeMate",
  "version": "0.1.0",
  "api_version": "v1"
}
```

## 健康检查边界

健康检查不应：

- 发起真实模型请求。
- 泄露 API Key。
- 返回本地完整路径。
- 执行昂贵扫描。

## 验收标准

- 服务启动后健康检查立即可用。
- 健康检查能反映基础配置和存储状态。
- OpenAPI 中所有业务接口都位于 `/api/v1` 下。

## 风险点

- 没有健康检查会增加部署、调试和前端联调成本。
- 没有版本化会让后续 Web、CLI、IDE 插件升级困难。
