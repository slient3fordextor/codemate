# Token 上下文预算、记忆治理与项目沙箱技术选型

## 文档状态

本文档最初用于实现前选型。2026-07-29 已完成记忆侧 Token 预算、确定性冲突检测、C1/C2/C4 智能压缩、异步 SQLite 边界和管理 API；可选 C3 语义压缩与命令沙箱仍未实现。当前运行行为以设计版 `11` 和流程版 `docs/流程/03` 为准。

本轮只确定方案和边界，不修改运行代码。选型确认后，实现阶段再同步设计版和运行流程版文档。

## 要解决的问题

### 上下文单位

当前 L2 摘要使用字符数限制，无法真实反映模型上下文消耗。中文、英文、代码和 JSON 的“字符数 / Token 数”比例差异很大，仅限制 L2 也无法防止 `L3 + L2 + L1 + 代码上下文 + 当前问题` 整体超限。

### 项目沙箱

当前代码尚未提供后端命令执行器。一旦后续允许模型运行测试、构建或脚本，仅校验 `WORKSPACE_ROOT` 路径不足以形成安全边界。仓库内恶意脚本、依赖安装器或模型生成命令仍可以读取用户主目录、连接网络、启动大量进程或修改真实工作区。

### 记忆治理

当前 L1/L2/L3 可以持久化和跨进程恢复，但尚不能判断新指令与旧偏好、L1 与 L2、当前代码事实与历史摘要是否冲突。L2 超限时也只保留尾部文本，不能识别哪些决策、约束、错误和未完成任务必须保留。

## 选型原则

- 默认拒绝：未显式允许的文件、网络、环境变量和系统资源均不可见。
- 离线可用：基本 Token 计数不应强制增加一次模型 API 请求。
- 模型感知：不同 provider 的 tokenizer 可以不同，不伪装存在“通用精确 Token 计数器”。
- 单机优先：当前产品是本地 Agent，先选择不需要常驻守护进程和 root 权限的方案。
- 安全降级：沙箱依赖不可用时不能静默退回宿主机直接执行。
- 结果可审计：计数器、裁剪结果、沙箱策略和执行原因需可记录和测试。

## Token 计数方案对比

| 方案 | 精度 | 覆盖范围 | 运行成本 | 主要问题 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 字符数或固定比例 | 低 | 全部模型 | 最低 | 中英文和代码误差不稳定 | 仅可作降级估算 |
| 只用 `tiktoken` | OpenAI 已知模型高 | OpenAI 模型族 | 低 | 不能精确代表 Claude、Qwen、DeepSeek 等 tokenizer | 作为 OpenAI 计数后端 |
| Hugging Face `tokenizers` / `transformers` | 取决于 tokenizer 配置 | 开源模型较广 | 中到高 | 依赖较重；可能需要下载模型资源；不保证与托管 API 完全一致 | 作为后续可选后端 |
| provider Token Counting API | 高 | provider 自己的模型 | 高 | 额外网络请求、延迟、鉴权和限流 | 严格模式或未知 tokenizer 可选 |
| 分层 `TokenCounter` | 已知模型高，未知模型保守估算 | 全部当前 provider | 可控 | 需维护模型到计数器的显式映射 | **选择** |

OpenAI 官方 `tiktoken` 提供 `encoding_for_model()`，但对无法映射的模型会抛出 `KeyError`，因此不应将其当成所有 provider 的通用答案。Claude 提供独立 Token Counting API，适合要求精确预检的可选模式，不适合默认每轮强制调用。

## Token 计数选型结论

### 统一接口

```python
class TokenCounter(Protocol):
    counter_id: str

    def count_text(self, text: str) -> int: ...

    def count_messages(self, messages: list[ChatMessage]) -> int: ...
```

`count_messages()` 必须包含 role、消息边界和 provider 协议开销，不能只把 `content` Token 数相加。

### 计数器选择顺序

1. 当 provider/model 有已验证的本地 tokenizer，使用精确计数器。OpenAI 已知模型首选 `tiktoken`。
2. provider 提供官方计数 API 时，仅在 `strict` 模式或预算接近上限时调用。
3. 未知模型使用 `EstimatedTokenCounter`，按 UTF-8 字节和消息数给出保守估算，并在计数结果标记 `estimated=true`。
4. 不允许把未知模型默认当成某个 OpenAI 编码，否则会产生虚假精确度。

### 统一预算

```text
input_budget
  = model_context_window
  - requested_output_tokens
  - provider_protocol_margin
```

建议默认保留 5% 的 provider 协议安全余量。裁剪不使用单一硬比例，而是按以下优先级分配：

1. 必须保留当前 user 问题和最小 system 指令。
2. 从新到旧放入 L1 轮次，必须成对保留 user/assistant，不拆半轮。
3. 从当前文件开始放入代码上下文，再放相关文件。
4. 保留与当前请求相关的 L3 偏好，相关性相同时新偏好优先。
5. 剩余预算给 L2 摘要；如果仍超限，从 L2 最旧部分开始裁剪。

L2 配置从 `SESSION_MEMORY_MEDIUM_TERM_MAX_CHARS=6000` 迁移为 `SESSION_MEMORY_MEDIUM_TERM_MAX_TOKENS=1536`。迁移时对旧环境变量给出弃用警告，不做字符到 Token 的静默等价换算。

### 计数结果缓存

文本 Token 数可按 `SHA-256(text) + counter_id` 做有界 LRU 缓存。`counter_id` 需包含 provider、model、encoding 和计数器版本，避免模型切换后复用错误结果。

## 记忆模块全量检查

2026-07-29 对 `session_memory.py`、`persistent_memory.py`、`chat.py`、配置和测试做了完整检查。下表记录的是实现前发现的问题；本轮已逐项处理，最终验证结果见本文末尾和运行流程版。

| 严重度 | 问题 | 当前表现 | 选定处理 |
| --- | --- | --- | --- |
| 高 | 历史内容权限提升 | L2 摘要和 L3 偏好以 `system` 消息注入，历史 user/assistant 文本可能被提升为高优先级指令 | 记忆改为结构化、低信任上下文；在组装 prompt 前解决冲突，不靠 system 文案声明“新指令优先” |
| 高 | 无冲突检测 | “以后使用中文”和“以后使用英文”会同时保存并注入 | 引入 `ContextConflictDetector` 和显式裁决矩阵，旧值标记 superseded 而不立即物理删除 |
| 高 | L2 不可逆截断 | 摘要超过字符上限后直接保留尾部，可从句子中间截断并永久丢失早期决策 | 实现按 Token 触发的分级智能压缩、结构化摘要和版本化来源 |
| 高 | 整体上下文无预算 | 只限制 L1 轮数和 L2 字符，L1/L2/L3/代码合计仍可超过模型窗口 | 实现 `TokenCounter` + `ContextBudgetPlanner` |
| 中 | 同步 SQLite 阻塞事件循环 | `get_messages()` 和 `append_turn()` 在 async 流程里同步执行，写竞争时最多等待 5 秒 | 将存储协议改为 async，使用 `asyncio.to_thread()` 或专用数据库执行器 |
| 中 | 记忆异常无结构化处理 | 仅 `ModelProviderError` 会被转为 SSE error，SQLite 初始化/锁/磁盘错误可直接中断流 | 定义 `MemoryStoreError`，在开流前预读；配置 fail-closed 或可审计的无记忆降级 |
| 中 | L3 无相关性和作用域配额 | 全局与项目偏好按更新时间共享 20 条配额，可互相挤出，与当前任务无关的偏好也会注入 | 先按 scope/category 取候选，再做相关性排序和 Token 配额 |
| 中 | 缺少可管理与可忘记性 | `clear()` 语义不明确，项目清理不会删除全局偏好；无 API/UI 查看、更正、禁用或删除长期记忆 | 拆分 `clear_session`、`clear_project`、`delete_preference`、`clear_global_preferences` 并增加审计 API |
| 中 | 敏感信息检测过窄 | 只检查少量关键词，没有复用 `SecurityConfig`，不覆盖私钥块、云密钥、cookie 等 | 与统一敏感内容检测器共用策略，保存前二次检查 |
| 中 | 无写入幂等性 | 请求重试可重复追加同一轮 | 引入 `turn_id/request_id` 唯一键，同一次请求只提交一次 |
| 中 | 数据库缺少正式迁移 | 启动时直接 `CREATE TABLE IF NOT EXISTS` 并重写 `user_version=1` | 引入顺序 migration，拒绝打开高于当前程序版本的数据库 |
| 低 | 读取快照未显式固定 | summary、turns 和 preferences 分三次查询，没有显式读事务 | 使用单一读事务保证同一快照 |
| 低 | 后端语义不一致 | `backend=memory` 只有 L1，`backend=sqlite` 同时启用 L1/L2/L3 | 在能力探测中显示层级，不将 memory 后端称为等价降级 |

### QA 处理结果

| QA 项 | 结果 |
| --- | --- |
| 历史内容权限提升 | 已处理：L2/L3 改为低信任 `memory_context`，不再使用 system 角色 |
| L3 与当前请求冲突 | 已处理：结构化 key/value 裁决，当前请求和项目作用域稳定胜出 |
| L1 与 L2 冲突 | 已处理：注入前过滤被 L1 新状态取代的 L2 行 |
| L2 不可逆截断 | 已处理：schema v3 `SummaryChunk`、必保项校验和压缩审计元数据 |
| 整体上下文预算 | 已处理：`ContextBudgetPlanner` 覆盖必需上下文、L1/L2/L3 和输出预留 |
| SQLite 阻塞与快照 | 已处理：async 协议、`asyncio.to_thread()`、显式读/写事务 |
| 异常静默丢失 | 已处理：`MEMORY_STORE_ERROR`、`CONTEXT_BUDGET_EXCEEDED`、`memory.warning` |
| L3 相关性与配额 | 已处理：作用域优先、相关性排序、category 限额和 Token 限额 |
| 管理与遗忘 | 已处理：能力、偏好、冲突审计和会话/项目/全局删除 API |
| 敏感信息 | 已处理：落盘前统一脱敏，敏感句不提取为长期偏好 |
| 写入幂等 | 已处理：`project_key + session_id + request_id` 唯一索引 |
| 正式迁移 | 已处理：v1 -> v2 -> v3 顺序 migration、并发初始化事务、高版本拒绝 |
| 后端能力差异 | 已处理：`/memory/capabilities` 明确返回真实层级和持久化能力 |

可选 C3 语义压缩和无结构化 key 的低置信度语义冲突仍属于后续增强；当前不会伪装为已经完成，也不会为了摘要额外调用模型。

## 上下文冲突检测选型

### 检测对象

冲突检测覆盖整个上下文模块，不只检查 L3：

- 当前 user 指令与 L3 全局/项目偏好；
- 同一 category/key 下的新旧 L3 偏好；
- L1 最新状态与 L2 历史摘要；
- 当前代码、配置和文件系统事实与记忆中的旧项目事实；
- 用户当前操作模式与历史 workflow 偏好。

### 冲突类型

```text
compatible   可同时生效
duplicate    语义重复
supersedes   新内容明确取代旧内容
contradicts  不能同时成立
uncertain    证据不足，不自动裁决
```

### 检测策略

1. 先对结构化 `scope + category + key` 做确定性检查，例如 `response_language=zh` 与 `response_language=en`。
2. 再做否定词、枚举值和规范化文本规则检查。
3. 只对可能冲突的少量候选执行可选语义判定，不把所有记忆每轮都送给模型。
4. 语义判定必须返回结构化 JSON，包含冲突类型、置信度和证据 ID；低置信度统一为 `uncertain`。
5. 检测阶段不物理删除内容，仅写入冲突关系和 active/superseded 状态，便于审计与恢复。

### 裁决优先级

| 优先级 | 来源 | 裁决规则 |
| --- | --- | --- |
| 1 | 当前轮明确 user 指令 | 总是高于历史记忆；安全和开发者固定规则除外 |
| 2 | 当前工作区可验证事实 | 当前文件、配置和工具结果高于历史摘要 |
| 3 | 当前会话 L1 | 同一事实以更新的轮次为准 |
| 4 | 项目 L3 | 在当前项目中高于全局 L3 |
| 5 | 全局 L3 | 只在当前请求和项目没有更具体指令时生效 |
| 6 | L2 历史摘要 | 作为背景而非指令，冲突时最先被覆盖 |

`uncertain` 冲突不能由模型暗中选一个。它需在上下文中标记不确定；如果会改变写文件、执行命令或安全策略，必须请求用户确认。

### 数据结构

```text
MemoryItem
  id, layer, scope, category, key, value
  source_type, source_id, created_at, observed_at
  confidence, active, superseded_by

MemoryConflict
  id, left_memory_id, right_memory_id
  relation, confidence, resolution, resolved_by, resolved_at
```

没有 `key/value` 的自由文本仍可保存，但只能参与重复检测和可选语义判定，不进行伪精确规则裁决。

## 智能压缩选型

### 目标

智能压缩不是“把文本从头部删掉直到变短”，而是在 Token 预算内优先保留当前目标、已确认决策、用户约束、未完成任务、错误证据和冲突状态。

### 不可丢失内容

- 当前用户的明确指令和否定约束；
- 已确认的技术/产品决策及其作用域；
- 尚未完成的任务、阻塞原因和用户待回答问题；
- 正在使用的文件、符号、错误码、命令和关键输出；
- 未解决冲突及双方证据 ID；
- 安全、隐私、沙箱和数据删除边界。

### 压缩级别

| 级别 | 触发条件 | 动作 | 是否调用模型 |
| --- | --- | --- | --- |
| C0 | 预算充足 | 不压缩 | 否 |
| C1 无损整理 | 达到软阈值 | 去重复系统文本、空白、重复工具输出；不改写代码和引号内文本 | 否 |
| C2 抽取压缩 | C1 后仍超预算 | 把轮次转换为目标、决策、约束、状态、待办、证据等结构化项 | 否 |
| C3 语义压缩 | C2 后仍超预算且已配置压缩模型 | 对低冲突风险的历史块做语义摘要，返回结构化结果 | 是 |
| C4 紧急裁剪 | 距硬上限过近或语义压缩失败 | 按相关性、时效和可恢复性删除最低优先级内容，记录丢失项 | 否 |

软阈值建议为输入预算的 75%，硬阈值为 90%，剩余 10% 用于 provider 协议差异和估算误差。如果使用 provider 精确计数，仍保留最小安全余量。

### 分层压缩流程

```text
收集 L1/L2/L3/代码上下文
  -> 规范化为 MemoryItem / ContextItem
  -> 检测冲突并标记 active / superseded / uncertain
  -> 锁定不可丢失项
  -> TokenCounter 计数
  -> 依次执行 C1 / C2 / C3 / C4
  -> 重新计数并校验必保项
  -> 组装最终 prompt
```

冲突检测必须先于压缩。否则智能摘要可能把“使用中文”和“改用英文”融合成一条无法执行的错误规则。

### 摘要再次超限

L2 不再只保存一个不可拆分的 `summary` 字符串，而是保存有 Token 数和来源范围的摘要块：

```text
SummaryChunk
  id, session_id, level, content, token_count
  source_start_turn_id, source_end_turn_id
  required_item_ids, conflict_ids, created_at
```

摘要块合计超限时，先合并相邻且无冲突的旧块，再按 C2/C3 重新压缩。每次都保留来源轮次范围、必保项和压缩版本，不对整份摘要反复盲目改写。

### 压缩结果验证

- 压缩前后的必保 `MemoryItem.id` 集合必须一致；
- 文件路径、符号、错误码、数值、版本和否定词做确定性校验；
- 任何未解决 `MemoryConflict` 必须仍然可见；
- C3 返回的新事实如果无法追溯到输入证据，直接丢弃；
- 验证失败时回退到 C2 结果，不使用未验证的语义摘要。

## 项目沙箱威胁模型

需同时防御：

- 路径穿越、符号链接逃逸和对真实工作区的未授权写入。
- 读取 `~/.ssh`、`~/.aws`、`.env`、浏览器数据、Git 凭据、SSH Agent 和容器 socket。
- 通过网络上传源码、日志或密钥。
- fork bomb、内存/磁盘/文件描述符耗尽和超长运行。
- 通过 `/proc`、`/sys`、D-Bus、宿主网络或特权 capability 探测和影响宿主机。
- 无限制命令输出挤爆内存、日志或模型上下文。

不在当前范围内：多机租户隔离、Windows/macOS 原生沙箱和面向公网攻击者的强多租户托管。

## 沙箱方案对比

| 方案 | 隔离能力 | 启动/资源成本 | 部署条件 | 适用性 | 结论 |
| --- | --- | --- | --- | --- | --- |
| `subprocess` + `cwd` + `rlimit` | 只限制部分资源，无文件/网络隔离 | 最低 | Python 即可 | 不是安全边界 | 拒绝单独使用 |
| Landlock | 进程自限制文件访问 | 低 | 需要对应 Linux ABI | 适合叠加文件防线，不解决完整进程/网络隔离 | 后续防御纵深 |
| Bubblewrap | mount/user/PID/IPC/network/UTS namespace，可传入 seccomp | 低 | Linux user namespace，单一可执行文件 | 本地单次命令、按路径构建文件视图 | **Linux 默认后端** |
| Rootless Podman | OCI 容器、seccomp、capability、namespace、cgroup | 中 | 需要容器镜像和 rootless 运行环境 | 需要稳定 rootfs/依赖或更强限制 | **可选 hardened 后端** |
| Rootless Docker | 与 OCI 容器类似 | 中 | 守护进程、rootless 配置和镜像 | 普及度高，但对本地单次任务过重 | 作为后续适配器 |
| NsJail | namespace + cgroup + rlimit + seccomp-bpf | 中 | 需安装和维护策略 | 隔离完整，但当前环境未安装 | 暂不默认 |
| gVisor | 用户态应用内核隔离宿主内核 | 中到高 | `runsc` + OCI 工具 | 强隔离，但有 syscall 兼容性和性能成本 | 托管/高风险模式备选 |
| Firecracker | microVM、独立 guest kernel | 最高 | KVM、kernel/rootfs 镜像和 VM 生命周期 | 强多租户隔离 | 当前本地 MVP 不选 |

Bubblewrap 官方明确它是构建沙箱的低层工具，不是自带安全策略的完整产品；安全性取决于调用参数。因此必须在 CodeMate 内部定义不可由模型覆盖的 `SandboxPolicy`，不允许模型自由组装 `bwrap` 参数。

## 沙箱选型结论

### 后端抽象

```python
class SandboxBackend(Protocol):
    def probe(self) -> SandboxCapabilities: ...
    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult: ...
```

第一阶段实现 `BubblewrapSandboxBackend`，后续可增加 `PodmanSandboxBackend`。业务层只提交结构化请求，不接受一整段 shell 字符串作为沙箱策略。

### Bubblewrap 默认策略

- 使用 user、PID、IPC、UTS、mount 和 network namespace，并启用 `--new-session`。
- 根文件系统从空 tmpfs 开始，只读挂载运行命令必需的 `/usr`、库、证书和解析器文件。
- 不挂载宿主 `$HOME`、`.ssh`、`.aws`、D-Bus、SSH Agent、Docker/Podman socket 和用户环境文件。
- 实际工作区默认只读挂载为 `/workspace`；`/tmp`、`/run` 和缓存目录使用有大小上限的临时存储。
- 网络默认关闭。安装依赖必须进入独立策略，后续通过 allowlist 代理提供有限网络，不共享宿主网络 namespace。
- 环境变量采用 allowlist 重建，不传入 API key、cookie、proxy 和凭据变量。
- 输入 argv 数组而不是默认经 `shell=True` 执行；确实需要 shell 语法的任务使用独立高风险类型。
- 使用 seccomp 阻止已明确不需要的高风险 syscall；策略必须随测试命令集合做兼容性回归。

### 写入模式

不把真实工作区直接以可写方式挂载给命令。

1. 主机侧创建临时工作区快照，优先使用 reflink，不可用时再复制。
2. 沙箱只写该快照。
3. 命令结束后在主机侧生成变更清单和 diff。
4. 通过现有操作模式决定是否请求用户确认。
5. 由主机侧经路径校验的 `PatchApplier` 将获准变更应用到真实工作区。

该路径同时保护用户当前未提交修改，也避免沙箱命令绕过审批直接改文件。

### 资源和输出限制

- 必须有 wall-clock timeout，超时后终止整个进程组。
- 优先使用 cgroup v2 限制 CPU、内存和 PID；不可用时叠加 `rlimit`，并把能力状态标记为 `degraded`。
- 限制单文件、临时空间、文件描述符和进程数。
- stdout/stderr 必须流式读取，同时使用字节硬上限和 Token 预算上限。超限后保留开头、结尾和截断原因，不将无界输出送入模型。

## 当前环境验证

2026-07-29 在当前 CodeMate Linux 开发环境检查：

- Bubblewrap `0.10.0` 已安装；
- `bwrap --unshare-all --new-session ... /usr/bin/true` 冒烟验证通过；
- 宿主使用 cgroup v2；
- Rootless Podman 可执行文件存在，但当前运行目录权限检查失败，不适合作为默认后端。

这些结果只证明具备实现前提，不等于安全策略已通过验收。

## 建议实施顺序

1. 实现 `TokenCounter` 和 `ContextBudgetPlanner`，将 L1/L2/L3、代码上下文和命令输出统一换成 Token 预算。
2. 将记忆候选规范化为 `MemoryItem`，先解决历史内容被提升为 system 指令的问题。
3. 实现 `ContextConflictDetector`、裁决矩阵和 `MemoryConflict` 审计记录。
4. 实现 C1/C2 无模型智能压缩与 `SummaryChunk`，再增加可选 C3 语义压缩。
5. 将 SQLite 访问改为 async 边界，增加幂等键、正式 migration、统一敏感检测和记忆管理 API。
6. 实现 `SandboxBackend` 接口、能力探测和“沙箱不可用即拒绝执行”。
7. 先落地 Bubblewrap 只读工作区和无网络命令，覆盖测试、静态检查和构建。
8. 增加资源监督、输出 Token 裁剪、取消和审计事件。
9. 实现临时快照写入模式和主机侧受控 patch 应用。
10. 在需要固定镜像或更强隔离时，增加 Rootless Podman 后端；暂不引入 gVisor/Firecracker。

## 验收标准

### Token 预算

- 所有上下文限制对外使用 Token，不再使用字符作为主预算单位。
- 每次请求可记录 `counter_id`、是否估算、各层 Token 用量和裁剪原因。
- 不发送已知超过模型上下文窗口的请求。
- L1 裁剪不会留下孤立 user 或 assistant 消息。
- 未知模型明确标记为估算，不返回虚假精确值。

### 记忆冲突与智能压缩

- 当前 user 指令与历史记忆冲突时，当前指令稳定胜出，不依赖模型自行猜测。
- 项目 L3 与全局 L3 冲突时，只在当前项目内使用项目值。
- L1 新事实与 L2 旧摘要冲突时，L2 不再注入过期值。
- `uncertain` 冲突保留双方证据，不会在压缩中被融合成虚构结论。
- C1/C2/C3/C4 每级都使用 Token 重新计数，并记录压缩原因、前后 Token 数和丢失项。
- 当前目标、已确认决策、用户约束、未完成任务、关键证据和未解决冲突在压缩后仍然存在。
- C3 语义压缩验证失败时回退 C2，不注入未验证摘要。

### 项目沙箱

- 沙箱内无法读取工作区外用户数据、凭据和宿主 socket。
- 默认无网络，不存在静默共享宿主网络的降级路径。
- 超时、超内存、超 PID 和超输出限制均会终止任务并返回结构化原因。
- 只读任务不能修改真实工作区；写任务只能通过快照和受控 patch 应用产生变更。
- 不支持沙箱时返回能力错误，不回退到宿主机执行。
- 安全测试覆盖路径/符号链接逃逸、凭据读取、网络连接、fork bomb、超时和无限输出。

## 选型结论

| 领域 | 当前选择 | 不选的默认方案 |
| --- | --- | --- |
| 上下文单位 | 分层 `TokenCounter` + 统一 `ContextBudgetPlanner` | 单独字符上限 |
| OpenAI 已知模型 | `tiktoken` | 所有 provider 强制套用 OpenAI 编码 |
| 未知模型 | 明确标记的保守 Token 估算 | 猜测一个“精确” tokenizer |
| 上下文冲突 | 结构化确定性检测 + 少量可选语义判定 + 显式裁决矩阵 | 把互相矛盾的记忆一起丢给模型 |
| 智能压缩 | C1 无损、C2 抽取、C3 语义、C4 紧急裁剪 | 字符串从头部直接截断 |
| Linux 本地沙箱 | Bubblewrap + CodeMate 固定安全策略 | 宿主 `subprocess` 直接执行 |
| 资源限制 | cgroup v2 优先，`rlimit` + timeout 降级 | 无限制执行 |
| 可写项目 | 临时快照 + diff + 受控应用 | 真实工作区直接 RW 挂载 |
| 容器增强 | Rootless Podman 可选后端 | 强制所有用户安装容器运行时 |
| 强多租户隔离 | 后续评估 gVisor/Firecracker | 当前 MVP 直接引入 microVM |

## QA

### Q1：为什么不直接让模型判断所有冲突？

全量模型判定会增加延迟和成本，同样输入还可能得到不同结果。语言、工具、测试策略等常见偏好可先转为结构化 key/value，用确定性规则判断。只有自由文本且规则无法判断时，才调用可选语义判定。

### Q2：新指令与长期偏好冲突时听谁的？

当前轮用户指令优先。如果用户明确表达“以后都改为……”，新偏好还会将同 key 旧偏好标记为 superseded。如果只是“这一次使用……”，它只覆盖当前轮，不修改 L3。

### Q3：智能压缩是否必须调用模型？

不是。C1 和 C2 是默认路径，可离线、确定性执行。C3 只是可选语义增强，而且必须通过必保项和证据校验。

### Q4：摘要自己再次超出 Token 预算怎么办？

不直接截断整个摘要。系统按来源轮次将 L2 保存为多个 `SummaryChunk`，优先合并旧且无冲突的块，重新执行 C2/C3。仅在接近硬上限时进入 C4，并明确记录丢失的项。

### Q5：为什么历史摘要不应直接变成 system 指令？

摘要来源包含旧用户文本、模型回答和可能的不可信内容。将它放进 system 角色会提高其指令权限。应先转为带来源和信任等级的结构化背景，排除被冲突裁决淘汰的项，再以明确的“不可信历史数据”边界注入。

### Q6：压缩失败会不会阻断对话？

当前实现不启用 C3。C2/C4 如果无法保留必保项，会回滚记忆写事务并发送 `memory.warning`；如果是本轮必需 prompt 已超过模型输入预算，则在调用 provider 前返回 `CONTEXT_BUDGET_EXCEEDED`。两条路径都不会静默删除当前指令或安全约束。

## 2026-07-29 实现验证

- `pytest -q`：60 passed；
- `ruff check app tests`：通过；
- `mypy app`：通过；
- `node --check app/static/app.js`：通过；
- HTTP 冒烟：`context.usage`、模型流、偏好 superseded、冲突审计均通过；
- 重启恢复：schema v3 数据库恢复 3 轮、2 条偏好和 1 条冲突记录；
- 数据库权限：`0600`。

## 官方资料

- [OpenAI tiktoken](https://github.com/openai/tiktoken)
- [Anthropic Token counting](https://docs.anthropic.com/en/docs/build-with-claude/token-counting)
- [Bubblewrap README](https://github.com/containers/bubblewrap/blob/main/README.md)
- [Bubblewrap security policy](https://github.com/containers/bubblewrap/blob/main/SECURITY.md)
- [Podman rootless mode](https://docs.podman.io/en/latest/markdown/podman.1.html#rootless-mode)
- [Podman run options](https://docs.podman.io/en/stable/markdown/podman-run.1.html)
- [Linux Landlock](https://docs.kernel.org/userspace-api/landlock.html)
- [NsJail](https://github.com/google/nsjail)
- [gVisor security introduction](https://gvisor.dev/docs/architecture_guide/intro/)
- [Firecracker design](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md)
