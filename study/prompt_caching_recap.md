# Prompt Caching 重构复盘

这份文档记录本次 prompt-caching 重构的思路、修改点和设计哲学，方便后续复盘和继续迭代。

## 1. 这次到底改了什么

### 1) prompt-caching 从“DeepSeek 专用补丁”变成“通用架构”

以前：
- prompt-caching 主要按 DeepSeek 的行为来设计
- 逻辑更像是针对某个 provider 的适配补丁
- 代码里会出现专门的 DeepSeek middleware、DeepSeek-only 判断

现在：
- 统一为 `PromptCacheMiddleware`
- 所有模型都走同一套“冻结 system prompt -> 写入 checkpoint -> 后续复用”的流程
- provider 差异只留在最后一层：支持显式 marker 的模型才加 `cache_control`，DeepSeek/OpenAI 依赖前缀稳定性，不额外注入标记

相关文件：
- `backend/agents/middlewares/prompt_cache_middleware.py`
- `backend/agents/middlewares/deepseek_prompt_cache_middleware.py`

### 2) system prompt 的构建方式变了

以前：
- `apply_prompt_template(...)` 是主路径
- 每轮都可能重新拼 prompt，里面带有动态内容
- `current_date`、memory、技能列表等内容更容易造成前缀变化

现在：
- `apply_prompt_template(...)` 还保留，但不再是主路径
- 新增 `build_prompt_cache_snapshot(...)`
- 把 prompt 分成两层：
  - stable/context：角色、规则、工具、skills、subagent 说明
  - volatile snapshot：memory、日期、模型信息
- 最终冻结成一个完整字符串，并存到 checkpoint

相关文件：
- `backend/agents/lead_agent/prompt.py`

### 3) thread state 增加了缓存字段

以前：
- thread state 里没有明确记录 frozen prompt

现在：
- 新增三个字段：
  - `cached_system_prompt`
  - `cached_system_prompt_signature`
  - `cached_system_prompt_created_at`
- 这样重启 `main.py` 后，仍然能从 checkpoint 恢复冻结后的 prompt

相关文件：
- `backend/agents/thread_state.py`

### 4) 模型默认值切到了 DeepSeek，但缓存不依赖 DeepSeek

以前：
- 运行路径更偏向 Qwen 兼容逻辑

现在：
- 默认模型改为 `deepseek-v4`
- 但 prompt-caching 本身是 provider-agnostic 的
- agent 自己配置模型时，仍然优先于默认模型

相关文件：
- `main.py`
- `config.yaml`
- `backend/agents/lead_agent/agent.py`

### 5) 模型工厂修掉了 `when_thinking_disabled` 泄漏问题

以前：
- `when_thinking_disabled` 会被不小心传给不接受这个参数的 client
- 运行时报错：`AsyncCompletions.create() got an unexpected keyword argument 'when_thinking_disabled'`

现在：
- `ModelConfig` 显式支持 `when_thinking_disabled`
- `factory.py` 在构造模型时会把它从通用参数里剥离，再按需要合并

相关文件：
- `backend/config/model_config.py`
- `backend/models/factory.py`

## 2. 现在的执行链路是什么

1. `make_lead_agent(...)` 创建 agent 时，先生成一个稳定的 prompt cache signature
2. `PromptCacheMiddleware` 在 `before_model` 阶段检查 thread state
3. 如果 checkpoint 里已有同 signature 的 frozen prompt，就直接复用
4. 如果没有，就重新构建一次 prompt，写入：
   - frozen prompt
   - signature
   - created_at
5. 真正发请求时，middleware 会把冻结后的 system prompt 塞给模型
6. 请求结束后，middleware 从返回值里读取 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，并写日志

## 3. 这套设计的哲学是什么

核心只有一句话：

**prompt-caching 不是某个模型的补丁，而是“系统 prompt 生命周期管理”**

所以它的原则是：

- **构建一次，冻结一次，后续复用**
- **稳定前缀优先**：role、规则、tools、skills、工作目录、subagent 说明要尽量不变
- **动态内容后置**：memory、日期、运行时状态放到后面，避免破坏前缀
- **provider 差异后移**：只有末端请求封装才考虑是否加 `cache_control`
- **checkpoint 是真相来源**：重启后从 state 恢复，而不是重新拼一遍
- **旧代码保留注释**：先注释旧逻辑，再新增实现，方便回滚和对照

## 4. 现在怎么判断它真的生效了

看三类信号就够了：

- `Frozen system prompt created` 只在首次构建时出现
- checkpoint 里能看到 `cached_system_prompt` 和 `cached_system_prompt_signature`
- 后续轮次里 `prompt_cache_hit_tokens` 开始大于 0

补充说明：
- 首轮出现 miss 很正常
- 真正的验证点是第二轮、第三轮是否复用同一 frozen prompt
- `eo-cache-status: MISS` 不等于 prompt-caching 失败，它不是同一层面的指标

## 5. 一句话总结

这次重构的本质，不是“给 DeepSeek 打一个缓存补丁”，而是把 prompt-caching 变成了 agent 的基础能力：  
**把 system prompt 当成可冻结、可恢复、可复用的运行时资产。**
