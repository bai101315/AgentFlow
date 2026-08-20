# Memory Review and SKILL Review

Memory: 默认每10次对话，触发一次候选复盘
SKILL：默认10次工具使用， 触发一次SKIL调用

都不会立刻调用；等到以下情况出现，才会调用
```python
if (
    final_response
    and not interrupted
    and not agent.skip_background_review
    and (_should_review_memory or _should_review_skills)
):
    agent._spawn_background_review(
        messages_snapshot=list(messages),
        review_memory=_should_review_memory,
        review_skills=_should_review_skills,
    )
```

## 一个问题
如果用户对话次数没超过10次，就不触发了？？触发条件之后，是所有的信息都会都会进行处理吗？？还是只处理最后几条？？

# Memory Prompt and SKILL Prompt

Memory: 自身信息/agent行为方式的期望
'''python
_MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — their persona, desires, "
    "preferences, or personal details worth remembering?\n"
    "2. Has the user expressed expectations about how you should behave, their work "
    "style, or ways they want you to operate?\n\n"
    "If something stands out, save it using the memory tool. "
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)
'''

SKILL：
```
一、触发时机(何时更新)
Be ACTIVE — most sessions produce at least one skill update.

默认是"每个会话都可能更新",不是出了问题才更新。不要错过学习机会；;

1. 用户纠正你的风格/语气/格式/可读性/啰嗦程度 —— 这是一等技能信号
2. 用户纠正你的工作流/方法/步骤顺序 —— 把纠正编码为该类任务的 pitfall 或显式步骤。
3. 涌现出非平凡的技术/修复/绕行方案/调试路径/工具使用模式 —— 未来会话受益就捕获。
4. 本会话加载/查阅过的技能被发现是错的、缺步骤、过时的 —— 立刻修补。

用户偏好的归属铁律: 风格/格式/工作流偏好 → 写进 SKILL.md body(不是只进记忆)。记忆管"用户是谁、现状如何";技能管"这类任务该怎么为用户做"。

二、怎么更新(优先级从高到低,选最早匹配的)
1. 更新当前会话加载过的技能
2. 更新已有伞级技能
3. 在已有伞下加支持文件
4. 创建新的伞级技能

三、保护技能(绝对禁止编辑)

四、不要捕获(否则变成长期自我束缚)
环境依赖的失败;;对工具/功能的负面断言;会话内已自愈的瞬时错误;一次性任务叙事
```

# Review Agent 怎么创建？

```
review_agent = AIAgent(
    model=_rt.get("model") or agent.model,
    max_iterations=16,
    quiet_mode=True,
    platform=agent.platform,
    provider=_rt.get("provider") or agent.provider,
    api_mode=_rt.get("api_mode"),
    base_url=_rt.get("base_url") or None,
    api_key=_rt.get("api_key") or None,
    credential_pool=_rt.get("credential_pool"),
    request_overrides=_rt.get("request_overrides") or {},
    parent_session_id=agent.session_id,
    enabled_toolsets=getattr(agent, "enabled_toolsets", None),
    disabled_toolsets=getattr(agent, "disabled_toolsets", None),
    skip_memory=True,
    **_fork_kwargs,
)

review_agent._memory_write_origin = "background_review"
review_agent._memory_write_context = "background_review"
```

