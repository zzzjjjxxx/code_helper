# 研发助手 Agent 系统技术文档

这份文档按“先看全局，再看运行链路，再看关键文件”的顺序写，目标是让第一次接触项目的人也能快速看懂。

## 1. 这个项目在做什么

它不是聊天机器人，而是一个会“接任务、查代码、改代码、跑测试、看结果、可回滚”的研发助手。

一次完整任务大致会做这些事：

1. 接收任务
2. 找相关代码
3. 读取上下文文件
4. 让 Agent 规划
5. 并行比较多个分支
6. 应用 patch
7. 跑测试
8. 根据结果继续修复或结束
9. 保存事件、产物、记忆
10. 必要时回滚

## 2. 系统结构

### 后端

- [FastAPI 入口](<../apps/api/main.py#L17>)：组装路由、存储、事件和回滚服务。
- [任务服务](<../apps/api/services/task_service.py#L28>)：真正把任务闭环串起来。
- [事件服务](<../apps/api/services/event_service.py#L14>)：写事件并实时推送给前端。
- [回滚服务](<../apps/api/services/rollback_service.py#L14>)：恢复到快照前状态。
- [SQLite 存储](<../apps/api/storage/sqlite.py#L95>)：保存任务、事件、快照、产物、记忆、子目标。

### Agent 层

- [工作流主类](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L64>)：任务如何被读、被规划、被修改、被验证。
- [并行分支选择](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L33>)：同一轮里让多个角色同时出方案。
- [LLM 封装](<../packages/agent-core/src/agent_core/llm.py#L65>)：把状态发给模型，再把 JSON 结果解析回来。

### 前端

- [任务详情页](<../apps/web/src/components/TaskDetail.tsx#L140>)：总控页面。
- [分支对比面板](<../apps/web/src/components/BranchComparisonPanel.tsx#L333>)：展示多 Agent 比较结果。
- [协作面板](<../apps/web/src/components/CollaborationPanel.tsx#L32>)：展示 planner / executor / reviewer / coordinator 的交接。
- [时间线](<../apps/web/src/components/TaskTimeline.tsx#L30>)：完整事件流。
- [产物面板](<../apps/web/src/components/ArtifactPanel.tsx#L1>)：看 diff、测试报告、分支比较摘要。

## 3. 当前阶段做到哪里了

现在已经不是单纯的 demo 了，已经能形成一个受控闭环：

- 能读代码
- 能找问题
- 能改代码
- 能跑测试
- 能回看 diff
- 能保存总结和记忆
- 能生成分支比较摘要
- 能回滚

也就是说，第一层到第五层都已经有落地，只是“通用 agent”的自治能力还有限，仍然偏任务驱动。

## 4. 关键数据对象

这些对象决定了系统里“任务是什么、事件是什么、产物是什么”。

### `Task`

任务本体，包含标题、描述、仓库路径、状态、当前步骤、最新 diff、测试结果、检索结果和总结。

### `Step`

表示任务运行到哪一步：

- `read`：读代码
- `analyze`：分析和规划
- `patch`：写补丁
- `test`：跑测试
- `review`：审查结果
- `summarize`：生成总结
- `rollback`：回滚

### `Event`

事件就是过程记录，比如：

- 读了哪个文件
- 哪个 Agent 开始工作
- 生成了哪个分支
- 应用了哪个 patch
- 测试是否通过
- 分支比较结果是什么

### `Artifact`

产物比如：

- `diff`
- `test_report`
- `branch_comparison`

### `Snapshot`

快照就是改代码前的备份，回滚时会恢复它。

### `Memory`

长期记忆，用来保存历史任务的结论、经验和相关文件。

## 5. LLM 配置在哪

默认从 [Settings](<../apps/api/core/config.py#L16>) 读取：

- `config/llm_config.json`
- 或环境变量 `APP_LLM_CONFIG_PATH`

配置示例：

```json
{
  "provider": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "OPENAI_API_KEY",
  "model": "your-model-name",
  "temperature": 0.2,
  "max_output_tokens": 2048,
  "timeout_seconds": 120
}
```

如果没配好，系统会自动退回启发式逻辑，不会真的调用模型。

## 6. 后端入口在做什么

[ `apps/api/main.py`](<../apps/api/main.py#L17>) 做了这些事：

1. 读取配置
2. 创建 FastAPI 应用
3. 加 CORS
4. 启动时初始化数据库、事件服务、回滚服务、任务服务
5. 注册路由

## 7. 任务闭环怎么跑

[ `TaskService`](<../apps/api/services/task_service.py#L28>) 是整个系统最核心的后端文件。

它负责：

1. 创建任务
2. 启动任务
3. 推进状态
4. 记录事件
5. 保存 diff、测试报告、记忆和分支比较摘要
6. 标记成功或失败

### 关键代码

- [创建任务 `create_task`](<../apps/api/services/task_service.py#L43>)
- [启动任务 `run_task`](<../apps/api/services/task_service.py#L60>)
- [执行闭环 `_execute`](<../apps/api/services/task_service.py#L74>)
- [状态推进 `_transition`](<../apps/api/services/task_service.py#L253>)
- [事件映射 `_apply_progress_event`](<../apps/api/services/task_service.py#L292>)

### 这里最重要的一点

真正的任务闭环不在前端，也不在 LLM，而是在 `TaskService` 里把整个过程串起来。

## 8. Agent 的“脑子”在哪里

[ `OpenAIPlanner`](<../packages/agent-core/src/agent_core/llm.py#L65>) 负责把任务状态发给模型，再把模型输出解析成结构化结果。

它主要做三件事：

1. 规划补丁
2. 规划下一步
3. 审查结果

### 关键代码

- [补丁提议 `propose_patch`](<../packages/agent-core/src/agent_core/llm.py#L73>)
- [下一步规划 `plan_next_step`](<../packages/agent-core/src/agent_core/llm.py#L137>)
- [结果审查 `review_result`](<../packages/agent-core/src/agent_core/llm.py#L189>)

## 9. 真正的工作流在哪里

[ `DemoWorkflow`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L64>) 是主工作流。

它做的是：

1. 检索相关代码
2. 读取上下文文件
3. 跑 baseline 测试
4. 进入协作循环
5. 汇总结果

### 关键代码

- [工作流入口 `run`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L81>)
- [协作循环 `_run_collaborative_loop`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L335>)
- [状态构造 `_build_react_state`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L941>)
- [分支比较 `_build_branch_comparison`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L984>)
- [测试执行 `_run_tests`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L1233>)

## 10. 多 Agent 协作现在怎么做

[ `choose_parallel_branch`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L33>) 现在每轮会同时生成 5 个候选角色：

- `planner`：平衡型，倾向最小安全修改
- `critic`：保守型，宁可多读上下文
- `memory`：记忆型，参考历史经验
- `explorer`：扩展搜索范围
- `verifier`：验证型，优先看测试和收尾

然后系统会：

1. 并行生成候选
2. 给每个候选打分
3. 选出最优分支
4. 写入 `branch.selected`
5. 最终写出 `branch.comparison.completed`

### 关键代码

- [并行入口 `choose_parallel_branch`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L33>)
- [分支构建 `_build_candidate`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L154>)
- [分支回退 `_fallback_branch_decision`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L265>)
- [分支打分 `_score_candidate`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L408>)

## 11. 分支比较结果是什么

现在不仅有事件流，还有一个正式的比较摘要 artifact。

在 [pipeline.py](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L656>) 里，工作流会把 branch 历史整理成一份结构化结果，然后发出：

- `branch.comparison.completed`

同时在 [TaskService](<../apps/api/services/task_service.py#L184>) 里保存成：

- `type = branch_comparison`

这意味着前端不仅能看到“谁赢了”，还能看到：

- 总共比较了几轮
- 一共多少候选
- 最后谁赢了
- 最终测试结果
- 高亮结论

## 12. 回滚怎么做

[ `RollbackService`](<../apps/api/services/rollback_service.py#L14>) 负责：

1. 找最近快照
2. 验证快照是否未被篡改
3. 恢复工作区
4. 更新任务状态
5. 发出回滚事件

### 关键代码

- [回滚入口 `rollback`](<../apps/api/services/rollback_service.py#L19>)

## 13. 事件怎么推到前端

[ `EventService`](<../apps/api/services/event_service.py#L14>) 做两件事：

1. 把事件写入数据库
2. 把事件实时广播给前端

[ `events` 路由](<../apps/api/api/routes/events.py#L9>) 提供 SSE，前端可以边跑边看。

## 14. 数据怎么存

[ `SQLiteStore`](<../apps/api/storage/sqlite.py#L95>) 负责建表和 CRUD。

它会保存：

- tasks
- task_events
- task_snapshots
- task_artifacts
- task_memory
- task_subgoals

### 关键代码

- [初始化数据库 `initialize`](<../apps/api/storage/sqlite.py#L124>)
- [创建任务 `create_task`](<../apps/api/storage/sqlite.py#L131>)
- [任务详情 `get_task_detail`](<../apps/api/storage/sqlite.py#L183>)
- [写入事件 `append_event`](<../apps/api/storage/sqlite.py#L227>)
- [保存快照 `create_snapshot`](<../apps/api/storage/sqlite.py#L262>)
- [保存产物 `create_artifact`](<../apps/api/storage/sqlite.py#L272>)
- [保存记忆 `create_memory`](<../apps/api/storage/sqlite.py#L300>)
- [搜索记忆 `search_memory`](<../apps/api/storage/sqlite.py#L376>)

## 15. 前端怎么显示这些东西

[ `TaskDetailPanel`](<../apps/web/src/components/TaskDetail.tsx#L140>) 是页面总控。

它会：

1. 加载任务详情
2. 加载 artifacts
3. 拉安全策略
4. 订阅 SSE
5. 有新事件就刷新界面

### 关键代码

- [事件合并 `applyEvent`](<../apps/web/src/components/TaskDetail.tsx#L44>)
- [总控组件 `TaskDetailPanel`](<../apps/web/src/components/TaskDetail.tsx#L140>)

[ `BranchComparisonPanel`](<../apps/web/src/components/BranchComparisonPanel.tsx#L333>) 是第五阶段最关键的可视化。

它会把每轮候选分支、选中分支、比较摘要一起展示出来。

[ `CollaborationPanel`](<../apps/web/src/components/CollaborationPanel.tsx#L32>) 会把 branch comparison 也纳入协作事件流。

[ `ArtifactPanel`](<../apps/web/src/components/ArtifactPanel.tsx#L1>) 会展示：

- diff
- 测试报告
- branch comparison summary

## 16. API 接口

[ `tasks` 路由](<../apps/api/api/routes/tasks.py#L12>) 提供：

- `POST /tasks`
- `GET /tasks`
- `GET /tasks/{id}`
- `POST /tasks/{id}/run`
- `POST /tasks/{id}/rollback`
- `GET /tasks/{id}/artifacts`

[ `security` 路由](<../apps/api/api/routes/security.py#L10>) 会返回当前安全策略。

## 17. 现在这个系统能实现什么

现在它已经可以：

- 接任务
- 找代码
- 读上下文
- 调 LLM 做规划
- 并行比较多个分支
- 生成 patch
- 跑测试
- 保存 diff、测试报告和分支比较摘要
- 记录事件流
- 保存长期记忆
- 回滚到快照
- 前端实时看全过程

## 18. 小白最推荐的阅读顺序

1. [ `docs/architecture.md`](<../docs/architecture.md#L1>)
2. [ `docs/technical_guide.md`](<../docs/technical_guide.md#L1>)
3. [ `apps/api/main.py`](<../apps/api/main.py#L17>)
4. [ `apps/api/services/task_service.py`](<../apps/api/services/task_service.py#L28>)
5. [ `packages/agent-core/src/agent_core/workflow/pipeline.py`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L64>)
6. [ `packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L33>)
7. [ `apps/web/src/components/TaskDetail.tsx`](<../apps/web/src/components/TaskDetail.tsx#L140>)
8. [ `apps/web/src/components/BranchComparisonPanel.tsx`](<../apps/web/src/components/BranchComparisonPanel.tsx#L333>)

## 19. 锚点索引

如果你的编辑器不认本地文件行号链接，就直接在代码里搜索这些 `DOC_ANCHOR`：

- `task_service.create_task` -> [`apps/api/services/task_service.py`](<../apps/api/services/task_service.py#L43>)
- `task_service.execute_loop` -> [`apps/api/services/task_service.py`](<../apps/api/services/task_service.py#L75>)
- `task_service.branch_comparison_artifact` -> [`apps/api/services/task_service.py`](<../apps/api/services/task_service.py#L187>)
- `task_service.progress_mapper` -> [`apps/api/services/task_service.py`](<../apps/api/services/task_service.py#L296>)
- `task_service.transition` -> [`apps/api/services/task_service.py`](<../apps/api/services/task_service.py#L256>)
- `workflow.demo` -> [`packages/agent-core/src/agent_core/workflow/pipeline.py`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L64>)
- `workflow.run` -> [`packages/agent-core/src/agent_core/workflow/pipeline.py`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L82>)
- `workflow.collaborative_loop` -> [`packages/agent-core/src/agent_core/workflow/pipeline.py`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L337>)
- `workflow.branch_comparison` -> [`packages/agent-core/src/agent_core/workflow/pipeline.py`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L1068>)
- `workflow.run_tests` -> [`packages/agent-core/src/agent_core/workflow/pipeline.py`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L1424>)
- `parallel_branches.choose` -> [`packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L33>)
- `parallel_branches.build_candidate` -> [`packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L154>)
- `parallel_branches.fallback` -> [`packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L265>)
- `parallel_branches.score` -> [`packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L408>)
- `task_detail.apply_event` -> [`apps/web/src/components/TaskDetail.tsx`](<../apps/web/src/components/TaskDetail.tsx#L44>)
- `task_detail.panel` -> [`apps/web/src/components/TaskDetail.tsx`](<../apps/web/src/components/TaskDetail.tsx#L141>)
- `branch_comparison.build_view` -> [`apps/web/src/components/BranchComparisonPanel.tsx`](<../apps/web/src/components/BranchComparisonPanel.tsx#L147>)
- `branch_comparison.panel` -> [`apps/web/src/components/BranchComparisonPanel.tsx`](<../apps/web/src/components/BranchComparisonPanel.tsx#L333>)
- `collaboration.role_map` -> [`apps/web/src/components/CollaborationPanel.tsx`](<../apps/web/src/components/CollaborationPanel.tsx#L3>)
- `collaboration.panel` -> [`apps/web/src/components/CollaborationPanel.tsx`](<../apps/web/src/components/CollaborationPanel.tsx#L32>)
- `artifact.format` -> [`apps/web/src/components/ArtifactPanel.tsx`](<../apps/web/src/components/ArtifactPanel.tsx#L1>)
- `artifact.panel` -> [`apps/web/src/components/ArtifactPanel.tsx`](<../apps/web/src/components/ArtifactPanel.tsx#L1>)
- `tools.run_command` -> [`packages/tools/src/assistant_tools/security.py`](<../packages/tools/src/assistant_tools/security.py#L73>)
- `tools.safe_environment` -> [`packages/tools/src/assistant_tools/security.py`](<../packages/tools/src/assistant_tools/security.py#L189>)


## 关键实现说明（按当前代码）

### 任务生命周期与成功判定

[`TaskService._execute`](<../apps/api/services/task_service.py#L74>) 是后端闭环的编排入口：它先检索历史记忆，再创建 [`DemoWorkflow`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L65>)，通过内部 `emit` 回调把工作流事件统一交给 [`EventService`](<../apps/api/services/event_service.py#L19>)，同时调用 [`_apply_progress_event`](<../apps/api/services/task_service.py#L296>) 推进任务状态。

工作流完成后，服务层并不是只看测试结果，而是要求 `result.final_test.passed` 且 `result.review_decision == 'approve'`；这段双重判定位于 [`_execute`](<../apps/api/services/task_service.py#L154>)。因此“测试通过但 reviewer 不批准”仍会进入 `failed`，并保留 diff、测试报告和错误原因，便于继续追问或重新运行。

### ReAct 协作循环

[`_run_collaborative_loop`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L342>) 每轮最多执行四次决策。它先调用 [`choose_parallel_branch`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L33>) 并发生成 planner、critic、memory、explorer、verifier 五个候选；[`_score_candidate`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L408>) 综合测试结果、上下文量、历史分支和当前动作，选择得分最高的候选。

选中 `read_more` 时只扩展上下文；选中 `patch` 时先调用 [`capture_snapshot`](<../packages/tools/src/assistant_tools/security.py#L42>)，再校验路径并写入文件，随后通过 [`_run_tests`](<../packages/agent-core/src/agent_core/workflow/pipeline.py#L1424>) 验证。reviewer 返回 `revise` 或 `reject` 时，工作流使用 [`restore_snapshot`](<../packages/tools/src/assistant_tools/security.py#L58>) 撤销当前分支，再进入下一轮。

### LLM 与启发式回退

[`OpenAIPlanner`](<../packages/agent-core/src/agent_core/llm.py#L97>) 将规划、执行器工具选择、结果审查和聊天分别封装为结构化 JSON 请求。响应通过 [`_extract_json_object`](<../packages/agent-core/src/agent_core/llm.py#L774>) 解析；未配置 key、请求失败或 JSON 无效时，规划器返回 heuristic 结果，分支层则使用 [`_fallback_branch_decision`](<../packages/agent-core/src/agent_core/workflow/parallel_branches.py#L265>)，所以本地闭环不会因为 LLM 不可用而直接崩溃。

### 事件、存储与前端同步

[`EventService.emit`](<../apps/api/services/event_service.py#L19>) 先将事件写入 [`SQLiteStore.append_event`](<../apps/api/storage/sqlite.py#L239>)，再广播到订阅队列；[`EventService.stream`](<../apps/api/services/event_service.py#L54>) 先回放 `after_sequence` 之后的历史事件，再等待实时事件。前端 [`applyEvent`](<../apps/web/src/components/TaskDetail.tsx#L44>) 追加事件，并即时合并子目标、diff、检索结果和测试结果，因此页面无需反复等待完整详情接口。

### 安全边界与回滚

默认允许的命令规则见 [`DEFAULT_ALLOWED_COMMANDS`](<../apps/api/core/config.py#L13>)；[`run_command`](<../packages/tools/src/assistant_tools/security.py#L73>) 在执行前检查可执行文件和参数前缀，并限制安全环境。快照会写入 manifest 和文件哈希，恢复前由 [`validate_snapshot`](<../packages/tools/src/assistant_tools/security.py#L141>) 校验完整性；公开回滚流程见 [`RollbackService.rollback`](<../apps/api/services/rollback_service.py#L17>)。

### 当前代码边界

当前补丁模型主要支持单文件创建或已有文件的一次文本替换；五个并行分支是“决策候选”的并行比较，不是五份工作区的并行写入；任务最终成功必须同时满足测试通过和 reviewer 批准。
## 20. 最后一句话

这个项目的核心不是“会聊天”，而是“会把任务真正做完”。

Agent 的脑子在 LLM，手脚在工具，记忆在数据库，眼睛在事件流，保险丝在快照回滚。

