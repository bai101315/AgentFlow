作用：```把你和 agent 的历史对话存进 SQLite 全文索引里。以后你问“上次我们怎么决定的？”、“之前那个 bug 怎么修的？”时，agent 可以搜索过去会话，而不是靠模型瞎猜。```
存储内容：AIMessage, UserMessage, <think>

# 触发条件
1. config配置需要开启
2. 每轮 agent 回复结束后，自动索引当前对话
   SessionSearchMiddleware中间件执行操作，它会把本轮“用户消息 + 最终 assistant 回复”写进搜索库。
3. 当用户问上下文时，agent会被prompt 引导去调用```session_search```工具。

# 执行操作

## 写入时
```
agent 完成一轮对话
SessionSearchMiddleware.after_agent 被调用
提取消息
写入 SQLite 数据库
同时写入 FTS5 全文索引
```

```
SessionSearchMiddleware.after_agent
-> index_messages(...)
-> 写入 session_search.db
```
这个过程会增加一点点时间，但很小，因为只是本地 SQLite 写入。当前实现还可以用后台线程做，不会明显阻塞主回复。



## 查询时

全局搜索对话： ```session_search(query="测试怎么跑")```
浏览最近会话： ```session_search()```
查看某条命中消息附近的上下文： ```session_search(session_id="xxx", around_message_id="yyy")```

```
prompt 会提示 agent 先调用：
session_search(query="Alpha 项目 决策")
```
然后 agent 根据搜索结果回答。这个会增加一次工具调用时间，但也是本地 SQLite 搜索，通常很快。


# 产生效果
1. agent可以跨会话找回上下文
2. agent 可以搜索历史，找到相关对话


# 一些问题
## FTS5 全文索引是什么？
看不懂，是内置的东西

```
FTS5 会把文本拆成可搜索的词，建立一个“倒排索引”。你可以粗略理解成一本书最后的索引页

SQLite -> 出现在第 3、8、20 条消息
测试 -> 出现在第 5、9 条消息
PYTHONPATH -> 出现在第 7 条消息

它不用一条条扫描所有聊天记录，而是直接从索引里找相关消息。
```
FTS5的作用是：```让历史会话可以被关键词快速搜索```

## 功能上是否和 checkpoint 重复？

checkpoint 是 LangGraph 的运行状态保存。主要用于
```
恢复某个 thread 的 agent 状态
继续对话
保存 messages/state
```
session_search: ```把对话内容抽出来，做全文检索```

checkpoint 里可能也有 messages，但它不适合直接做“跨所有会话搜索”：

```
checkpoint 的结构是给 LangGraph 恢复状态用的，不是给搜索用的
checkpoint 不一定方便跨 thread 查关键词
checkpoint 可能包含大量中间状态、工具消息、内部状态
session_search 会过滤，只保留用户消息和最终回复，更适合召回
```

## 当前记忆会无限增长？
已有：
```
插入
去重
搜索
最近会话浏览
around 滚动查看上下文
```

缺乏：
```
按时间删除旧记录
按 thread 删除
更新已存在消息
压缩旧会话
设置最大数据库大小
隐私清理
```

## 是否会增加时间和浪费？
因为是本地SQLite 写入和搜索，写入甚至还可以会后台线程做，时间会很短；真正风险是： agent 误判

目前 只在 prompt 已经写了类似规则，
