
没有收藏选项



MEMORY.md 存技术偏好，USER.md 存人格

```
TWO TARGETS:
- 'user': who the user is -- name, role, preferences, communication style, pet peeves
- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned
```
USER.md：你是谁。用中文沟通。喜欢简短回答还是详细分析。工作人格还是放松人格。

MEMORY.md：环境是什么。项目用 pytest。WSL Ubuntu。DeepSeek V4 是主模型。偏好技术深度。

# 怎么在工作和生活之间切换：
## 方案一：全写在 USER.md 里，让 LLM 自己判断

```
USER.md:
    User communicates in Chinese (Simplified).
    工作时偏好技术深度、精确回答、不需要寒暄。
    生活时偏好温和语气、有人情味、可以闲聊。
    When asking about code/architecture/tools => work mode.
    When chatting casually => life mode.

优缺点：
- 优点：零维护，永远生效
- 缺点：依赖 LLM 正确判断场景
```

## 方案二：用 Profiles 完全隔离
```
hermes profile create work --clone    # 创建工作 profile
hermes profile create life --clone    # 创建生活 profile

然后分别编辑各自的 USER.md：

~/.hermes/profiles/work/memories/USER.md:
    User is a software engineer.
    偏好技术深度、精确回答、代码优先、不需要寒暄。

~/.hermes/profiles/life/memories/USER.md:
    User 偏好温和语气、有人情味、可以闲聊、不赶时间。

优缺点：
- 优点：完全隔离，连 config、skills、session 都分开
- 缺点：需要手动切换 profile

```

## 方案三：方案一 + Skill 引导

```
MEMORY.md:
    User 有两种对话模式：
    - work: 技术问题、项目问题、架构讨论 → 精确、深入、无废话
    - life: 闲聊、生活话题 → 温和、有人情味

USER.md:
    User communicates in Chinese. Default to work mode.

再让 curator 或背景回顾自动创建一个 skill（比如 context-detection），skill 里写着判断规则。

优缺点：
- 优点：不切 profile，自动生效
- 缺点：需要积累几次对话后才能触发 skill 创建

```
