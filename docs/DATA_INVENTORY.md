# AgentFlow Data Inventory

*Generated: 2026-07-16 | Agent: bwq | Session: bwq-31d2a42e*

---

## 1. `memory.json` — Long-Term Agent Memory

**Location:** `.agentflow/agents/bwq/memory.json`

**Format:** JSON, structured as a single object.

**Schema:**

| Top-level key | Structure | Purpose |
|---|---|---|
| `version` | String (`"1.0"`) | Schema version marker |
| `lastUpdated` | ISO 8601 timestamp | Last write time |
| `user` | Object with 3 sub-keys | Categorized narrative summaries about the user |
| `user.workContext` | `{ summary, updatedAt }` | Job/project context — education, career state, tech stack |
| `user.personalContext` | `{ summary, updatedAt }` | Language, location, OS, personality patterns |
| `user.topOfMind` | `{ summary, updatedAt }` | Current active tasks, frustrations, immediate concerns |
| `history` | Object with 3 sub-keys | Time-bucketed narrative summaries of conversation history |
| `history.recentMonths` | `{ summary, updatedAt }` | Recent detailed conversation history |
| `history.earlierContext` | `{ summary, updatedAt }` | Older context (currently empty) |
| `history.longTermBackground` | `{ summary, updatedAt }` | Stable biographical facts |
| `facts` | Array of fact objects | Discrete, machine-extracted facts with metadata |

**Individual fact object schema:**

| Field | Description |
|---|---|
| `id` | Unique fact identifier (e.g., `fact_5665fa8e`) |
| `content` | Human-readable fact statement |
| `category` | Tag: `context`, `goal`, `knowledge`, `preference`, `behavior`, `correction` |
| `confidence` | Float 0.0–1.0 |
| `createdAt` | ISO 8601 timestamp |
| `source` | Session ID that generated the fact |
| `sourceError` | (Optional) Description of what went wrong, for correction facts |

**What kind of facts are stored (43 total):**

| Category | Count | Examples |
|---|---|---|
| `context` | ~9 | Name (白伟琦), location (沈阳), OS (Windows), project directory, session log paths |
| `knowledge` | ~17 | Project features: prompt caching, self-improving, multi-agent scheduling, skill system, observability, sub-agent config details, MCP integration |
| `preference` | ~4 | Code analysis response structure (结论→证据→不足→改进), use sub-agent when requested, verify code before claiming |
| `behavior` | ~2 | Tested coding with quicksort, experienced resume rejections |
| `correction` | ~4 | Assistant incorrectly characterized project status, sub-agent recursion limit issues |
| `goal` | ~1 | Developing AI Agent for job portfolio |

**Update mechanism:** The memory update pipeline runs via middleware (`signal_detection` → update queue → updater → storage). Facts are extracted by LLM summarization and deduplicated by content hash.

---

## 2. `session_logs/` — Per-Session Trace Logs

**Location:** `.agentflow/session_logs/`

**Files:** One JSON file per session. Currently contains only `bwq-31d2a42e.json` (a merged session spanning 2026-07-03 to 2026-07-16, consolidating 7 earlier legacy session files).

**Format:** JSON, structured as a single object.

**Top-level fields:**

| Field | Type | Description |
|---|---|---|
| `session_id` | String | Session identifier (e.g., `bwq-31d2a42e`) |
| `started_at` | ISO 8601 | Session start time |
| `ended_at` | ISO 8601 | Session end/last-update time |
| `exit_reason` | String | How session ended (`keyboard_interrupt`) |
| `initial_agent_name` | String | Agent used at session start (`bwq`) |
| `initial_thread_id` | String | Thread ID at session start |
| `initial_model_name` | String | Model at session start (`deepseek-v4`) |
| `merged_legacy_session_ids` | String[] | Prior session IDs merged into this one |
| `merged_legacy_files` | String[] | Prior log filenames merged into this one |
| `latest_trace` | Object | Most recent turn's trace data |
| `recent_traces` | Array | Last ~20 turns with full trace data |
| `turns` | Array | All turns in the session (may be truncated to first N chars) |

**Per-trace fields (in `latest_trace` and each element of `recent_traces`):**

| Field | Type | Description |
|---|---|---|
| `turn_index` | Integer | Sequential turn number (1-indexed, but may reset across merged sessions) |
| `trace_id` | UUID | Unique trace identifier |
| `timestamp` | ISO 8601 | When the turn was processed |
| `elapsed_ms` | Integer | Wall-clock time for this turn (ms) |
| `completed` | Boolean | Whether the turn finished normally |
| `failure_reason` | String/null | Why it failed, if applicable |
| `content_mode` | String | `summary` or `full` |
| `tool_names` | String[] | Tools called in this turn |
| `tool_call_count` | Integer | Number of tool calls made |
| `had_any_failure` | Boolean | Whether any tool call failed |
| `failed_tool_call_count` | Integer | Number of failed tool calls |
| `failed_tool_names` | String[] | Which tools failed |
| `recovered_after_failure` | Boolean | Whether the agent recovered from failures |
| `failure_events` | Object[] | Detailed failure event records |
| `input_tokens` | Integer | Total input tokens (billable + cached) |
| `output_tokens` | Integer | Output tokens |
| `total_tokens` | Integer | Sum of input + output |
| `billable_input_tokens` | Integer | Only non-cached input tokens |
| `prompt_cache_hit_rate` | Float | 0.0–1.0 cache hit ratio |
| `trace_file_path` | String/null | Path to full observability trace JSON |
| `trace_file_reason` | String/null | Why a trace file was saved (e.g., `slow_trace,high_billable_input_tokens`) |
| `diagnostic_reasons` | String[] | Array of diagnostic flags |
| `user_input` | String | What the user said this turn |
| `assistant_output` | String | What the assistant replied |

**Per-turn fields (in `turns` array):**

| Field | Description |
|---|---|
| `agent_name` | Which agent responded |
| `assistant_output` | Full assistant response text |
| `model_name` | Model used |
| `thread_id` | Thread identifier |
| `timestamp` | When processed |
| `turn_index` | Sequential number |
| `usage` | Token usage object (`input_tokens`, `output_tokens`, `total_tokens`, `billable_input_tokens`, `prompt_cache_hit_rate`, cache breakdown fields) |
| `user_input` | What the user said |

**Session statistics (from current session):**
- 41 turns indexed in `recent_traces`
- Session duration: 13 days (2026-07-03 → 2026-07-16)
- Peak cache hit rate: 99.95% (turn 23)
- Typical cache hit rate: 97–99% for consecutive turns
- 0% cache hit rate on turns 40–41 (cold start after session gap)

---

## 3. `session_search.db` — Cross-Session Conversation Search

**Location:** `.agentflow/session_search.db`

**Format:** SQLite database with FTS5 (Full-Text Search) extension.

**Implementation:** `backend/session_search/store.py` — `SessionSearchStore` class.

**Schema (2 tables):**

### `session_messages` (main table)

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment row ID |
| `dedupe_key` | TEXT UNIQUE | SHA256 hash of `session_id|message_id|role` (or `session_id|ordinal|role|content_hash`) — prevents re-indexing the same message |
| `session_id` | TEXT | Session identifier |
| `thread_id` | TEXT | Thread identifier |
| `message_id` | TEXT (nullable) | LangChain message ID |
| `role` | TEXT | `user` or `assistant` |
| `content` | TEXT | Full message text (truncated to 1200 chars for display) |
| `ts` | TEXT | ISO 8601 timestamp |
| `ordinal` | INTEGER | Position in the conversation |
| `active` | INTEGER | 1 = active, 0 = soft-deleted (for pruning) |
| `source` | TEXT | Origin label (default: `agent`) |
| `metadata` | TEXT | JSON blob (currently empty `{}`) |

### `session_messages_fts` (virtual FTS5 index)

| Column | Description |
|---|---|
| `content` | Indexed from `session_messages.content` |
| `role` | Indexed from `session_messages.role` |
| `session_id` | Indexed from `session_messages.session_id` |
| `content_rowid` | Linked to `session_messages.id` |

**What gets indexed:** User messages (with `<uploaded_files>` blocks stripped) and final assistant replies (tool-call-only messages are skipped).

**Search modes:**
- FTS5 full-text search with BM25 scoring
- LIKE-based fallback for CJK (Chinese/Japanese/Korean) queries when FTS5 returns nothing
- Context window retrieval (`around` for surrounding messages)
- Session reading (`read_session` with head/tail pagination)
- Recent sessions listing (`recent_sessions`)
- Soft-delete and time-based pruning (`delete_session`, `prune_older_than`)

**Not stored in session_search.db:**
- Tool call arguments or results
- Intermediate agent reasoning (LangGraph state graph internals)
- Full system prompts
- MCP tool schemas

---

## 4. Related Data Stores (Supplemental)

| Store | Location | Format | Purpose |
|---|---|---|---|
| `checkpoints.db` | `.agentflow/checkpoints.db` | SQLite (LangGraph checkpointer) | Conversation state — enables session resume across restarts |
| `observability.db` | `.agentflow/observability.db` | SQLite | Local observability data (traces, spans, tool calls) |
| `observability/traces/` | `.agentflow/observability/traces/` | JSON files per trace | Full per-turn trace dumps, saved when diagnostic thresholds are met |
| `agent_threads.yaml` | `.agentflow/agent_threads.yaml` | YAML | Agent-to-thread mapping configuration |
| `last_agent.txt` | `.agentflow/last_agent.txt` | Plain text | Last used agent name |
| `.history/*.jsonl` | `skills/custom/{skill}/.history/` | JSONL | Per-skill version history with prev/new content diffs |

---

## 5. Conclusion: What the Agent Knows vs. What It Doesn't

### ✅ What the agent knows about the user

1. **Identity & background:** Name (白伟琦 / Bai Weiqi), Master's year 2 CS student, located in 沈阳 with family connection to 洛阳, job hunting for summer internships.

2. **Project & technical domain:** Building AgentFlow (Hermes Agent fork), implements prompt caching middleware, self-improving background review, multi-agent scheduling, layered memory, skill hot-plugging, tool governance, LangSmith/Langfuse observability.

3. **Platform & tools:** Windows OS, DeepSeek V4 model, project at `C:\Users\BAI\Desktop\project\`, coding in Python/JS/TS/Go/Rust/Shell.

4. **Behavioral patterns:** Strict about code verification (assistant must read code before making claims), requires structured code analysis responses (结论→证据→不足→改进), insists on using sub-agents when requested regardless of limitations.

5. **Emotional state history:** Has experienced frustration with incorrect assistant claims, anxiety about internship timeline, and processing of deep emotional content.

6. **Interaction history:** Full conversation history since 2026-07-03 across all turns, including all user inputs and assistant outputs, tool calls, token usage, and cache hit rates.

### ❌ What the agent does NOT know

1. **Real-time external information:** No live access to job listings, company HC status, or current events without explicit web search.

2. **Private credentials:** API keys, passwords, or other secrets are in `.env` and not indexed into memory or session search.

3. **Non-indexed data:** Files outside the project directory, system-level data, browser history, email, messaging apps, or other desktop activity not explicitly shared.

4. **Exact codebase state beyond last session:** The agent knows *about* the codebase (via fact summaries) but doesn't have the full codebase in memory — it reads files on demand.

5. **User's thoughts/intentions not expressed:** Only what was explicitly said in conversation is captured.

### ⚠️ Data retention notes

- `session_search.db` supports time-based pruning (`prune_older_than`).
- `memory.json` facts have no automatic expiry but are gated by confidence scores.
- Session logs are append-only JSON files that can grow large; the current session file is ~140KB compressed.
- Skill history (`.history/*.jsonl`) grows with each create/patch operation.
