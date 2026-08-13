# AGENTS.md — AgentFlow 项目规则

本文件为项目内最高优先级规则,任何在该仓库内工作的 agent 必须遵守。

## ⭐ 最高优先级:证据优先(Evidence-first)

任何**操作**或**结论**,必须先阅读相关源码或数据,提供可靠性支撑,方可执行或给出。

具体要求:

1. **结论必须有依据** — 回答"为什么/是什么/是否有问题"时,必须引用:
   - 源码位置(`path:line`)与关键代码摘录;
   - 实际数据(日志、事件、数据库、配置文件内容);
   - 真实命令输出(测试结果、lint 结果、运行输出)。
   禁止凭印象、推测或"我记得"下结论。

2. **操作前必须读代码** — 修改、删除、诊断前,先读目标文件及相关调用链,
   确认符号/接口/行为的真实形态,不得假设。

3. **验证必须真实** — 声称"已修复/已通过"必须有对应工具输出支撑
   (pytest 输出、命令 exit code、文件落盘确认等)。禁止编造结果。

4. **先核实后评判** — 对第三方报告、他人结论、AI 生成的分析,先逐条对照
   当前代码/数据核实,再确认或反驳,不直接采信。

5. **证据不足时明确说明** — 无法读取数据或被拒绝时,明确说明证据缺口,
   给出自查路径,不得用推断填补。

## 项目背景(供快速定位)

- AgentFlow:DeerFlow 风格 agent 框架,backend/ 含 self-improving 机制
  (skill_manage 工具 / background review agent / curator / memory 中间件)。
- 关键目录:backend/agents/(中间件、review_agent、memory)、
  backend/skill/(manager/curator/usage/validation)、backend/observability/。
- 配置:config.yaml;数据目录:.agentflow/(observability.db、events.jsonl 等)。
