# SKILL 是什么

答案：
```
Agent Skil是把「指令、脚本、模板」一体化打包成可复用能力包的机制，关键在于三件事:Agent能自动发现它、按需加载它、在需要时调用里面的脚本和资源。它不只是「存prompt」，而是一份Agent能自己翻阅的「操作手册+工具箱」。每个Skill是一个文件夹，里面有一份SKILL.md指令文件，还可以带上脚本、模板、参考文档这些资源。

它和普通prompt最大的区别是:Skill能被Agent 自动发现和按需加载，不用你每次手动输入;和MCP工具的区别是:MCP给Agent 提供外部工具和数据的访问能力，而Skill教Agent拿到这些工具和数据之后该怎么用。

Anthropic在2025年10月推出了Agent Skills，同年12 月把规范作为开放标准发布出来，允许其他 Agent 平台按照这套格式来兼容Skills生态。
```

## SKILL 构成

```
code-review/                  # Skill 文件夹，名字就是这个 Skill 的标识
├── SKILL.md                  # 核心指令文件（必须有）
├── scripts/                  # 可选：可执行的脚本
│   └── check_security.py     # 比如一个安全检查脚本
├── references/               # 可选：参考文档
│   └── review_standards.md   # 比如团队的审查标准文档
└── assets/                   # 可选：模板、资源文件
    └── report_template.md    # 比如审查报告的输出模板
```
SKILL.md 的内容分两部分。顶部是一段 YAML 格式的元数据，叫 frontmatter，声明这个 Skill 的名字和一句话描述

## SKILL 渐进式加载
1. Agent 启动的时候，只加载每个 Skill 的 name 和 description 这两个字段
2. 当用户提了一个任务，Agent 判断「这个任务这个 Skill 相关」， SKILL.md 正文完整加载进来，读取里面的详细指令。不相关的 Skill 始终不会被加载，不浪费一个 token。
3. 执行过程中，如果指令里提到了具体资源，比如scripts/assets/references「使用 assets/report_template.md 的模板」，Agent 才会在那个时刻去读取这个模板文件。参考文档、脚本这些辅助资源也是一样，用到的时候才加载。


# 项目SKILL

## 1，Skill 的目录结构

## 2，解析SKILL
parse_skill_file： 解析 SKILL.md 文件并提取元数据。如果解析成功，则返回**技能对象**；否则返回 None

允许字段：
```
ALLOWED_FRONTMATTER_PROPERTIES = {
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

会变为技能对象：
```python
@dataclass
class Skill:
    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path
    category: str
    enabled: bool = False
```

提供虚拟路径：get_container_file_path()，

```
真实文件：C:/Users/BAI/Desktop/project/skills/public/bootstrap/SKILL.md
在prompt会显式为：/mnt/skills/public/bootstrap/SKILL.md
这个虚拟路径后续由沙箱文件工具映射回本地真实路径。
```

## 3，Skill 如何加载
```
入口：
load_skills(enabled_only=False)

流程：

确定 skills 根目录
  -> 扫描 skills/public 和 skills/custom
  -> 递归查找 SKILL.md
  -> parse_skill_file()
  -> 得到 Skill 对象
  -> 读取 extensions_config.json 判断 enabled
  -> enabled_only=True 时过滤未启用技能
  -> 按 name 排序返回

技能路径来自 config.yaml：
skills:
  path: C:/Users/BAI/Desktop/project/skills
  container_path: ../skills
```
## Skill 如何进入 Agent
通过系统提示注入的，

```
用户提出任务
  -> Agent 从 system_prompt 看到 available_skills
  -> 判断某个 skill 相关
  -> 调用 read_file 读取 /mnt/skills/.../SKILL.md
  -> SKILL.md 里可能要求继续读取 references/templates/scripts
  -> Agent 逐步读取这些资源
  -> 按技能说明完成任务
```
## Skill Prompt 缓存

启动 Agent 前，agent.py (line 369) 会预热：warm_enabled_skills_cache()
如果技能发生变化，比如 Agent 通过 skill_manage 创建/编辑了 skill，会调用：
refresh_skills_system_prompt_cache_async()

## SKILL 创建
第一种：Agent 自己通过 skill_manage 工具创建或修改。工具定义在 skill_manage_tool.py (line 213)：
```
create
patch
edit
delete
write_file
remove_file
```

调用流程
```
模型调用 skill_manage(action="create", name="xxx", content="...")
  -> validate_skill_name()
  -> validate_skill_markdown_content()
  -> scan_skill_content()
  -> 写入 skills/custom/<name>/SKILL.md
  -> 记录 history
  -> 刷新 skills prompt cache
```

第二种：安装 .skill 压缩包。逻辑在 installer.py (line 1)：

```
检查 .skill 后缀
  -> 安全解压 ZIP
  -> 防路径穿越 / symlink / zip bomb
  -> 找到 SKILL.md
  -> 校验 front matter
  -> 复制到 skills/custom/<skill_name>
```


