# 技能系统

backend/skill/types.py       Skill 数据结构
backend/skill/loader.py      扫描 skills 目录并加载 Skill
backend/skill/parser.py      解析 SKILL.md frontmatter
backend/skill/validation.py  校验 skill 格式
backend/skill/installer.py   安装 .skill 压缩包
backend/skill/manager.py     管理 custom skill

## SKILL 格式
SKILL.md 开头必须有 YAML frontmatter，例如：
```
---
name: deep-research
description: Use this skill when the user asks for web research...
license: MIT
---

# Deep Research Skill

具体工作流...
```

## SKILL 数据结构

在 types.py (line 4)：
```python
@dataclass
class Skill:
    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path
    category: str      # public 或 custom
    enabled: bool = False
```
name          skill 名称
description   给 Agent 判断是否应该使用这个 skill
skill_file    SKILL.md 真实文件路径
category      public/custom
enabled       是否启用

## 记载流程

入口是 loader.py (line 20) 的 load_skills(...)。

它会：
- 找到 skills 根目录
- 扫描 public/ 和 custom/
- 递归查找 SKILL.md
- 用 parse_skill_file(...) 解析 frontmatter
- 生成 Skill 对象
- 从 extensions_config.json 读取启用状态
- 返回排序后的 skills

先扫秒public后扫墓奥custom，相同名字的skill会进行覆盖

## 启用/禁用

启用状态来自 extensions_config.json：
如果想禁用某个skill
{
  "skills": {
    "deep-research": {
      "enabled": false
    }
  }
}

## Agent 怎么知道有 skill
在构建系统提示词时，lead_agent/prompt.py 会调用：
get_skills_prompt_section(...)

```md
它不会把所有 SKILL.md 全文塞进 prompt，只注入一个清单：
<skill_system>
You have access to skills...

<available_skills>
    <skill>
        <name>deep-research</name>
        <description>...</description>
        <location>../skills/public/deep-research/SKILL.md</location>
    </skill>
</available_skills>
</skill_system>
```
## Progressive Loading
先只给 Agent 看 skill 的简介，真正需要时才读完整文件，节省上下文。流程如下：

举例：用户说“帮我做一个深度调研”。

Agent 在 prompt 里看到 deep-research 的 description 匹配
Agent 调用 read_file 读取：
../skills/public/deep-research/SKILL.md
如果 SKILL.md 里提到 references/foo.md，Agent 再按需读取
Agent 按 skill 里的步骤执行任务

## 格式校验
validation.py (line 11) 允许的 frontmatter 字段是：
```python
{
  "name",
  "description",
  "license",
  "allowed-tools",
  "metadata",
  "compatibility",
  "version",
  "author",
}
```
必须有：name和description

## 自定义/演化skill
这个项目还有一个 skill_manage 工具，在 skill_manage_tool.py (line 224)。

```text
create       创建 custom skill
edit         整体编辑 SKILL.md
patch        局部替换 SKILL.md
delete       删除 custom skill
write_file   写 references/templates/scripts/assets 下的辅助文件
remove_file  删除辅助文件
```

但只允许管理 skills/custom/，不能直接改 public skill。

并且每次修改都会写历史：
skills/custom/.history/<skill_name>.jsonl

## Agent创建skill
所以系统提示词里会加入一段：
```
任务需要 5+ tool calls
遇到非显而易见的问题
用户纠正后形成可复用流程
发现重复工作流
```
但也会要求用户确认


