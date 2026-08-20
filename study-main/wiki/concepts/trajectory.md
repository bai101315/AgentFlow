---
title: Trajectory
created: 2026-06-24
type: concept
tags: [trajectory, dataset, training-data]
sources: [agent/trajectory.py, run_agent.py, agent/agent_init.py]
---

# Trajectory（对话轨迹）

Trajectory 是 agent 一次完整对话的 ShareGPT-format JSON 记录，用于模型训练
和微调。与 session log（`~/.hermes/sessions/` 下的 SQLite 数据）不同，
trajectory 是 training-ready 格式。

## 文件位置

- 保存逻辑：`agent/trajectory.py:30` — `save_trajectory()`
- 调用入口：`run_agent.py:1340` — `AIAgent._save_trajectory()`
- 写入时机：`agent/conversation_loop.py:3860` — 每次 `run_conversation()` 结束时

## 怎么开启

### 方式一：通过 config（交互式 hermes）

```bash
hermes config set agent.save_trajectories true
```

设置后重启 `hermes`。不过注意：CLI 入口 `hermes_cli/main.py` 当前没有从 config
读取此值传给 `AIAgent`，所以**交互式 hermes 里可能不生效**。

### 方式二：standalone 模式（确认生效）

```bash
cd ~/.hermes/hermes-agent
python run_agent.py --save_trajectories --query="你的问题"
```

`run_agent.py` 的 `__main__` 块直接支持 `--save_trajectories` flag。

### 方式三：代码中

```python
from run_agent import AIAgent

agent = AIAgent(
    save_trajectories=True,
    model="deepseek-v4-pro",
    ...
)
agent.chat("你的问题")
```

## 文件格式

在当前工作目录生成 JSONL 文件，每行一条对话记录：

```jsonl
{"conversations": [...], "timestamp": "2026-06-24T...", "model": "...", "completed": true}
```

### 文件命名

| 对话结果 | 文件名 |
|---------|--------|
| 成功完成 | `trajectory_samples.jsonl` |
| 未完成/出错 | `failed_trajectories.jsonl` |

可以通过 `filename` 参数自定义输出路径。

### 记录结构

```json
{
  "conversations": [
    {"role": "system", "content": "You are..."},
    {"role": "user", "content": "用户问题"},
    {"role": "assistant", "content": null, "tool_calls": [...]},
    {"role": "tool", "name": "...", "content": "..."},
    {"role": "assistant", "content": "最终回复"}
  ],
  "timestamp": "2026-06-24T15:30:00.123456",
  "model": "deepseek-v4-pro",
  "completed": true
}
```

格式是 ShareGPT `{"conversations": [...]}` 格式。reasoning 内容嵌入在
`<think>` 标签中。

## 与 session log 的区别

| | Session Log | Trajectory |
|---|---|---|
| 位置 | `~/.hermes/sessions/` | 当前工作目录 |
| 格式 | SQLite (FTS5) | JSONL |
| 用途 | 对话恢复、session_search | 模型训练/微调 |
| 默认开关 | 始终写入 | 默认关闭 |
| 推理内容 | `<think>` 内嵌 | `<think>` 内嵌 |

## 限制

- 默认关闭，需显式开启
- 交互式 `hermes` CLI 里可能不生效（入口没接 config 读取）
- 仅在 `run_conversation()` 正常结束时写入——如果进程被 kill，不保存
- 写入当前工作目录，不是固定路径。建议启动 hermes 前 `cd` 到目标目录

## 关系

- [[aiagent]] — `save_trajectories` 是 AIAgent 的一个属性
- [[conversation-loop]] — `run_conversation()` 末尾触发 `_save_trajectory()`
- [[llm-wiki-usage]] — trajectory 可以用于 LLM Wiki 的 ingest
