# Git 工作树隔离策略

> 状态：P0 已确定的产品方向。`WorktreeManager`、`GitAdapter` 与仅负责路径校验的 `SandboxPolicy` 已实现为未接入执行图的基础库；实际 Sandbox Executor、任务持久化、补丁应用和完整清理流程仍未实现。`SandboxPolicy.require_process_isolation()` 在执行器缺失时固定 fail-closed，不能用路径校验结果代替进程隔离。

## 1. 决策

CodeMate 对会修改文件或运行可能产生工作区副作用的任务，优先使用 **Git worktree 隔离**。用户当前打开的本地工作区保持前台基线；每个可写 Agent 任务在独立的 worktree 中读取、修改和验证，最终以 diff 和明确的用户动作交付。

这不是安全沙箱的替代品：命令执行仍需遵守独立的沙箱、网络、环境变量和资源限制策略。Worktree 解决的是开发者体验、并发修改冲突和可审查交付。

该方向参考 Codex App 的 worktree 机制：独立聊天可以在同一 Git 项目中使用独立工作树，避免干扰本地前台工作；任务完成后通过 handoff 或审查变更回到本地。[Codex Worktrees](https://developers.openai.com/codex/app/worktrees)

当前基础库位于 `app/agent/worktree/` 与 `app/agent/sandbox/`：前者只使用固定 Git 子命令创建 detached task worktree、可选应用 tracked diff 快照并生成包含 tracked patch 与 untracked 清单的任务变更集；后者只校验路径位于 task worktree 内且拒绝 `.git` 元数据，不执行命令。

## 2. 目标体验

```text
用户在当前项目发起可写任务
  -> CodeMate 记录基线提交和当前工作区状态
  -> 创建 task 专属 worktree
  -> Agent 在 worktree 中读取、修改、测试
  -> 用户查看任务 diff、验证结果和风险
  -> 用户选择：应用到本地 / 保留 worktree / 丢弃任务
```

用户的原工作目录不应被后台任务意外修改，也不应因为 Agent 正在运行而无法继续使用 IDE、终端或 Git。

## 3. 任务类型与隔离级别

| 任务类型 | 默认执行位置 | 原因 |
| --- | --- | --- |
| 只读解释、搜索、Git 状态查询 | 当前工作区，只读策略 | 不产生修改，避免创建成本 |
| 单文件或多文件修改 | task worktree | 保留原工作区，生成独立 diff |
| 测试、构建、格式化 | task worktree | 命令可能写缓存、生成文件或修改 lockfile |
| 多个独立可写任务 | 每任务一个 worktree | 从物理目录层面消除跨任务写入冲突 |
| Git 提交、推送、重写历史 | P0 不支持 | 先由用户在审查后决定版本控制动作 |

任务在同一 worktree 内仍默认串行执行写入与工作区命令；worktree 降低跨任务冲突，不代表其中的任意命令都安全并行。

## 4. 基线与未提交修改

### 4.1 默认基线

默认从任务启动时的 `HEAD` 创建 detached worktree，并记录：

```text
task_id
repository_root
base_commit
base_branch
created_at
```

使用 detached HEAD 避免占用或污染用户正在使用的分支。任务 worktree 的路径由 CodeMate 生成并由 Worktree Manager 管理，不能由模型决定。

### 4.2 本地未提交修改

不自动将用户当前未提交修改复制到 task worktree。自动复制会扩大冲突面，并可能将无关或敏感变更带入模型和命令上下文。

当任务确实依赖当前未提交修改时，CLI 必须显式展示摘要并让用户选择：

| 选择 | 行为 |
| --- | --- |
| 从提交基线开始（默认） | task worktree 只包含 `HEAD` 内容 |
| 包含当前 tracked diff | 将当前已跟踪文件的 diff 作为可审计补丁快照应用到 task worktree |
| 取消 | 不创建 worktree，不开始可写任务 |

未跟踪文件、忽略文件、`.env`、密钥和其他敏感路径不因“包含当前 diff”而自动复制。需要这些文件时应使用项目安装脚本、受控配置或单独的用户确认。

## 5. 交付与合并

任务完成时，CodeMate 展示相对于 `base_commit` 的：

- 文件变更摘要和完整 diff；
- 独立的 untracked 文件清单；空文件即使没有 patch 内容也必须出现在清单中；
- 已执行命令、退出码、测试/构建结果；
- 未执行或失败的验证；
- 与用户当前工作区变更可能冲突的文件。

P0 不由 Agent 自动合并到用户原工作区。用户选择的交付动作：

| 动作 | 行为 |
| --- | --- |
| 查看 diff | 只读审查，不修改任何工作区 |
| 应用变更 | 在用户确认后，将经验证的 task diff 应用到原工作区；冲突则停止并展示冲突，不自动解决 |
| 保留 worktree | 保留任务目录和状态，用户可继续检查或手动接管 |
| 丢弃 | 删除 task worktree；仅在确认没有待交付变更后执行 |

“应用变更”是一个新的高风险图节点，必须重新经过权限、基线一致性和冲突检查，不能因为任务中的文件写入已获批准就自动放行。

## 6. 并行、锁与取消

多个 task worktree 可并行运行独立读取、模型调用、测试和修改，默认不共享 `write:{path}` 锁，因为它们不在同一物理目录。仍需保留以下共享约束：

- 模型 provider 和网络 host 的并发配额；
- 本机 CPU、内存、磁盘和进程数的命令资源限制；
- Git 公共元数据操作与未来远程 push 的全局锁；
- 原工作区的“应用变更”操作必须全局串行。

取消任务时，Worktree Manager 停止该任务的 Executor，保留工作树和 Artifact 供用户审查。未明确丢弃前，不自动删除含有未交付 diff 的 worktree。

## 7. 与沙箱的组合设计

### 7.1 分层职责

Git worktree 和沙箱不冲突，它们分别回答“改哪里”和“能做什么”：

```text
原工作区
  -> Worktree Manager（受控宿主侧）创建 task worktree
  -> Sandbox Executor 将 task worktree 挂载为 /workspace
  -> Agent 工具与命令仅在 /workspace 中运行
  -> Worktree Manager（受控宿主侧）生成 diff 与交付变更
  -> 用户确认后才应用到原工作区
```

| 层 | 职责 | 不应承担的职责 |
| --- | --- | --- |
| Worktree Manager | 创建/移除 worktree、记录基线、生成 diff、检查交付冲突 | 执行模型生成的任意命令 |
| GitAdapter | 执行严格白名单的 Git 查询，读取 Git 状态和 diff | 允许模型自行传入 Git argv 或修改历史 |
| Sandbox Executor | 限制文件系统、网络、环境变量、进程和资源，并运行批准的工具/命令 | 创建 worktree、合并变更、访问用户原工作区 |
| Agent | 提出结构化工具意图和分析结果 | 决定挂载路径、沙箱参数、Git 元数据可见性或权限提升 |

Worktree 不是安全边界；Sandbox 也不会自动解决多任务写入冲突。两层必须同时存在。

### 7.2 P0 文件系统视图

P0 的命令沙箱从 task worktree 启动，并只将其作为可写项目目录暴露：

```text
Sandbox root
  /workspace       task worktree（读写）
  /tmp             受大小限制的临时目录（读写）
  /usr, /lib ...   命令运行必需文件（只读）
  /home            不挂载
  原工作区          不挂载
  凭据/socket       不挂载
  网络              默认关闭
```

文件工具同样只接受解析后位于 `/workspace` 下的路径。无论任务是否使用 worktree，都不允许通过符号链接、环境变量、挂载点或相对路径逃出该目录。

### 7.3 Git 元数据边界

Git worktree 的 `.git` 通常是一个指向原仓库公共 Git 元数据的文件。若将 task worktree 放进严格沙箱，沙箱中的 `git status` 和某些依赖脚本可能会跟随该指针访问工作区外的 `.git` 目录。

P0 不将完整公共 `.git` 暴露给任意沙箱命令。采用以下规则：

- `WorktreeManager` 在受控宿主侧执行 `git worktree add/remove` 和交付操作；
- `GitAdapter` 在受控宿主侧执行固定白名单的只读查询，例如 `status`、`diff`、`rev-parse`；
- Agent Sandbox 默认不提供通用 Git 写操作，也不信任模型传入的任意 Git 参数；
- 沙箱命令因 Git 元数据不可见而失败时，记录为环境限制，不以放宽 `.git` 挂载作为自动恢复动作；
- 未来确有需求时，再评估只读、最小化 Git 元数据视图或专用 Git 服务接口，不能直接挂载整个原仓库 `.git`。

这意味着 P0 的测试、构建和格式化命令应以 worktree 文件内容为依据，不应依赖在沙箱内运行任意 Git 命令。

### 7.4 环境准备与依赖

worktree 会隔离已跟踪的项目文件，但不会自动携带未跟踪虚拟环境、缓存、密钥或本地配置。P0 使用以下优先级：

1. 受信任、可审计的项目环境准备声明；
2. 沙箱内只读基础运行环境与受限临时缓存；
3. 用户明确批准的额外配置。

不复制用户主目录、`node_modules`、虚拟环境、`.env` 或忽略文件来“让任务跑起来”。这可能降低首次验证成功率，但能保持隔离边界和输入来源可追溯。

### 7.5 取消与清理

取消按以下顺序传播：

```text
用户取消 TaskRun
  -> Scheduler 不再启动新节点
  -> Sandbox Executor 终止任务进程组并等待清理
  -> Worktree Manager 记录最终 diff 与 Artifact
  -> Task 进入 cancelled / waiting-delivery
  -> 用户选择保留或丢弃 worktree
```

Sandbox 终止不等于删除 worktree。只有用户明确丢弃，且 Worktree Manager 已确认不存在正在运行的 Executor 或待交付动作，才允许移除 task worktree。

## 8. 生命周期与恢复

```text
created
  -> preparing
  -> active
  -> completed / cancelled / failed
  -> delivered / retained / discarded
```

- `preparing`：创建 worktree、记录基线、按授权应用 tracked diff 快照、运行受控环境准备。
- `active`：Agent 执行图在 task worktree 中运行。
- `completed`：任务已达到结束条件，等待用户审查与交付选择。
- `cancelled` / `failed`：停止新执行，保留状态和可审查结果。
- `discarded`：确认清理后调用 Git worktree 移除；保留最小任务元数据，不保留完整源码。

进程重启后根据 `task_id -> worktree_path -> base_commit` 恢复任务关联。路径不存在、Git 状态异常或基线已无法验证时，任务进入 `waiting`，不尝试重新创建或自动合并。

## 9. P0 验收

- 有 Git 仓库的可写任务默认不修改用户原工作区。
- 两个可写任务可在不同 worktree 中并行运行，且不会互相看到未交付文件修改。
- 用户可看到相对基线的 diff 与验证结果，再选择是否应用。
- 应用前检测原工作区是否已偏离任务基线；冲突不自动解决。
- 取消、失败和进程重启后，含有未交付变更的 worktree 不被静默删除。
- 非 Git 项目或 worktree 创建失败时，P0 禁止进入自动可写模式，退回只读解释或要求用户选择替代路径。
- Sandbox 仅可写 task worktree 的 `/workspace`，不能访问用户原工作区、主目录、凭据或网络。
- Git worktree 公共元数据不因测试/构建失败而自动暴露给沙箱命令。
- 取消任务会终止 Sandbox Executor，但不会静默删除含有未交付 diff 的 worktree。

## 10. 后续开放项

- worktree 环境准备：是否支持项目声明的受信任 setup 脚本；
- worktree 的磁盘配额、保留数量、自动清理时间和快照策略；
- 是否支持用户指定已有 worktree 作为任务目标；
- tracked diff 快照的冲突、二进制文件和大文件处理；
- P1 是否支持用户确认后的 Git commit、分支创建或 PR 交付。
