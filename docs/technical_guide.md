# 研发助手 Agent 系统技术文档

这份文档按“先看系统，再看文件，再看关键代码”的方式写。目标是让第一次接触这个项目的人，也能大概看懂它是怎么工作的。

下面这些都是本地文件链接，在 Codex 或 VS Code 这类支持跳转的编辑器里可以直接点开。

## 1. 这个项目在做什么

它不是一个普通聊天框，而是一个“会干活的研发助手”:

1. 接收任务
2. 找相关代码
3. 读代码上下文
4. 让 Agent 先想方案，再决定改哪一块
5. 应用补丁
6. 跑测试
7. 根据结果继续修复或结束
8. 记录事件、产物、记忆，并支持回滚

你可以把它理解成一个“能自己查代码、改代码、跑测试、看结果”的小型研发流水线。

## 2. 系统结构一眼看懂

### 后端

- [`apps/api/main.py`](<../apps/api/main.py#17>)：FastAPI 入口，负责把所有路由、服务、存储系统组装起来。
- [`apps/api/services/task_service.py`](<../apps/api/services/task_service.py#28>)：任务执行中枢，真正把“创建任务 -> 跑任务 -> 存事件 -> 存产物”串起来。
- [`apps/api/services/event_service.py`](<D:/code_helper/apps/api/services/event_service.py:14>)：负责把事件写入数据库，并实时推送给前端。
- [`apps/api/services/rollback_service.py`](<D:/code_helper/apps/api/services/rollback_service.py:14>)：负责回滚，把代码恢复到快照前状态。
- [`apps/api/storage/sqlite.py`](<D:/code_helper/apps/api/storage/sqlite.py:95>)：SQLite 数据层，保存任务、事件、快照、产物、记忆。

### Agent 层

- [`packages/agent-core/src/agent_core/workflow/pipeline.py`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:59>)：主工作流。它定义了 Agent 怎么读、怎么想、怎么改、怎么测。
- [`packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/parallel_branches.py:1>)：并行多 Agent 协作。每轮同时让多个“角色”出方案，再选出最优分支。
- [`packages/agent-core/src/agent_core/llm.py`](<D:/code_helper/packages/agent-core/src/agent_core/llm.py:44>)：LLM 调用封装。负责把任务状态发给模型，并解析模型返回的 JSON。

### 前端

- [`apps/web/src/components/TaskDetail.tsx`](<D:/code_helper/apps/web/src/components/TaskDetail.tsx:81>)：任务详情页的总控，拉任务详情、订阅 SSE、更新界面。
- [`apps/web/src/components/BranchComparisonPanel.tsx`](<D:/code_helper/apps/web/src/components/BranchComparisonPanel.tsx:294>)：分支对比面板，展示每一轮的多个候选分支和最终选中分支。
- [`apps/web/src/components/CollaborationPanel.tsx`](<D:/code_helper/apps/web/src/components/CollaborationPanel.tsx:32>)：协作事件流，展示 planner / executor / reviewer 的交接过程。
- [`apps/web/src/components/TaskTimeline.tsx`](<D:/code_helper/apps/web/src/components/TaskTimeline.tsx:30>)：完整事件时间线。
- [`apps/web/src/components/LogPanel.tsx`](<D:/code_helper/apps/web/src/components/LogPanel.tsx:3>)：测试输出、错误日志。
- [`apps/web/src/api/client.ts`](<D:/code_helper/apps/web/src/api/client.ts:3>)：前端调用后端 API 的统一入口。

## 3. 关键数据对象是什么

这些对象决定了系统里“任务是什么、事件是什么、产物是什么”。

### `Task`

任务本体，包含:

- 标题、描述
- 仓库路径
- 当前状态
- 当前步骤
- 最新 diff
- 最新测试结果
- 最新检索结果
- 任务总结

### `Step`

表示任务运行到哪一步了:

- `read`：读代码
- `analyze`：分析和规划
- `patch`：写补丁
- `test`：跑测试
- `review`：审查结果
- `summarize`：生成总结
- `rollback`：回滚

### `Event`

事件就是“过程记录”。比如:

- 读了哪个文件
- 哪个 Agent 开始工作了
- 生成了哪个分支
- 应用了哪个 patch
- 测试是否通过

### `Artifact`

产物，比如:

- diff
- 测试报告
- 日志
- 截图

### `Snapshot`

快照就是“改代码前的备份”。回滚的时候会恢复它。

### `Memory`

长期记忆，记录历史任务的结论、修复经验、相关文件。

## 4. 配置文件在干什么

真正的大模型配置在:

- [`config/llm_config.json`](<D:/code_helper/config/llm_config.json:1>)

示例内容:

```json
{
  "provider": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "OPENAI_API_KEY",
  "model": "gpt-5.1",
  "temperature": 0.2,
  "max_output_tokens": 2048,
  "timeout_seconds": 120
}
```

如果这里没填好，系统仍然能跑，但会退回到启发式逻辑，不走真正的 LLM 推理。

## 5. 后端入口文件做什么

[`apps/api/main.py`](<D:/code_helper/apps/api/main.py:17>)

它做了几件事:

1. 读取配置
2. 创建 FastAPI 应用
3. 加 CORS
4. 启动时初始化数据库、事件服务、回滚服务、任务服务
5. 注册路由

关键逻辑:

```python
settings = get_settings()
store = SQLiteStore(settings.database_path)
events = EventService(store)
rollback_service = RollbackService(store, events)
task_service = TaskService(...)
```

这段代码的意思是:

- 先准备数据库
- 再准备事件系统
- 再准备回滚能力
- 最后把这些能力交给任务服务统一调度

## 6. 任务是怎么跑起来的

[`apps/api/services/task_service.py`](<D:/code_helper/apps/api/services/task_service.py:28>)

这是整个系统最核心的后端文件。

它负责:

1. 创建任务
2. 运行任务
3. 把任务状态推进到下一步
4. 把 Agent 过程记录进数据库
5. 保存 diff、测试报告、记忆
6. 遇到失败时把任务标成失败

### 关键代码链接

- [任务服务类 `TaskService`](<D:/code_helper/apps/api/services/task_service.py:28>)
- [创建任务 `create_task`](<D:/code_helper/apps/api/services/task_service.py:43>)
- [运行入口 `run_task`](<D:/code_helper/apps/api/services/task_service.py:60>)
- [执行闭环 `_execute`](<D:/code_helper/apps/api/services/task_service.py:74>)
- [状态推进 `_transition`](<D:/code_helper/apps/api/services/task_service.py:240>)
- [进度映射 `_apply_progress_event`](<D:/code_helper/apps/api/services/task_service.py:279>)

### 它为什么重要

因为真正的“任务闭环”不在前端，也不在 LLM，而在这里。

### 关键流程

#### 1）创建任务

`create_task()` 会把标题、描述、仓库路径写进数据库，生成一个新的任务记录。

#### 2）运行任务

`run_task()` 会:

- 检查任务是否已经在跑
- 把任务状态改成 `queued`
- 异步启动 `_execute()`

这意味着 API 返回很快，但真正干活是在后台线程里继续跑。

#### 3）执行任务

`_execute()` 做的是完整闭环:

- 先加载历史记忆
- 再创建 `DemoWorkflow`
- 让 workflow 跑起来
- 保存 snapshot、diff、test report
- 写入 memory
- 根据测试和审查结果决定 `succeeded` 还是 `failed`

## 7. Agent 的“脑子”在哪里

[`packages/agent-core/src/agent_core/llm.py`](<D:/code_helper/packages/agent-core/src/agent_core/llm.py:44>)

这里是 LLM 接口层。

它主要做三件事:

1. 发起模型请求
2. 让模型按 JSON 格式输出
3. 把模型回复解析成结构化对象

### 关键代码链接

- [LLM 主类 `OpenAIPlanner`](<D:/code_helper/packages/agent-core/src/agent_core/llm.py:44>)
- [补丁提议 `propose_patch`](<D:/code_helper/packages/agent-core/src/agent_core/llm.py:73>)
- [下一步规划 `plan_next_step`](<D:/code_helper/packages/agent-core/src/agent_core/llm.py:137>)
- [结果审查 `review_result`](<D:/code_helper/packages/agent-core/src/agent_core/llm.py:189>)
- [Responses API 调用 `_invoke_responses_api`](<D:/code_helper/packages/agent-core/src/agent_core/llm.py:230>)

## 8. 真正的工作流在哪里

[`packages/agent-core/src/agent_core/workflow/pipeline.py`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:59>)

这个文件是 Agent 的主循环。

它把一个任务拆成这些阶段:

1. 检索相关代码
2. 读取上下文文件
3. 跑 baseline 测试
4. 进入协作循环
5. 生成总结

### 关键代码链接

- [工作流主类 `DemoWorkflow`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:59>)
- [工作流入口 `run`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:76>)
- [协作循环 `_run_collaborative_loop`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:193>)
- [LLM 规划 `_plan_react_decision`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:528>)
- [LLM 审查 `_review_react_decision`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:612>)
- [状态构造 `_build_react_state`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:782>)
- [分支计数 `_count_explored_branches`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:815>)
- [文件读取 `_read_requested_files`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:825>)
- [patch 解析 `_resolve_react_proposal`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:837>)
- [测试执行 `_run_tests`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:967>)

## 9. 多 Agent 并行协作是怎么实现的

[`packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/parallel_branches.py:1>)

这部分实现了“同一轮里多个角色同时提方案”。

当前有 3 个角色:

- `planner`：偏平衡，负责小而稳的修复
- `critic`：偏保守，宁可多看一点上下文
- `memory`：会参考历史记忆和已有经验

### 关键代码链接

- [并行入口 `choose_parallel_branch`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/parallel_branches.py:33>)
- [分支生成 `_build_candidate`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/parallel_branches.py:143>)
- [分支回退 `_fallback_branch_decision`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/parallel_branches.py:253>)
- [分支打分 `_score_candidate`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/parallel_branches.py:329>)

## 10. 回滚是怎么做的

[`apps/api/services/rollback_service.py`](<D:/code_helper/apps/api/services/rollback_service.py:14>)

它的职责很简单:

1. 找到最近快照
2. 发出 `rollback.started`
3. 把工作区恢复到快照
4. 更新任务状态为 `rolled_back`
5. 发出 `rollback.completed`

### 关键代码链接

- [回滚服务类 `RollbackService`](<D:/code_helper/apps/api/services/rollback_service.py:14>)
- [回滚入口 `rollback`](<D:/code_helper/apps/api/services/rollback_service.py:19>)

## 11. 事件是怎么实时推到前端的

[`apps/api/services/event_service.py`](<D:/code_helper/apps/api/services/event_service.py:14>)

它做两件事:

1. 把事件写入数据库
2. 把事件实时广播给正在监听的前端

[`apps/api/api/routes/events.py`](<D:/code_helper/apps/api/api/routes/events.py:9>)

这个路由提供 SSE:

- 前端可以订阅任务事件流
- 后端会把历史事件先发一遍
- 然后继续推实时事件

## 12. 数据是怎么存的

[`apps/api/storage/sqlite.py`](<D:/code_helper/apps/api/storage/sqlite.py:95>)

这是数据库仓库层。

它负责:

- 建表
- 插入任务
- 插入事件
- 插入快照
- 插入产物
- 插入记忆
- 查任务详情
- 查事件
- 查最新快照

### 关键代码链接

- [SQLite 仓库 `SQLiteStore`](<D:/code_helper/apps/api/storage/sqlite.py:95>)
- [初始化数据库 `initialize`](<D:/code_helper/apps/api/storage/sqlite.py:100>)
- [创建任务 `create_task`](<D:/code_helper/apps/api/storage/sqlite.py:107>)
- [写入事件 `append_event`](<D:/code_helper/apps/api/storage/sqlite.py:198>)
- [保存快照 `create_snapshot`](<D:/code_helper/apps/api/storage/sqlite.py:234>)
- [保存产物 `create_artifact`](<D:/code_helper/apps/api/storage/sqlite.py:244>)
- [保存记忆 `create_memory`](<D:/code_helper/apps/api/storage/sqlite.py:280>)
- [搜索记忆 `search_memory`](<D:/code_helper/apps/api/storage/sqlite.py:333>)
- [列出快照 `list_snapshots`](<D:/code_helper/apps/api/storage/sqlite.py:345>)
- [最新快照 `get_latest_snapshot`](<D:/code_helper/apps/api/storage/sqlite.py:353>)

## 13. 前端是怎么显示这些东西的

[`apps/web/src/api/client.ts`](<D:/code_helper/apps/web/src/api/client.ts:3>)

这是前端访问后端的统一入口。

它封装了:

- 列表任务
- 看任务详情
- 创建任务
- 运行任务
- 回滚任务
- 看 artifacts
- 订阅事件流

[`apps/web/src/components/TaskDetail.tsx`](<D:/code_helper/apps/web/src/components/TaskDetail.tsx:81>)

这是页面总控。

它会:

1. 加载任务详情
2. 加载 artifacts
3. 读取安全策略
4. 订阅 SSE
5. 来新事件就更新界面

### 关键代码链接

- [任务详情页总控 `TaskDetailPanel`](<D:/code_helper/apps/web/src/components/TaskDetail.tsx:81>)
- [事件同步 `applyEvent`](<D:/code_helper/apps/web/src/components/TaskDetail.tsx:19>)

[`apps/web/src/components/BranchComparisonPanel.tsx`](<D:/code_helper/apps/web/src/components/BranchComparisonPanel.tsx:294>)

这是并行多 Agent 的可视化核心。

它会按 turn 分组，把每轮的多个候选分支放在一起，显示:

- 谁提出了方案
- 方案动作是什么
- 要读哪些文件
- 改了哪些文件
- 测试结果
- 审查结果
- 哪个分支被选中

### 关键代码链接

- [分支对比 `BranchComparisonPanel`](<D:/code_helper/apps/web/src/components/BranchComparisonPanel.tsx:294>)
- [分支分组 `buildTurns`](<D:/code_helper/apps/web/src/components/BranchComparisonPanel.tsx:128>)
- [分支差异 `describeDelta`](<D:/code_helper/apps/web/src/components/BranchComparisonPanel.tsx:248>)

[`apps/web/src/components/CollaborationPanel.tsx`](<D:/code_helper/apps/web/src/components/CollaborationPanel.tsx:32>)

把和 Agent 协作相关的事件聚合起来，比如:

- planner 开始了
- executor 开始了
- reviewer 完成了
- branch 被选中或被回滚了

### 关键代码链接

- [协作面板 `CollaborationPanel`](<D:/code_helper/apps/web/src/components/CollaborationPanel.tsx:32>)
- [角色映射 `getRole`](<D:/code_helper/apps/web/src/components/CollaborationPanel.tsx:3>)

[`apps/web/src/components/TaskTimeline.tsx`](<D:/code_helper/apps/web/src/components/TaskTimeline.tsx:30>)

把所有事件按时间列出来，像“流水账”一样展示整个 Agent 过程。

### 关键代码链接

- [时间线 `TaskTimeline`](<D:/code_helper/apps/web/src/components/TaskTimeline.tsx:30>)
- [事件摘要 `summarizePayload`](<D:/code_helper/apps/web/src/components/TaskTimeline.tsx:3>)

[`apps/web/src/components/LogPanel.tsx`](<D:/code_helper/apps/web/src/components/LogPanel.tsx:3>)

显示最后一次测试的:

- 命令
- 成功失败
- 退出码
- stdout
- stderr

## 14. 路由接口在做什么

[`apps/api/api/routes/tasks.py`](<D:/code_helper/apps/api/api/routes/tasks.py:12>)

提供这些接口:

- `POST /tasks`
- `GET /tasks`
- `GET /tasks/{id}`
- `POST /tasks/{id}/run`
- `POST /tasks/{id}/rollback`
- `GET /tasks/{id}/artifacts`

它只是“转发入口”，真正逻辑在 `TaskService`。

### 关键代码链接

- [任务路由 `tasks`](<D:/code_helper/apps/api/api/routes/tasks.py:12>)
- [创建任务接口](<D:/code_helper/apps/api/api/routes/tasks.py:15>)
- [任务列表接口](<D:/code_helper/apps/api/api/routes/tasks.py:20>)
- [运行任务接口](<D:/code_helper/apps/api/api/routes/tasks.py:33>)
- [回滚任务接口](<D:/code_helper/apps/api/api/routes/tasks.py:41>)
- [产物列表接口](<D:/code_helper/apps/api/api/routes/tasks.py:51>)

[`apps/api/api/routes/events.py`](<D:/code_helper/apps/api/api/routes/events.py:9>)

提供任务事件流 SSE。

[`apps/api/api/routes/security.py`](<D:/code_helper/apps/api/api/routes/security.py:10>)

返回当前安全策略，比如:

- 工作区路径
- 快照路径
- 命令白名单

## 15. 一次完整任务的运行顺序

如果你点一次“Run task”，大致会发生:

1. API 收到请求
2. TaskService 把任务标记为运行中
3. workflow 先检索相关文件
4. workflow 读取上下文
5. 跑 baseline 测试
6. 并行发起多个 Planner 分支
7. 选中一个最优分支
8. 执行 patch
9. 再跑测试
10. reviewer 判断
11. 成功则保存总结和记忆
12. 失败则标记失败，必要时可回滚

## 16. 现在这个系统能做什么

现在它已经能:

- 读任务
- 找代码
- 读上下文
- 调 LLM 做规划
- 并行比较多个分支
- 应用补丁
- 跑测试
- 生成 diff 和测试报告
- 记录事件流
- 回滚到快照
- 前端实时展示协作过程

## 17. 给新手的阅读顺序

如果你想按最省力的方式看代码，建议顺序是:

1. [`docs/architecture.md`](<D:/code_helper/docs/architecture.md:1>)
2. [`docs/technical_guide.md`](<D:/code_helper/docs/technical_guide.md:1>)
3. [`apps/api/main.py`](<D:/code_helper/apps/api/main.py:17>)
4. [`apps/api/services/task_service.py`](<D:/code_helper/apps/api/services/task_service.py:28>)
5. [`packages/agent-core/src/agent_core/workflow/pipeline.py`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/pipeline.py:59>)
6. [`packages/agent-core/src/agent_core/workflow/parallel_branches.py`](<D:/code_helper/packages/agent-core/src/agent_core/workflow/parallel_branches.py:33>)
7. [`apps/web/src/components/TaskDetail.tsx`](<D:/code_helper/apps/web/src/components/TaskDetail.tsx:81>)
8. [`apps/web/src/components/BranchComparisonPanel.tsx`](<D:/code_helper/apps/web/src/components/BranchComparisonPanel.tsx:294>)

## 18. 最后一句话

这个项目的核心，不是“会聊天”，而是“会把任务真正做完”。
Agent 的脑子在 LLM，手脚在工具，记忆在数据库，眼睛在事件流，保险丝在快照回滚。
