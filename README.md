# CodeMate

[English](README.en-US.md) | [简体中文](README.en-US.md)

CodeMate 是一个本地优先的 AI 编码助手，提供 Agent CLI 和基于 FastAPI 的 Web 对话后端。

## 当前能力

- 使用 `readonly`、`confirm`、`agent` 三种真实生效的权限策略分析或修改项目。
- 通过受限工具读取文件、搜索文本以及查看 Git 状态和差异。
- 可写任务在独立的 Git worktree 中运行，不会直接修改当前工作区。
- 文件修改先生成统一 diff，再根据权限策略审批和原子写入。
- 测试、格式化和类型检查命令在无网络的 Bubblewrap 沙箱中执行。
- 执行图、Loop 状态和任务状态通过 SQLite 持久化，可使用同一任务 ID 恢复。
- 任务变更只有在用户显式执行 `deliver` 后才会应用到原工作区。
- Web/API 默认只允许本机访问；远程访问需要显式开启并配置 Bearer Token。

## 环境要求

- Python 3.12 或更高版本
- Git
- Linux Bubblewrap：仅 `confirm`、`agent` 可写模式和验证命令需要

确认 Bubblewrap 是否可用：

```bash
bwrap --version
```

如果 Git 或 Bubblewrap 不可用，可写模式会拒绝执行，不会退回到不受保护的宿主机写入或命令执行。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -c requirements.lock -e ".[dev]"
```

也可以使用统一安装命令：

```bash
make sync
```

### 一键启动

在项目根目录执行：

```bash
make start
```

或直接执行：

```bash
./scripts/start.sh
```

首次启动会自动创建 `.venv`、安装 `requirements.lock` 中的依赖，并从
`.env.example` 创建 `.env`。之后启动会复用已有环境；当
`requirements.lock` 发生变化时会自动重新同步依赖。

默认访问地址是 `http://127.0.0.1:8000/`。可以通过环境变量覆盖：

```bash
HOST=0.0.0.0 PORT=8080 WORKERS=2 make start
```

开发模式可启用自动重载：

```bash
CODEMATE_RELOAD=true make start
```

远程绑定时仍需在 `.env` 中显式配置 `ALLOW_REMOTE_API=true` 和强 Bearer
Token；没有配置时 API 会继续拒绝远程请求。

### Linux 桌面客户端

Linux 桌面客户端使用 GTK 和 WebKitGTK 承载同一套本地 Web 工作区。首次使用前安装系统运行库：

```bash
sudo apt install python3-gi python3-venv gir1.2-gtk-3.0 gir1.2-webkit2-4.0
```

启动客户端窗口：

```bash
make desktop
```

客户端会自动准备 Python 运行环境、分配本地端口、启动 FastAPI，并在关闭窗口时回收后端进程。也可以把它安装到应用菜单：

```bash
./scripts/install-desktop.sh
```

安装后可以从系统应用菜单启动 CodeMate。客户端默认只绑定 `127.0.0.1`，不会把本地控制接口暴露到局域网。

创建本地配置文件：

```bash
cp .env.example .env
```

安装完成后可检查 CLI：

```bash
codemate --version
codemate --help
```

## Agent CLI

### 权限模式

| 模式 | 自动允许 | 需要确认 | 禁止 |
| --- | --- | --- | --- |
| `readonly` | 安全读取、搜索、Git 查询 | 无 | 文件写入、命令执行、联网 |
| `confirm` | 安全读取、搜索、diff 提案 | 补丁应用、白名单验证命令 | 联网、任意命令、危险操作 |
| `agent` | worktree 内写入、白名单验证命令 | 中断结果未知的命令重跑、最终交付 | 联网、任意命令、危险操作 |

默认模式是最小权限的 `readonly`。只有显式选择 `confirm` 或 `agent` 时，CodeMate 才会创建可写任务 worktree。

### 交互式会话

只读会话：

```bash
codemate --workspace .
```

逐次确认写入和命令：

```bash
codemate --mode confirm --workspace .
```

退出会话可输入 `/exit` 或 `/quit`。

### 单次只读任务

```bash
codemate run "检查当前改动并列出风险" \
  --mode readonly \
  --workspace .
```

输出机器可读 JSON：

```bash
codemate run "分析 app 目录结构" \
  --mode readonly \
  --workspace . \
  --json
```

### 受控编码任务

```bash
codemate run "修复解析器失败问题并运行相关测试" \
  --mode agent \
  --workspace . \
  --task parser-fix \
  --deliver
```

`--deliver` 表示本次非交互运行完成并验证成功后，允许把确认过的 diff 写入主工作区。没有该参数时仍只保留任务供审查；交互模式则会展示最终 diff 并单独询问是否交付。

任务在独立 worktree 中执行。完成后输出会包含：

- `task_id`：任务标识；
- `task_state`：持久化任务状态；
- `worktree`：隔离工作树路径；
- `changed_paths`：变更文件清单；
- `diff`：相对于任务基线的完整差异；
- 模型轮数、工具调用数和 Token 用量。

模型声称完成并不等于任务可交付。只要任务产生过编辑，最新编辑之后必须存在一次成功的沙箱验证，否则任务返回 `needs_evidence`。完成时还会保存已验证补丁的 SHA-256；交付前重新计算，如果 worktree 在验证后发生变化则拒绝写入主工作区。

### 恢复任务

再次传入相同的 `--task` 会恢复已有 worktree，并从已持久化状态开始新一轮：

```bash
codemate run "继续修复并完成验证" \
  --mode agent \
  --workspace . \
  --task parser-fix
```

如果上次进程在命令运行期间中断，CodeMate 无法确定该命令是否已经产生副作用，因此默认阻止自动重跑。确认结果后可显式授权：

```bash
codemate run "继续任务" \
  --mode agent \
  --workspace . \
  --task parser-fix \
  --resume
```

### 审查与交付

确认任务 diff 后，将变更应用到原工作区：

```bash
codemate deliver parser-fix --workspace .
```

交付前会检查原工作区自任务基线以来是否修改过相同文件，并执行 `git apply --check`。存在重叠变更时任务会停止，不会自动覆盖或解决冲突。

交付后需要回退时执行：

```bash
codemate rollback parser-fix --workspace .
```

回退前会检查已验证补丁仍然一致，并执行反向补丁预检。如果主工作区中的相关内容在交付后又被修改，回退会停止，不覆盖后续修改。交付或回退在应用过程中失败时，会尝试恢复操作前状态。

丢弃没有变更的任务：

```bash
codemate discard parser-fix --workspace .
```

显式丢弃含有未审查变更的任务：

```bash
codemate discard parser-fix --workspace . --force
```

### Agent 运行预算

- 默认自动执行最多 10 轮；
- 第 11 至 15 轮必须产生新工具证据；
- 超过 15 轮后，每一轮都需要交互式确认；
- 非交互任务不会绕过审批，而是返回等待审批状态；
- 模型上下文、总 Token、文件大小、工具输出、命令时长和内存均有独立上限。

## 模型配置

默认使用 `mock` provider，便于本地验证应用和 CLI 链路。使用真实模型时编辑 `.env`：

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.example.com/v1
MODEL_NAME=your-model
MODEL_API_KEY=your-api-key
MODEL_TIMEOUT_SECONDS=60
MODEL_MAX_RETRIES=2
MODEL_CONTEXT_WINDOW=32768
```

项目支持 OpenAI-compatible、Anthropic/Claude、Ollama 以及配置为 OpenAI-compatible 的 DeepSeek、Qwen、智谱、Moonshot、百川等 provider。完整示例见 [.env.example](.env.example)。

模型流式请求只会在首个输出块之前重试超时、传输错误、HTTP 429 和 5xx。一旦输出开始，后续失败会直接上报，避免重复内容。

## Web 对话与 API

启动开发服务：

```bash
make dev
```

默认访问地址：

```text
http://127.0.0.1:8000/
```

指定监听地址和端口：

```bash
make dev HOST=0.0.0.0 PORT=8080
```

API 默认只接受 loopback 客户端和可信本地 Host。远程访问必须在 `.env` 中配置：

```dotenv
ALLOW_REMOTE_API=true
REMOTE_API_TOKEN=replace-with-a-strong-random-token
```

远程客户端需要发送请求头：

```text
Authorization: Bearer <REMOTE_API_TOKEN>
```

启用 API 限流：

```dotenv
ENABLE_RATE_LIMIT=true
RATE_LIMIT_PER_MINUTE=60
```

多个本机服务进程可以配置同一个 SQLite 文件共享计数：

```dotenv
RATE_LIMIT_DATABASE_PATH=/var/lib/codemate/rate-limit.sqlite3
```

跨机器部署仍应由反向代理或 API 网关统一完成认证和限流。

## 健康检查

基础健康检查：

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/api/v1/health
```

正常响应为 HTTP `200`，JSON 中的 `status` 为 `ok`。记忆存储等关键依赖不可用时返回 HTTP `503`。

显式深度探活会发起一次最小模型请求，可能消耗模型额度：

```bash
curl -i 'http://127.0.0.1:8000/api/v1/health?probe_model=true'
```

## 开发与质量检查

常用命令：

```bash
make lint       # Ruff 静态检查
make typecheck  # mypy 严格类型检查
make test       # pytest 测试
make check      # 依次执行以上全部门禁
make format     # Ruff 代码格式化
```

当仓库存在 `.venv/bin/python` 时，Makefile 会自动使用项目虚拟环境。

当前完整门禁包含 102 项自动化测试，覆盖 API、记忆、模型适配器、Agent Runner、执行图、SQLite 恢复、敏感边界、Bubblewrap 和 worktree 交付。

## 目录说明

```text
app/api/                 FastAPI 路由
app/adapters/models/     模型 provider 适配器
app/agent/               Agent Runner、执行图和任务持久化
app/agent/tools/         只读、编辑和受控工具
app/agent/sandbox/       沙箱策略与 Bubblewrap Executor
app/agent/worktree/      Git worktree 生命周期与交付
app/services/            对话、记忆、Token 和语言上下文服务
app/static/              Web 对话页面
docs/                    产品、工程和流程文档
tests/                   自动化测试
```

## 当前边界

- Web 页面目前仍是对话入口，尚未开放 CLI 的可写 Agent 模式。
- 可写命令沙箱当前仅支持安装了 Bubblewrap 的 Linux 环境。
- 当前 Runner 按单工具串行执行，完整并行 Join 和多层规划仍属于后续能力。
- 不支持自动 Git commit、push、历史重写或无人值守交付。
- 不会自动复制 `.env`、密钥、用户主目录、虚拟环境或其他敏感未跟踪文件到任务 worktree。

更详细的 Agent 风险状态和修复证据见：

- [CodeMate 全流程与 Agent 工程方法论](docs/engineering/zh/15-CodeMate全流程与Agent工程方法论.md)
- [当前设计风险与整改计划](docs/cli/06-当前设计风险与整改计划.md)
- [Agent CLI 问题复盘与工程方法论](docs/cli/07-AgentCLI问题复盘与工程方法论.md)

### 客户端皮肤

左侧「个性皮肤」可一键切换「梦幻公主粉」和「星空玻璃」，无需刷新或重启。
两套皮肤共用布局，覆盖对话、输入框、代码块和模型设置弹窗。
浏览器通过本地存储记住选择；Linux 客户端保存到
`$XDG_CONFIG_HOME/codemate/theme.json`（默认 `~/.config/codemate/theme.json`），
不受每次启动随机端口的影响。

皮肤交互回归检查：`node --test tests/frontend/theme.test.cjs`。
