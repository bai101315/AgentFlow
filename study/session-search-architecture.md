# Session Search 完整架构参考文档

> 基于 Hermes Agent 源码 (`hermes_state.py` + `tools/session_search_tool.py`)，适用于复用。
> 文件: `/home/bai/session-search-architecture.md`

---

## 1. 整体架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    Agent / 用户请求                       │
│  session_search(query=...) / scroll / browse            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│             tools/session_search_tool.py                 │
│  四个模式：DISCOVERY / SCROLL / READ / BROWSE            │
│  - 去重 (lineage dedup)                                  │
│  - 排序 (交互 > cron)                                    │
│  - bookend 组装 (头3 + 尾3)                              │
│  - scroll 翻页 + lineage rebind                         │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│          hermes_state.py — SessionDB                     │
│  - search_messages()      (FTS5 全文搜索)                │
│  - get_anchored_view()    (锚点窗口 + bookends)          │
│  - get_messages_around()  (原始窗口查询)                  │
│  - list_sessions_rich()   (最近 session 列表)            │
│  - _resolve_to_parent()   (lineage 回根)                 │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              SQLite 数据库 (~/.hermes/state.db)           │
│  WAL 模式 | 触发器维护 FTS5 索引 | 自动修复               │
│                                                         │
│  ┌─ sessions          (session 元数据)                   │
│  ├─ messages          (消息记录, active 软删除)           │
│  ├─ state_meta        (k/v 配置, 如 last_auto_prune)     │
│  ├─ compression_locks (压缩并发锁)                       │
│  ├─ messages_fts      (FTS5 索引, unicode61 tokenizer)   │
│  └─ messages_fts_trigram (FTS5 索引, trigram tokenizer)  │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 数据库 Schema

### 2.1 核心表

```sql
-- ============================================
-- sessions 表
-- ============================================
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                    -- 唯一 ID: YYYYMMDD_HHMMSS_xxxxxxxx
    source TEXT NOT NULL,                   -- 来源: cli, telegram, discord, cron, subagent, ...
    user_id TEXT,                           -- 平台用户 ID
    session_key TEXT,                       -- 路由 key (平台+用户+chat组合)
    chat_id TEXT, chat_type TEXT, thread_id TEXT,  -- gateway 路由
    model TEXT,                             -- 模型名
    model_config TEXT,                      -- 模型配置 JSON
    system_prompt TEXT,                     -- 缓存的 system prompt
    parent_session_id TEXT,                 -- 父 session (compression/分支/delegate 产物)
    started_at REAL NOT NULL,               -- 启动时间戳
    ended_at REAL,                          -- 结束时间戳 (NULL = 活跃)
    end_reason TEXT,                        -- 结束原因: compression, branched, ...
    message_count INTEGER DEFAULT 0,        -- 消息计数
    tool_call_count INTEGER DEFAULT 0,      -- 工具调用计数
    input_tokens INTEGER DEFAULT 0,         -- token 统计
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT, git_branch TEXT, git_repo_root TEXT,  -- 工作目录/git 上下文
    title TEXT,                             -- 用户命名或自动标题 (唯一, max 100chars)
    rewind_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,    -- 软归档: 0=活跃, 1=归档
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);

-- ============================================
-- messages 表
-- ============================================
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,                     -- user, assistant, system, tool
    content TEXT,                           -- 消息文本; 多模态用 "\x00json:" 前缀 + JSON
    tool_call_id TEXT,                      -- 工具调用 ID
    tool_calls TEXT,                        -- 工具调用 JSON
    tool_name TEXT,                         -- 工具函数名
    timestamp REAL NOT NULL,                -- 写入时间
    token_count INTEGER,                    -- token 计数
    finish_reason TEXT,                     -- 完成原因
    reasoning TEXT,                         -- 推理内容
    reasoning_content TEXT,                 -- 推理原始内容
    reasoning_details TEXT,                 -- 推理细节 JSON
    platform_message_id TEXT,              -- 外部平台消息 ID (Telegram update_id 等)
    observed INTEGER DEFAULT 0,            -- 已处理标记
    active INTEGER NOT NULL DEFAULT 1,      -- ★ 软删除标记: 0=undone, 1=活跃
    compacted INTEGER NOT NULL DEFAULT 0    -- ★ 压缩历史保留: 0=正常, 1=压缩前历史
);

-- ============================================
-- 辅助表
-- ============================================
CREATE TABLE IF NOT EXISTS state_meta (
    key TEXT PRIMARY KEY,
    value TEXT                              -- 如 last_auto_prune 时间戳
);

CREATE TABLE IF NOT EXISTS compression_locks (
    session_id TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
```

### 2.2 索引

```sql
-- 基础索引
CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_source_id ON sessions(source, id);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_compression_locks_expires ON compression_locks(expires_at);

-- 延迟索引 (依赖后续列，在 _reconcile_columns() 后创建)
CREATE INDEX IF NOT EXISTS idx_messages_session_active
    ON messages(session_id, active, timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_session_key
    ON sessions(session_key, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_gateway_peer
    ON sessions(source, user_id, chat_id, chat_type, thread_id, started_at DESC);
```

### 2.3 FTS5 全文索引

```sql
-- ============================================
-- 主 FTS5 索引 (unicode61 tokenizer，适合英文/拉丁文字)
-- ============================================
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content);

-- INSERT 触发器: 新消息写入 → FTS 索引自动更新
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' ||
        COALESCE(new.tool_name, '') || ' ' ||
        COALESCE(new.tool_calls, '')
    );
END;

-- DELETE 触发器: 消息删除 → FTS 索引同步删除
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;

-- UPDATE 触发器: 消息更新 → FTS 索引同步更新
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
    INSERT INTO messages_fts(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' ||
        COALESCE(new.tool_name, '') || ' ' ||
        COALESCE(new.tool_calls, '')
    );
END;

-- ============================================
-- Trigram FTS5 索引 (专门处理 CJK 中日韩文子串搜索)
-- 问题: unicode61 把 "大别山" 拆成 "大 AND 别 AND 山" → false positives
-- 解决: trigram tokenizer 生成重叠 3-gram 序列 → 真正子串匹配
-- ============================================
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(
    content,
    tokenize='trigram'
);

-- 对应的 INSERT/DELETE/UPDATE 触发器 (结构与主 FTS 相同)
CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts_trigram(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' ||
        COALESCE(new.tool_name, '') || ' ' ||
        COALESCE(new.tool_calls, '')
    );
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update AFTER UPDATE ON messages BEGIN
    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
    INSERT INTO messages_fts_trigram(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '') || ' ' ||
        COALESCE(new.tool_name, '') || ' ' ||
        COALESCE(new.tool_calls, '')
    );
END;
```

### 2.4 索引内容说明

FTS 索引的 `content` 字段 = `content || ' ' || tool_name || ' ' || tool_calls`，即同时索引消息正文、工具名称和工具调用参数。这样搜索 "git push" 时能匹配到调用了 `terminal("git push")` 的消息。

---

## 3. 数据库初始化与维护

### 3.1 SessionDB 初始化 (`__init__`)

```python
class SessionDB:
    _WRITE_MAX_RETRIES = 15       # 写锁重试
    _WRITE_RETRY_MIN_S = 0.020    # 20ms 最小等待
    _WRITE_RETRY_MAX_S = 0.150    # 150ms 最大等待 (随机 jitter 避免 convoy)
    _CHECKPOINT_EVERY_N_WRITES = 50  # 每 50 次写入触发 WAL checkpoint

    def __init__(self, db_path, read_only=False):
        # 1. 连接 SQLite，WAL 模式 (NFS 回退到 DELETE)
        # 2. PRAGMA foreign_keys = ON
        # 3. _init_schema() → 建表/列补全/FTS设置
        # 4. 自动修复 malformed schema
```

### 3.2 写操作重试机制

```python
def _execute_write(self, fn):
    """带随机 jitter 的重试写入，突破 convoy effect"""
    for attempt in range(self._WRITE_MAX_RETRIES + 1):
        try:
            with self._lock:
                # BEGIN IMMEDIATE 防止 defered → 死锁
                self._conn.execute("BEGIN IMMEDIATE")
                result = fn(self._conn)
                self._conn.execute("COMMIT")
                self._write_count += 1
                # 每 N 次写入后 PASSIVE checkpoint (不锁读)
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                return result
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < self._WRITE_MAX_RETRIES:
                time.sleep(random.uniform(self._WRITE_RETRY_MIN_S, self._WRITE_RETRY_MAX_S))
                continue
            raise
```

**设计要点**: 不用 SQLite 内置 busy handler 的确定性等待，而是在应用层随机 jitter，天然打散竞态写入者。

### 3.3 WAL 兼容性

```python
def apply_wal_with_fallback(conn, db_label):
    # 1. 先读当前 journal_mode (probe)
    # 2. 尝试设置 WAL
    # 3. 如果失败且错误是 "locking protocol" → NFS/SMB/FUSE → 回退 DELETE
    # 4. 如果已经有其他连接设为 WAL → 不降级，抛异常
    # macOS: PRAGMA checkpoint_fullfsync=1 防掉电损坏
```

### 3.4 自动修复 (malformed schema)

当数据库的 `sqlite_master` 出现重复对象定义等损坏时:

```python
def repair_state_db_schema(db_path):
    # 策略 1: 重建 FTS 索引 (REBUILD)
    # 策略 2: 去重 sqlite_master 行
    # 策略 3: 删掉所有 messages_fts* 对象 + VACUUM + 重建
    # 修复前自动备份: state.db → state.db.malformed-backup-<timestamp>
```

### 3.5 FTS5 可用性探测

```python
def _sqlite_supports_fts5(cursor):
    # 创建临时 FTS5 表测试
    # 失败 → _fts_enabled = False → 搜索返回空列表

def _fts_table_probe(cursor, table_name):
    # 测试 FTS 表是否可以 SELECT
    # 区分: FTS5 模块缺失 vs trigram tokenizer 缺失
```

---

## 4. 消息生命周期与 `active`/`compacted` 字段

这是理解"哪些消息可搜索"的关键设计。

```
消息写入 → active=1, compacted=0
    │
    ├─ FTS5 索引 ← INSERT trigger
    │  (此时可搜索)
    │
    ├─ undo/rewind:
    │   执行: UPDATE messages SET active=0 WHERE id > N
    │   效果: FTS DELETE trigger 触发 → 索引删除 → 搜不到
    │
    └─ compaction (压缩):
        执行: archive_and_compact():
          1. UPDATE messages SET active=0, compacted=1 WHERE session_id=? (旧消息)
          2. INSERT 新的压缩摘要消息 (active=1, compacted=0)
        效果: 
          - 旧消息: active=0, compacted=1 → FTS 索引保留 → ★ 搜得到
          - 新摘要: active=1, compacted=0 → FTS 索引保留 → ★ 搜得到
```

**search_messages 的过滤逻辑**:

```sql
-- 默认排除纯 undo 的消息，但保留 compaction 历史
WHERE (m.active = 1 OR m.compacted = 1)

-- 等价规则:
-- active=1, compacted=0  → ✅ 可搜索 (正常活跃消息)
-- active=0, compacted=1  → ✅ 可搜索 (压缩前的历史记录)
-- active=0, compacted=0  → ❌ 不可搜索 (被 undo 撤回的消息)
```

---

## 5. FTS5 搜索实现 (`search_messages`)

### 5.1 主流程

```python
def search_messages(query, source_filter, exclude_sources, role_filter, 
                    limit, offset, sort, include_inactive):
    # 1. 检查 _fts_enabled (FTS5 是否可用)
    # 2. 清洗查询: _sanitize_fts5_query(query)
    #    - 保留引号内的特殊字符
    #    - 转义裸特殊字符 (*, %, _, 等)
    #    - 给 dotted/hyphenated 词加引号: "my-app.config"
    # 3. 检测是否为 CJK 查询
    # 4. 构建动态 WHERE + 执行 SQL
    # 5. 为每个匹配消息加载 ±1 上下文
    # 6. 从结果中移除完整 content (保留 snippet 节省 token)
```

### 5.2 搜索 SQL (英文/FTS5 主路径)

```sql
SELECT
    m.id,
    m.session_id,
    m.role,
    snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
    m.content,
    m.timestamp,
    m.tool_name,
    s.source,
    s.model,
    s.started_at AS session_started
FROM messages_fts
JOIN messages m ON m.id = messages_fts.rowid
JOIN sessions s ON s.id = m.session_id
WHERE messages_fts MATCH ?
  AND (m.active = 1 OR m.compacted = 1)        -- 排除 undo
  AND s.source NOT IN ('subagent', 'tool')     -- 排除隐藏源
  AND m.role IN ('user', 'assistant')          -- 角色过滤
ORDER BY rank                                   -- 或 m.timestamp DESC, rank
LIMIT ? OFFSET ?
```

### 5.3 CJK 搜索的分支处理

```
_query → _contains_cjk(query)?

├─ 是 CJK:
│   ├─ CJK字符 >= 3 且 trigram tokenizer 可用:
│   │   → messages_fts_trigram MATCH (trigram FTS5, 支持 snippet/rank)
│   │   → 对非 operator token 加引号: "大别山" OR "项目"
│   │
│   ├─ CJK字符 < 3 或 trigram 不可用:
│   │   → LIKE '%keyword%' (子串匹配, 无 rank, 无 snippet)
│   │   → 按 m.timestamp DESC 排序
│   │   → 手动构造 snippet: substr(content, instr-40, 120)
│   │
└─ 非 CJK:
    → messages_fts MATCH (unicode61 FTS5, 标准流程)
```

**为什么需要 trigram**:

unicode61 tokenizer 把中文每个字当独立 token:
- 搜索 "大别山" → FTS5 理解为 `大 AND 别 AND 山` → 匹配到任何含这三个字的任意排列消息

trigram tokenizer 生成重叠 3-gram:
- "大别山项目" → `大别山`, `别山项`, `山项目`
- 搜索 "大别山" → 精确匹配 3-gram `大别山`

### 5.4 查询清洗 (`_sanitize_fts5_query`)

处理步骤:
1. 保护引号内的内容 (临时替换为 `\x00Q{i}\x00`)
2. 转义裸特殊字符: `* % _, ${}` 等 FTS5 保留字符
3. 给 `word.word` / `word-word` / `word_word` 加引号 (防止被 tokenizer 拆分)
4. 恢复保护的引号内容

### 5.5 上下文加载

```python
# 为每个匹配加载 ±1 条相邻消息
for match in matches:
    ctx_cursor = conn.execute("""
        -- 前一条 (timestamp < match, 或相同时间但 id < match)
        SELECT role, content FROM messages ... WHERE timestamp < ? ... LIMIT 1
        UNION ALL
        -- 匹配消息本身
        SELECT role, content FROM messages WHERE id = ?
        UNION ALL
        -- 后一条
        SELECT role, content FROM messages ... WHERE timestamp > ? ... LIMIT 1
    """)
    # 处理多模态内容: 提取 text part, 截断到 200 字符
    match["context"] = [{role, content_preview}, ...]

# 移除完整 content (snippet 够用)
match.pop("content", None)
```

---

## 6. 锚点视图 (`get_anchored_view`)

这是 session_search discovery 模式的核心——一次查询同时获取三个切片。

```python
def get_anchored_view(session_id, around_message_id, window=5, bookend=3,
                      keep_roles=("user", "assistant")):
    """
    返回:
      window:        以 anchor 为中心 ±window 的消息 (±5 = 共 11 条)
                     role 过滤掉 tool 噪声 (但 anchor 永不过滤)
      bookend_start: session 头部前 bookend 条 user/assistant 消息 (开篇)
      bookend_end:   session 尾部最后 bookend 条 user/assistant 消息 (结论)
      messages_before: window 之前的消息数 (判断是否到开头)
      messages_after:  window 之后的消息数 (判断是否到结尾)
    """
```

**bookend 的边界保护**:
- `bookend_start` 只取 `id < window_min_id` 的消息 (不和 window 重叠)
- `bookend_end` 只取 `id > window_max_id` 的消息
- 排除空 content 的消息 (tool-call-only assistant turn)

**效果**: 一条 FTS5 命中在 session 第 500 条消息 → 返回命中周围 ±5 条 + 开头 3 条 + 结尾 3 条 → 重建"目标→上下文→结论"而不加载全部 500 条。

### 底层: `get_messages_around`

```python
def get_messages_around(session_id, around_message_id, window=5):
    # 1. 确认 anchor 存在于 session
    # 2. 查 id <= anchor 的最近 window+1 条 (DESC 取, 含 anchor)
    # 3. 查 id > anchor 的最近 window 条 (ASC 取)
    # 4. 反转 before_rows (DESC → ASC) + after_rows → 完整窗口
    # 5. 解码 content (处理多模态 JSON), 反序列化 tool_calls
    # 返回: {window: [...], messages_before: N, messages_after: N}
```

---

## 7. session_search 四个模式

### 7.1 核心入口

```python
def session_search(query, role_filter, limit, session_id, around_message_id,
                   window, sort, profile, db, current_session_id):
    """
    模式推断逻辑:
    1. session_id + around_message_id → SCROLL
    2. session_id only → READ
    3. query → DISCOVERY
    4. 无参数 → BROWSE
    """
```

### 7.2 DISCOVERY 模式 (搜索)

流程:
1. 调用 `db.search_messages(query, ...)` — FTS5 搜索，取 `_DISCOVER_SCAN_LIMIT=300` 行
2. 调用 `_order_for_recall()` — stable-sort: 交互式会话排在 cron 前面
3. 按 lineage root 去重 (`_resolve_to_parent`)
4. 跳过当前 session lineage
5. 对每个存活命中调用 `db.get_anchored_view()` 获取 window + bookends
6. 返回 JSON

**去重逻辑** (lineage dedup):

```
FTS5 可能返回同一 session 的多条消息命中 (不同消息都包含关键词)
→ 对每个命中，用 _resolve_to_parent 找到 lineage root
→ 同一 lineage root 的命中只保留第一个 (BM25 最高的)
→ 确保返回的是不同 session，无重复
```

**排序逻辑** (`_order_for_recall`):

```python
# 只在 cron vs 交互之间重新排序；同一类的 BM25 rank 保留
sorted(raw_results, key=lambda r: 1 if r["source"] in ("cron",) else 0)
# 交互式 session 的命中排在 cron 前面
# cron 虽然排后面，但不会被排除
```

### 7.3 SCROLL 模式 (翻页)

```python
def _scroll(db, session_id, around_message_id, window, current_session_id):
    # 1. 参数校验: window ∈ [1, 20]
    # 2. 拒绝当前 session lineage 内翻页 (消息已在 context 中)
    # 3. 调用 db.get_messages_around(session_id, anchor, window)
    # 4. Lineage rebind:
    #    如果 anchor 不在 session_id 但属于同 lineage 的子 session
    #    → 透明重定向到子 session，重新查询
    #    → 返回 warning 告知调用者
    # 5. 返回 window + messages_before/after
```

**Scroll 使用模式**:
- 向前翻: 把返回的 `messages[-1].id` 作为新的 `around_message_id`
- 向后翻: 把返回的 `messages[0].id` 作为新的 `around_message_id`
- 边界消息出现在前后两个窗口中 (orientation marker)
- `messages_before < window` → 到开头了
- `messages_after < window` → 到结尾了

### 7.4 READ 模式 (dump 整个 session)

```python
def _read_session(db, session_id, head=20, tail=10):
    # 小于 head+tail 条 → 完整返回
    # 大于 → 返回前 head 条 + 后 tail 条
    #        + 提示: "pass around_message_id to scroll the middle"
```

### 7.5 BROWSE 模式 (最近 session)

```python
def _list_recent_sessions(db, limit, current_session_id):
    # 调用 db.list_sessions_rich(limit, exclude_sources=['subagent','tool'])
    # 跳过当前 session 及其 lineage
    # 跳过子 session (parent_session_id != NULL)
    # 返回: [{session_id, title, source, started_at, last_active, 
    #          message_count, preview}, ...]
```

---

## 8. Title Match (标题匹配)

在 DISCOVERY 中，除了 FTS5 内容搜索，还额外检查标题:

```python
def _title_match_result(db, query, current_lineage_root):
    # 1. 去掉引号 → normalize
    # 2. db.resolve_session_by_title(title_query)
    # 3. 找到的话 → 构造 discovery-shaped 结果 (取第一条消息作为锚点)
    # 4. 跳过隐藏源 (subagent, tool)
    # 5. 跳过当前 session
```

---

## 9. 隐藏与降权源

```python
# 从 browse/search 中完全排除
_HIDDEN_SESSION_SOURCES = ("subagent", "tool")

# 可搜索但排在交互式 session 后面
_DEMOTED_SESSION_SOURCES = ("cron",)
```

设计动机: cron job 产生大量重复词汇 ("daily summary", "cron job"，项目名等)，在 BM25 下会淹没用户的真实对话。降权而非排除，让 cron 内容在唯一匹配时仍可找到。

---

## 10. 跨 Profile 搜索

```python
def _resolve_profile_db(profile):
    # 打开另一个 profile 的 state.db (read_only=True)
    # mode=ro, 不取写锁，不影响目标 profile 运行
    # 用于解析 @session:work/20250305_091523_a1b2c3 链接

def _locate_session_db(session_id):
    # 遍历所有 profile 的 state.db 寻找 session_id
    # 当模型丢失 profile 信息时的 fallback
```

---

## 11. Session 删除与数据清理

### 11.1 单 session 删除

```python
def delete_session(session_id):
    # 1. 级联删除 delegate subagent children
    # 2. Orphan 剩余子 session: UPDATE sessions SET parent_session_id = NULL
    #    (branch/compression children 保留，只是断链)
    # 3. DELETE FROM messages WHERE session_id = ?
    #    → FTS5 DELETE trigger 自动清理索引
    # 4. DELETE FROM sessions WHERE id = ?
    # 5. 清理磁盘 JSON/JSONL 文件
```

### 11.2 空 session 自动清理

```python
def delete_session_if_empty(session_id):
    # CLI 退出时自动调用
    # 条件: title IS NULL AND message_count=0 AND 无子 session
    # 防止"启动→立即quit"产生垃圾 session
```

### 11.3 按时间剪枝

```python
def prune_sessions(older_than_days=90):
    # SELECT id FROM sessions 
    #   WHERE started_at < cutoff AND ended_at IS NOT NULL
    # 只删已结束的，不碰活跃 session
    # 返回删除条数
```

### 11.4 自动维护

```python
def maybe_auto_prune_and_vacuum(retention_days=90, min_interval_hours=24):
    # 1. 查 state_meta.last_auto_prune，24h 内跑过 → skip
    # 2. prune_sessions(older_than_days)
    # 3. 如果实际删了行 → VACUUM (回收磁盘空间)
    # 4. 更新 last_auto_prune
    # 全程不抛异常
```

配置 (`config.yaml`):
```yaml
sessions:
  auto_prune: false          # 默认关！需手动开
  retention_days: 90
  vacuum_after_prune: true   # prune 后自动 VACUUM
  min_interval_hours: 24
```

### 11.5 归档 (archive)

```python
def set_session_archived(session_id, archived=True):
    # UPDATE sessions SET archived = 1/0
    # 软隐藏，不删数据
    # 压缩链中归档/解归档一个 → 影响整条 lineage
```

---

## 12. 并发控制

```
多个写入者竞争:
  CLI session  + gateway + worktree agents + cron runner
  → 全共享一个 state.db

解决:
  1. WAL mode — 多读单写
  2. 应用层 retry: BEGIN IMMEDIATE, 15 次重试, 随机 jitter 20-150ms
  3. 确定性 busy handler 会被 convoy effect 放大 → 不用
  4. PERIODIC PASSIVE checkpoint (每 50 次写入)
     - PASSIVE: 不阻塞读者，能 check 多少算多少
     - 不用 FULL/RESTART: 会阻塞所有连接
```

---

## 13. 关键设计决策总结

| 决策 | 理由 |
|------|------|
| WAL + 应用层重试 | 多进程并发，随机 jitter 消除 convoy |
| FTS5 触发器而非应用层同步 | 保证索引一致性，出错时 rebuild 即可 |
| CJK trigram 独立表 | unicode61 拆中文为单字 → 假阳性 |
| LIKE 回退 (短 CJK) | trigram 需 ≥3 个 CJK 字符 |
| active/compacted 双标记 | undo 隐藏 vs compaction 保留历史 → 不同语义 |
| lineage dedup | 压缩链产生父子 session，避免同一对话出现多次 |
| cron 降权 | 交互优先，但不排除自动会话 |
| bookends (头3+尾3) | 一次调用重建"目标→结论"，不加载全部 |
| cross-profile read-only | 不影响目标 profile 运行 |
| auto_prune 默认关 | 不意外删除用户数据 |

---

## 14. 最小复用方案

如果你要为自己的项目实现类似的 session search，最小可行架构:

### 数据库层
```
sessions (id, source, title, started_at, ended_at, message_count, ...)
messages (id, session_id, role, content, timestamp, active)
messages_fts (FTS5, INSERT/UPDATE/DELETE triggers)
state_meta (key, value) — 存 last_auto_prune
```

### 核心方法
```
SessionDB.search_messages(query, role_filter, limit)
  → FTS5 MATCH, snippet(), JOIN sessions → [匹配消息]

SessionDB.get_messages_around(session_id, anchor_id, window)
  → ASC/DESC query → {window, messages_before, messages_after}

SessionDB.get_anchored_view(session_id, anchor_id, window, bookend)
  → {window, bookend_start, bookend_end, counts}

SessionDB.prune_sessions(older_than_days)
  → DELETE WHERE started_at < cutoff AND ended_at IS NOT NULL
```

### 工具层
```
session_search(query) → discover (去重, bookends, snippet)
session_search(session_id, around_message_id) → scroll (翻页)
session_search(session_id) → read (全量 dump)
session_search() → browse (最近列表)
```

### 文件大小
- `hermes_state.py`: ~5500 行 (含 schema 管理、修复、billing、handoff)
- `session_search_tool.py`: ~920 行 (纯搜索逻辑 + 工具注册)
- 核心搜索链路: ~500 行可独立提取
