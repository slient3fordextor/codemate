877# Agent 编排方案调研

> 调研日期：2026-08-03。本文记录当前公开框架与设计模式，用于 CodeMate Agent CLI 的技术评测；不代表已选型或已实现能力。

## 1. 结论

CodeMate 的候选方向应是：**自研执行图运行时 + 有限状态机循环 + Generate-Verify-Repair 编码单元 + 少量 ReAct 诊断节点 + 事件持久化与人工审批恢复**。

图运行时负责任务级的依赖、分支、并行、等待和恢复；单元循环负责某一明确目标的收敛；模型只在需要推理、探索或根据新证据调整时介入。文件修改、命令执行和审批不得由模型循环直接绕过策略层。

LangGraph 与耐久工作流产品应作为评测基线，不应在 P0 未验证需求前直接绑定。

## 2. 方案全景

| 方案 | 核心能力 | 对 CodeMate 的价值 | 主要限制 |
| --- | --- | --- | --- |
| 自研执行图 + 事件存储 | 节点、边、并发、审批、恢复完全可控 | 最适合本地 CLI 的权限、diff、沙箱和任务恢复 | 实现、测试和运维成本最高 |
| LangGraph | 图、条件边、循环、检查点、人工中断恢复 | 贴合分支、等待、恢复需求 | 恢复会重放节点函数，副作用需幂等 |
| 耐久工作流 | Temporal、DBOS、Prefect、Restate 的持久化、重试与队列 | 长任务、进程重启恢复、可靠执行 | 对单机 CLI 首期可能偏重 |
| 确定性工作流 | 顺序、并行、循环模板 | 快速验证有限循环和并行的调度语义 | 动态图与复杂恢复能力有限 |
| 多 Agent 图 | 专家分工、并行审查、结果汇聚 | 后期可用于受限的专家协作 | 增加模型调用、Token、审批面和调试复杂度 |
| Agent Runtime | 内建 tool loop、handoff、guardrail、追踪 | 可作为模型层循环和观测能力参考 | 不会替代本地权限、文件策略和沙箱 |

## 3. 图与耐久执行方案

### 3.1 自研执行图

执行图以 `TaskRun`、`GraphNode`、`GraphEdge`、`NodeAttempt`、`Approval`、`Artifact` 和不可变 `Event` 为核心。它对 CodeMate 的特殊价值在于：写文件、运行命令、Git 操作和访问工作区外资源都需要产品自有的策略判定、diff 展示和恢复语义。

首期不需要支持任意动态 DAG，应按顺序增加：顺序、条件分支、等待审批、有限循环、并行汇聚和恢复。详细候选设计见 [02-Agent执行图设计与评测](02-Agent执行图设计与评测.md)。

### 3.2 LangGraph

LangGraph 提供图、条件路由、检查点和 `interrupt` 人工中断。中断会保存图状态，使用同一 thread ID 可以恢复。它的关键限制是：恢复后中断所在的节点会从头运行；因此节点内中断之前的副作用可能重放，写文件和命令必须设计为幂等操作，或拆成受控的单独节点。

LangGraph 还以 super-step 为并行边界保存检查点，并保存单节点的 pending writes，使同一并行批次中已成功的节点不必因其他节点失败而重跑。

资料：[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)。

### 3.3 Temporal、DBOS、Prefect、Restate

Pydantic AI 当前官方支持 Temporal、DBOS、Prefect 和 Restate 四种耐久执行集成。它们将模型请求、工具调用或 MCP 通信包装为可持久化的工作流步骤，从而支持重试、超时、恢复和队列管理。

Temporal 要求工作流协调逻辑保持确定性，I/O 放在 activities；DBOS 以数据库 checkpoint 在重启后从最后完成步骤恢复；Prefect 提供 task 的重试、缓存和事务语义。它们适合后期的长任务或更高可靠性需求，但会引入运行时、部署和流式事件语义的额外复杂度。

资料：[Pydantic AI Durable Execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)、[Temporal](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/)、[DBOS](https://pydantic.dev/docs/ai/capabilities/durable_execution/dbos/)、[Prefect](https://pydantic.dev/docs/ai/capabilities/durable_execution/prefect/)。

## 4. 循环与工作流模式

### 4.1 有限确定性循环

Google ADK 将顺序、并行和循环作为不由模型控制的工作流模板。Loop 必须有最大迭代次数或显式终止条件；Parallel 只应用于相互独立的分支。这一约束应直接吸收进 CodeMate 的 Graph Scheduler：循环的停止权属于运行时和可验证结果，不能交给模型自由决定。

资料：[Google ADK Workflow Agents](https://adk.dev/agents/workflow-agents/)、[Loop Agent](https://adk.dev/agents/workflow-agents/loop-agents/)、[Parallel Agent](https://adk.dev/agents/workflow-agents/parallel-agents/)。

### 4.2 ReAct

ReAct（推理 -> 行动 -> 观察）是节点内部的探索策略，而不是图运行时。它适用于诊断、检索和信息不完整的任务；不适用于已批准补丁、读取指定文件和执行已批准命令等确定性操作。

每个 ReAct 单元必须受最大模型轮数、工具白名单、Token 预算、总时长、失败次数和明确退出条件约束。轮数与重规划是两个独立预算：普通动作不能因为重规划次数耗尽而被阻断；超限批准必须绑定明确的下一轮或下一次重规划，不能使用可重复消费的布尔值。达到边界后，节点应失败、请求用户输入或交给图上的诊断分支，不能无限循环。

### 4.3 Plan-and-Execute 与 Generate-Verify-Repair

- Plan-and-Execute：先产生结构化计划，按步骤执行，在新证据出现时重新规划。适合大型、可分解任务。
- Generate-Verify-Repair：生成补丁，运行验证，依据错误证据修复。适合作为代码修改单元的默认有限循环。

CodeMate 应优先使用 Generate-Verify-Repair，因为其完成条件可由 diff 和验证结果证明；ReAct 主要承担诊断与上下文收集。

## 5. 多 Agent 与 Agent Runtime

### 5.1 多 Agent 图

AutoGen GraphFlow 支持 DAG、并行 fan-out/fan-in、条件循环和消息过滤。其“执行图”与“消息图”分离的思想值得借鉴：并行执行不意味着让每个参与者接收所有结果，应只按需要发送结构化摘要或证据引用。

资料：[AutoGen GraphFlow](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/graph-flow.html)。

多 Agent 不应成为 P0 默认能力。模型调用、上下文、审批和失败路径都会随角色数量增加；先证明单 Agent + 工具图无法满足某类任务，再加入职责狭窄的专家节点。

### 5.2 OpenAI Agents SDK

OpenAI Agents SDK 提供 Agent tool loop、handoff、agents-as-tools、guardrail 和 tracing。其架构建议是：先使用单 Agent；只有职责、工具集合或策略真正改变时才拆分为 handoff 或 specialist。对于 CodeMate，工具 guardrail 的“每次调用前后检查”思路可映射为策略引擎的执行前后判定。

资料：[Agents SDK](https://openai.github.io/openai-agents-python/)、[Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)、[Guardrails](https://openai.github.io/openai-agents-python/guardrails/)。

## 6. 性能、Token 与严谨性

| 目标 | 推荐策略 | 代价与防线 |
| --- | --- | --- |
| 降低延迟 | 并行安全只读工具；确定性节点直连执行；减少无意义模型往返 | 并行分支必须独立，汇聚顺序和输出大小受控 |
| 降低 Token | 按需读取；工具输出结构化摘要；文件/搜索缓存；只向节点提供相关证据；限制 ReAct 轮数 | 摘要不可覆盖原始 artifact，关键结论保留文件/命令证据引用 |
| 提高严谨性 | 先取证再结论；用 diff、测试、Git 状态约束模型；模型输出 schema 校验 | 会提高工具调用数和延迟，应只对高风险节点使用强校验 |
| 提高准确性 | 当前文件、工具结果和验证输出高于记忆与模型猜测；不确定时继续取证或请求用户 | 需要上下文优先级和预算治理 |

严谨不等于生成更长的模型推理文本。对 CLI 来说，严谨应表现为：结论关联可查看的文件、命令输出、diff 或测试结果；不确定性明确显示；副作用都经过策略检查和记录。

## 7. CodeMate 评测建议

以当前 [执行图评测计划](02-Agent执行图设计与评测.md) 为主线，增加以下对照：

| 对照对象 | 要验证的问题 |
| --- | --- |
| 串行单 Agent loop | 图是否真的改善等待、分支、恢复和解释性 |
| 自研 Graph Runtime | 是否能在本地 CLI 中实现最小权限、diff、恢复和流式交互 |
| LangGraph 原型 | 检查点和 interrupt 是否足够匹配审批恢复；副作用重放成本是否可接受 |
| 耐久工作流原型 | 对长任务的恢复收益是否值得其运行时复杂度 |
| 单 Agent 与多 Agent | 专家拆分是否确实提高任务成功率，而非只增加 Token 和延迟 |

统一记录：任务完成率、验证通过率、首次有效反馈时间、总耗时、模型调用次数、输入/输出 Token、确认次数、恢复成功率、未确认副作用数，以及用户对当前任务状态的理解程度。

## 8. 当前开放决策

- P0 采用自研轻量状态机；LangGraph 仅作为调度评测基线；
- ReAct 默认 10 轮，11-15 轮仅在有新证据或验证进展时继续，超过 15 轮逐轮等待用户确认；单元 Token 预算和总时长仍待量化；
- 并行读取和并行验证的资源锁、取消与输出汇聚规则；
- SQLite 是否只记录事件与快照，或承担完整任务状态存储；
- 哪些验证命令可在 `agent` 模式预授权，哪些始终要求确认；
- 何时才有足够证据引入多 Agent、MCP 或完整耐久工作流产品。
