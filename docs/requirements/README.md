# CodeMate Requirements / CodeMate 需求文档

Version: v1.2  
Last updated: 2026-07-03  
Status: Draft for review

CodeMate is an open-source local Agent for coding assistance. It does not provide a public demo, does not host user project code, and does not charge for software usage. Users run it locally and choose either a local model or a third-party model API.

CodeMate 是一个开源本地编程 Agent。项目不提供公共 Demo，不托管用户项目代码，也不对软件本身收费。用户在本地运行，并自行选择本地模型或第三方大模型 API。

## Document Index

| Language | Entry |
| --- | --- |
| Chinese | [zh/README.md](zh/README.md) |
| English | [en/README.md](en/README.md) |

## Current Decisions

- MVP runs locally by default.
- No official public demo.
- No hosting of user project code.
- No pricing or paid plans in MVP.
- Model options: local model or user-provided third-party API key.
- Backend recommendation: FastAPI.

## 当前决策

- MVP 默认本地运行。
- 不提供官方公共 Demo。
- 不托管用户项目代码。
- MVP 不设计定价或付费套餐。
- 模型方式支持本地模型或用户自带第三方 API Key。
- 后端推荐 FastAPI。

## Recommended Next Step / 建议下一步

Confirm the backend selection, then generate the FastAPI skeleton described in [en/02-technical-selection.md](en/02-technical-selection.md).

确认后端选型后，生成 [zh/02-技术选型.md](zh/02-技术选型.md) 中定义的 FastAPI 骨架。
