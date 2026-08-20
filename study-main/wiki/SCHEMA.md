# Wiki Schema

## Domain
Hermes Agent 内部架构。由 Nous Research 开发的开源 AI agent 框架的源码级知识体系。
涵盖：agent 生命周期、tool 系统、memory 系统、prompt 缓存、沙箱系统、子 agent 调度、
CLI 架构、gateway、guardrail 系统、skill 系统、observability。

这是一个深入到函数调用级别、数据结构级别的技术 wiki。

## Conventions
- File names: lowercase, hyphens, no spaces (e.g., `tool-disclosure.md`)
- Every wiki page starts with YAML frontmatter (see below)
- Use `[[wikilinks]]` to link between pages (minimum 2 outbound links per page)
- When updating a page, always bump the `updated` date
- Every new page must be added to `index.md` under the correct section
- Every action must be appended to `log.md`
- **Provenance markers:** On pages that synthesize 3+ sources, append `^[raw/articles/source-file.md]`
  at the end of paragraphs whose claims come from a specific source.
- **Code references:** Wiki pages for source files must note the file path under `~/.hermes/hermes-agent/`
  (e.g., `run_agent.py`, `agent/conversation_loop.py`).

## Frontmatter
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | summary
tags: [from taxonomy below]
sources: [source_file_path or raw/ source]
# Optional quality signals:
confidence: high | medium | low
contested: true
contradictions: [other-page-slug]
---
```

## raw/ Frontmatter
```yaml
---
source_url: https://github.com/NousResearch/hermes-agent/blob/main/run_agent.py
ingested: YYYY-MM-DD
sha256: <hex digest of body below frontmatter>
---
```

## Tag Taxonomy

### Entities (things that exist as files, classes, or concrete implementations)
- `core-class` — a Python class (AIAgent, MemoryStore, IterationBudget)
- `source-file` — a `.py` file under the hermes-agent repo
- `tool` — a tool handler (read_file, terminal, delegate_task)
- `subsystem` — a directory or logical module (agent/, tools/, gateway/)
- `config-key` — a config.yaml key

### Concepts (mechanisms, architectures, design patterns)
- `agent-loop` — the conversation loop and its invariants
- `tool-system` — tool registration, discovery, dispatch, filtering
- `memory-system` — frozen snapshots, MEMORY.md, nudge logic, providers
- `prompt-caching` — three-tier prompt, Anthropic cache_control, SQLite persistence
- `sandbox` — execute_code isolation, UDS RPC, env scrubbing
- `guardrail` — safety checks, approval, tool call guardrails
- `subagent` — delegate_task, child AIAgent, constraints
- `skill-system` — skill loading, auto-creation, nudge triggers
- `context-compression` — context compression and management
- `cli-architecture` — argparse, fire, prompt_toolkit, slash commands
- `provider-system` — model provider adapters, credential pools
- `observability` — logging, session JSON, analytics

### Meta
- `comparison` — side-by-side analysis
- `design-decision` — why something was built a certain way
- `pitfall` — known bugs, footguns, surprising behavior
- `definition` — terminology

Rule: every tag on a page must appear in this taxonomy. Add new tags here first.

## Page Thresholds
- **Create a page** when an entity/concept appears in 2+ areas of the codebase OR is central to understanding one subsystem
- **Add to existing page** when a source mentions something already covered
- **DON'T create a page** for passing mentions or minor utility functions
- **Split a page** when it exceeds ~200 lines
- **Archive a page** when its content is fully superseded by code changes

## Entity Pages
One page per notable entity (file, class, tool, config key). Include:
- Overview / what it is
- File location (`~/.hermes/hermes-agent/...`)
- Key methods or attributes
- Relationships to other entities
- Notable design decisions or pitfalls

## Concept Pages
One page per architectural concept. Include:
- Definition and purpose
- How it works (with code paths)
- Key files involved
- Related concepts
- Open questions or unresolved tensions

## Update Policy
When new information conflicts with existing content:
1. Check the source code — the code is the ultimate authority
2. If genuinely contradictory, note both positions with dates
3. Mark `contested: true` and `contradictions: [page-name]`
4. Flag for review
