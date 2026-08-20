# Hermes没有middleware，如何约束行为

所有约束逻辑都写在 while 循环、工具函数、以及直接的数据结构中，没有 hooks 注册、没有 callback chain、没有中间件拦截层。

# 死循环防护 —— 三重保险
Hermes 用三层独立机制防止 agent 无限调用工具：

## 1，硬上限 max_iterations
```python
conversation_loop.py 第 598 行
while (api_call_count < agent.max_iterations       # ← 默认 90，可配置
        and agent.iteration_budget.remaining > 0) \
        or agent._budget_grace_call:
```

达到 90 轮后，会触发一次 grace call（给模型最后一次机会输出文本而不是工具调用），然后强制退出。超限后返回给用户

## IterationBudget 线程安全计数器

agent/iteration_budget.py——只有 62 行，纯 Python 对象：

父 agent 90 轮，子 agent 50 轮（delegation.max_iterations），各算各的互不干扰。execute_code（Python 代码工具调用）走 refund() 不计入预算

```python
class IterationBudget:
    def consume(self) -> bool:
        with self._lock:            # 线程安全
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:       # execute_code 不计入预算
        with self._lock:
            if self._used > 0:
                self._used -= 1
```

## ToolCallGuardrailController —— 模式检测
这是最智能的一层。agent/tool_guardrails.py 全部 475 行，没有框架。核心逻辑：

```python
class ToolCallGuardrailController:
    def before_call(self, tool_name, args):
        # 检测 1：完全相同的调用失败 N 次
        if exact_failure_count >= block_after:  # 默认 5
            return ToolGuardrailDecision(action="block",
                message="这个调用已经用完全相同参数失败了5次，停止重试")

        # 检测 2：idempotent 工具反复返回相同结果
        if is_idempotent(tool_name) and repeat_count >= no_progress_after:
            return ToolGuardrailDecision(action="block",
                message="read_file 返回相同结果3次了，停止重复")

    def after_call(self, tool_name, args, result):
        # 失败时更新计数器 + 给出恢复提示
        if failed:
            return ToolGuardrailDecision(action="warn",
                message="terminal 失败了3次，先检查 pwd && ls -la 诊断一下")

        # 成功时：对 idempotent 工具做结果哈希比对
        # 连续相同结果 → 无进展检测
```

## 当前的配置（从 config.yaml）
```yaml
tool_loop_guardrails:
    warnings_enabled: true     # 警告开启（注入到 tool result 的提示文本）
    hard_stop_enabled: false   # 硬停止关闭（不会强行阻断）
    warn_after:
    exact_failure: 2         # 相同调用参数失败 2 次 → 警告
    same_tool_failure: 3     # 同一工具失败 3 次 → 警告  
    idempotent_no_progress: 2 # 只读工具无进展 2 次 → 警告
    hard_stop_after:           # 以下只有 hard_stop_enabled=true 才生效
    exact_failure: 5
    same_tool_failure: 8
    idempotent_no_progress: 5
```

# 工具调用失败 —— 错误分类 + 渐进式恢复

工具调用失败不是统一处理的，而是按错误类别分路径恢复。看 conversation_loop.py 的 while 循环里的错误处理：

API 调用失败:
    ├── 空响应 (empty content) → 3 次 retry
    │   ├── thinking prefill 重试（强制要求模型思考）
    │   ├── post-tool empty 重试（工具执行后的空响应，用不同策略）
    │   ├── 1 次后 → 切换到 fallback provider
    │   └── 全部失败 → 给用户返回错误
    │
    ├── 无效 tool call name → 在 tool result 中注入错误，让 LLM 看到并修改
    │
    ├── tool call arguments JSON 损坏 → 特殊清理逻辑
    │   ├── Unicode surrogate 清理
    │   ├── Non-ASCII 降级清理
    │   └── pattern/format 关键字剥离 (llama.cpp 兼容)
    │
    ├── HTTP 错误码诊断:
    │   ├── 429 → rate limited，切换 fallback
    │   ├── 524 → Cloudflare timeout，重试
    │   ├── 503/529 → 上游过载，重试
    │   └── 未知 → 指数退避重试
    │
    ├── Provider 认证问题:
    │   ├── Codex OAuth 过期 → 不同 provider 各自的重试逻辑
    │   ├── Anthropic OAuth → 同上
    │   └── Nous Portal → nous_rate_guard 预检
    │
    └── 全部 retry 耗尽 → fallback provider 链
        └── 都没了 → returned to user with error
    
    
重试用的是抖动指数退避（agent/retry_utils.py）：
    
```python
wait_time = jittered_backoff(retry_count, base_delay=5.0, max_delay=120.0)
retry 0 → ~5s, retry 1 → ~10s, retry 2 → ~20s, retry 3 → ~40s
睡眠时每 0.2s 检查 interrupt_requested，随时可被用户 /stop 打断
``` 





