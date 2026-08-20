|            | Hermes-Agent                   | Pi                | OpenCode        | DeerFlow                  |
|------------|--------------------------------|-------------------|-----------------|---------------------------|
| 作者       | Nous Research                  | earendil-works    | Anomaly         | ByteDance                 |
| 语言       | Python                         | TypeScript        | Go              | Python                    |
| Stars      | 215K                         | 70K               | 160K            | 47K+                      |
| 定位       | 通用自主Agent平台              | 极简终端编码Agent | 纯终端编码Agent | 多Agent超级编排框架       |
| 核心场景   | 编码+研究+自动化+多平台聊天    | 终端编码          | 终端/IDE编码    | 长周期深度研究+自动化     |
| 多平台网关 | ✅ Telegram/Discord/Slack等15+ | ❌                | ❌              | ✅ Telegram/Slack/Feishu  |
| 持久记忆   | ✅ 跨会话                      | ❌                | ❌              | ✅ LangGraph checkpointer |
| 技能系统   | ✅ 自学习                      | ✅ 手动           | ✅ 自定义命令   | ✅                        |
| 沙箱执行   | execute_code UDS沙箱           | 无内建→需Docker   | 无内建          | ✅ Docker容器隔离         |
| 子Agent    | ✅ delegate_task               | ❌                | ✅ 多会话并行   | ✅ 子Agent编排            |
| 模型提供商 | 20+（任意切换）                | 多提供商统一API   | 75+             | LangChain生态             |
| MCP支持    | ✅                             | ✅                | ✅              | ✅                        |
| IDE集成    | ACP协议                        | 无                | ✅ VS Code/Zed  | ✅ Cursor/Windsurf        |
| 安装复杂度 | 中（curl一键）                 | 低（npm全局）     | 低（curl一键）  | 高（需Docker/多组件）     |
| 运行模式   | CLI + 网关后台服务             | CLI               | CLI + 桌面App   | Web UI + CLI              |

逐个分析

1. Hermes-Agent（Nous Research）—— 通用自主Agent平台

侧重点："一个能在任何地方运行、记住你的Agent"。

它不是纯编码工具。核心卖点是：

- 跨会话持久记忆：记住你是谁、你的偏好、项目上下文。其他三个都没有这个。
- 技能自学习：Agent 完成任务后可以自动把学到的流程保存为技能文档，下次自动加载。这是 Knowledge Accumulation 的思路。
- 多平台网关：同一个 Agent 通过 Telegram/Discord/Slack 用，全工具可用，不只聊天。
- Provider-agnostic：20+ 提供商随便切，甚至在对话中途换模型。
- Profiles：隔离的多个 Agent 实例，各自有独立的技能/记忆/配置。

最适合：需要持久上下文的研发工程师、需要跨平台统一的个人助理、需要自定义扩展的高级用户。

2. Pi（earendil-works）—— 极简终端编码Agent

侧重点："最小化、可扩展的终端编码助手"。

核心哲学是极简主义：
- 默认只有 4 个工具：read、write、edit、bash
- 没有内建的权限系统、子Agent、计划模式——一切靠你自己扩展
- TypeScript monorepo，拆成 pi-ai（统一LLM API）、pi-agent-core（运行时）、pi-coding-agent（CLI）
- 自扩展：你可以让 Pi 自己写自己的扩展（TypeScript），然后立即加载使用
- 70K stars，社区活跃度极高

核心理念是：给你最少的脚手架，你把需要的功能自己加上。不替你做决定。

最适合：喜欢掌控一切、想要最小脚手架、不想被框架绑架的开发者。

3. OpenCode（Anomaly）—— 终端/IDE纯编码Agent

侧重点："最好的终端编码体验"。

- Go 语言写的，启动快、性能好
- 160K stars，7.5M 月活用户——这个赛道里用户量最大的
- LSP 原生集成：自动加载 Language Server，给 LLM 提供代码补全/跳转/诊断能力
- 多会话：同一个项目并行跑多个 Agent
- Zen 服务：提供经过 OpenCode 优化和基准测试的模型
- 桌面 App + VS Code/Zed 扩展 + 终端三端运行
- 隐私优先：不存储任何代码或上下文

核心竞争力：LSP 集成 + 用户量带来的生态 + 终端体验打磨。

最适合：想要流畅终端编码体验、已经在用 Copilot/Cursor 想替代的开发者。

4. DeerFlow（ByteDance）—— 多Agent超级编排框架

侧重点："长周期、多步骤、多Agent协作的深度研究"。

名字就是 Deep Exploration and Efficient Research Flow。

- 不是"帮你写代码"的工具，是"多个 Agent 协作完成复杂任务"的平台
- Docker 沙箱隔离：每个 Agent 在独立容器里跑，有自己的文件系统、终端、网络
- LangGraph 编排：图结构定义 Agent 之间的工作流——谁先干什么、结果传给谁
- 持久记忆+文件系统：Agent 可以存东西，后续 Agent 继续用
- 场景：深度研究 → 生成报告 → 生成 PPT → 生成播客，全自动链路
- 子Agent + 技能 + 消息网关：跟 Hermes 更像，但底层是 LangChain/LangGraph

这是"多Agent框架"不是"编码Agent"——它和另外三个不在同一个赛道上。它编排 Agent 去做编码、研究、内容创作，而不是直接帮你敲代码。

最适合：需要多步自动化研究、报告生成、复杂工作流的团队。



为什么会有这么多AI Agent？

根本原因："AI Agent"这个词覆盖了从聊天机器人到自主系统的整个光谱，每个项目都在不同维度上做取舍。

具体来说：

1. 定位不同：Pi 和 OpenCode 是编码 Agent（帮你写代码），Hermes 是通用 Agent（编码+自动化+多平台），DeerFlow 是 Agent 框架（编排多个 Agent 做复杂任务）

2. 哲学不同：Pi 追求极简（给你最少的东西，剩下的自己来），DeerFlow 追求完备（把能给的都给你），Hermes 追求"持久性"（记住一切，越用越好），OpenCode 追求"编码体验"（LSP、多会话、分享链接）

3. 运行模式不同：CLI only vs CLI+WebUI+网关 vs CLI+桌面App+IDE扩展

4. 技术栈不同：Python vs TypeScript vs Go，各自生态不同，吸引的开发者不同

5. 用户群体不同：
    - OpenCode：7.5M 月活，面向所有开发者
    - Pi：喜欢极简和可扩展性的 TS 开发者
    - Hermes：需要持久上下文、跨平台的高级用户
    - DeerFlow：需要多Agent协作的企业/团队

6. 开源生态的必然：2024-2026 年是 Agent 爆发期，每个大厂和独立团队都在押注不同的设计哲学。没有"正确"答案，只有适合不同场景的答案。



简单总结

| 如果你想要...                         | 选           |
|---------------------------------------|--------------|
| 在终端高效写代码，LSP集成             | OpenCode     |
| 极简可扩展的终端编码助手              | Pi           |
| 跨平台+持久记忆+越用越聪明的通用Agent | Hermes-Agent |
| 多Agent协作完成长周期深度研究         | DeerFlow     |

它们不完全是竞争关系——很多开发者会同时装好几种，根据不同任务切换使用。