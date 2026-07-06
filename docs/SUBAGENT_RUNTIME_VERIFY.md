# Subagent Runtime Verification Report

## Question 1: Do "code", "coding_agent", "coding-agent", "code-agent", "coding" all resolve to the same subagent?

**Yes.** All five names resolve to `"code"`.

### Evidence: Alias mapping in `backend/subagents/registry.py`

```python
_SUBAGENT_ALIASES = {
    "coding_agent": "code",
    "coding-agent": "code",
    "code-agent": "code",
    "coding": "code",
}


def _normalize_subagent_name(name: str) -> str:
    return _SUBAGENT_ALIASES.get(name, name)
```

The function `get_subagent_config(name)` calls `_normalize_subagent_name(name)` which maps `"coding_agent"`, `"coding-agent"`, `"code-agent"`, and `"coding"` to the canonical name `"code"`. Names not in the alias dict pass through unchanged.

### Evidence: Builtin registration in `backend/subagents/builtins/__init__.py`

```python
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "code": CODING_AGENT_CONFIG,
}
```

All aliases resolve to the `"code"` key → `CODING_AGENT_CONFIG`.

---

## Question 2: What is the current timeout_seconds for the "code" subagent?

**The effective `timeout_seconds` is 300 seconds (5 minutes).**

### Builtin default (`backend/subagents/config.py`)

```python
@dataclass
class SubagentConfig:
    # ...
    timeout_seconds: int = 900  # 15 minutes
```

The `CODING_AGENT_CONFIG` in `backend/subagents/builtins/coding_agent.py` does NOT set `timeout_seconds`, so it inherits the dataclass default of `900`.

### Config.yaml override

```yaml
# config.yaml
subagents:
  timeout_seconds: 300
  max_turns: 100
```

### Override logic (`backend/config/subagents_config.py`)

```python
class SubagentsAppConfig(BaseModel):
    timeout_seconds: int = Field(default=900, ...)

    def get_timeout_for(self, agent_name: str) -> int:
        override = self.agents.get(agent_name)
        if override is not None and override.timeout_seconds is not None:
            return override.timeout_seconds
        return self.timeout_seconds  # global default = 300 from config.yaml
```

Since `config.yaml` sets the global `subagents.timeout_seconds: 300` and there is NO per-agent override for `"code"` (the `agents:` dict is empty), `get_timeout_for("code")` returns `300`.

### Applied in `backend/subagents/registry.py`

```python
app_config = get_subagents_app_config()
effective_timeout = app_config.get_timeout_for(canonical_name)  # → 300
```

**Result: 900 → overridden to 300.**

---

## Question 3: What is the max_turns for the "code" subagent?

**The effective `max_turns` is 100.**

### Builtin default (`backend/subagents/builtins/coding_agent.py`)

```python
CODING_AGENT_CONFIG = SubagentConfig(
    name="code",
    # ...
    max_turns=200,
)
```

The coding agent's builtin `max_turns` is **200**.

### Config.yaml override

```yaml
subagents:
  timeout_seconds: 300
  max_turns: 100
```

Yes, **there IS a config.yaml override**: `subagents.max_turns: 100` is a global override.

### Override logic (`backend/config/subagents_config.py`)

```python
def get_max_turns_for(self, agent_name: str, builtin_default: int) -> int:
    override = self.agents.get(agent_name)
    if override is not None and override.max_turns is not None:
        return override.max_turns       # per-agent override (not used here)
    if self.max_turns is not None:
        return self.max_turns            # global override → 100
    return builtin_default               # 200 (not reached)
```

Since there is no per-agent override for `"code"` and the global `max_turns` is `100` (not `None`), `get_max_turns_for("code", 200)` returns **100**.

### Applied in `backend/subagents/registry.py`

```python
effective_max_turns = app_config.get_max_turns_for(canonical_name, config.max_turns)
```

**Result: 200 → overridden to 100.**

---

## Question 4: Where does the max_turns override get applied in task_tool.py?

**The `max_turns` override from the LLM tool call parameter is applied at lines 92-93 of `task_tool.py`:**

```python
# task_tool.py, lines 87-93
# Build config overrides
overrides: dict = {}
skills_section = get_skills_prompt_section()

if skills_section:
    overrides["system_prompt"] = config.system_prompt + "\n\n" + skills_section

if max_turns is not None:
    overrides["max_turns"] = max_turns

if overrides:
    config = replace(config, **overrides)
```

Flow:
1. `get_subagent_config(subagent_type)` is called — applies `config.yaml` overrides (timeout=300, max_turns=100).
2. The returned config already has `max_turns=100` (from config.yaml).
3. If the LLM passes a `max_turns` parameter (e.g., `max_turns=50`), it **further overrides** via `replace(config, max_turns=max_turns)`.

Note: The LLM can *only lower* max_turns further; it cannot restore the builtin default of 200.

---

## Conclusion: Why Code Subagent Tasks Keep Timing Out

**Root cause: The `config.yaml` global timeout of 300 seconds (5 minutes) is too short for the `max_turns=100` workload.**

| Parameter | Builtin Default | config.yaml | Effective |
|-----------|----------------|-------------|-----------|
| `timeout_seconds` | 900 (15 min) | **300 (5 min)** | **300** |
| `max_turns` | 200 | **100** | **100** |

The code subagent gets **100 turns** but only **300 seconds** of total execution time. This means:

- Each turn must complete in ~3 seconds on average (300s / 100 turns).
- With an LLM API call typically taking 5-30 seconds per response, plus tool execution time (file reads, bash commands, etc.), 3 seconds per turn is completely unrealistic.
- The subagent will either timeout at the 300-second wall before exhausting its 100 turns, or the 100-turn budget is wasteful because the timeout comes first.

**Recommendation:** Either:
1. Increase `config.yaml` `subagents.timeout_seconds` to a realistic value (e.g., 900 or 1200), OR
2. Decrease `subagents.max_turns` so the turn budget matches the available time, OR
3. Add a per-agent override for `"code"` that sets a higher timeout:

```yaml
subagents:
  timeout_seconds: 300
  max_turns: 100
  agents:
    code:
      timeout_seconds: 900   # restore 15 min for code subagent
      max_turns: 150
```
