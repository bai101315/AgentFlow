# 会话
Hermes Agent会自动将每次对话保存为一个会话。会话功能支持对话恢复、跨会话搜索以及完整的对话历史管理。

## 两种系统
- SQLite 数据库（~/.hermes/state.db）—— 使用 FTS5 全文搜索的结构化会话元数据
- JSONL 转录文件（~/.hermes/sessions/）—— 包含工具调用（网关）的原始对话转录

甚至可以通过CLI会话恢复，

## 压缩会话上下文
/compress, Hermes会创建一个新的延续会话

## 会话搜索
Agent内置了 session_search 工具，使用 SQLite 的 FTS5 引擎对所有历史对话执行全文搜索。

工作原理
- FTS5 搜索匹配的消息，并按相关性排序
- 按会话分组，选取前 N 个唯一会话（默认为 3 个）
- 加载每个会话的对话内容，截取约 100K 字符，以匹配内容为中心
- 发送到快速摘要模型，生成聚焦摘要
- 返回每个会话的摘要，附带元数据和上下文信息

## 会话过期与清理
### 自动清理
- 网关会话根据配置的重置策略自动重置
- 重置前会保存即将过期会话的记忆和技能
- 结束的会话会保留在数据库中，直到被清理

### 手动清理

```python
# 修剪 90 天以上的 sessions
hermes sessions prune

# 删除特定的session
hermes sessions delete <session_id>

# 修剪前导出（备份）
hermes sessions export backup.jsonl
hermes sessions prune --older-than 30 --yes
```

数据库增长缓慢（通常：数百个会话仅占 10-15 MB）。清理主要适用于删除不再需要用于搜索召回的旧对话。
