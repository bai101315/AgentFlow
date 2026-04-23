# 模型选择与自定义指南

## 概述
DeerFlow 项目支持通过配置文件和动态工厂函数来自定义选择和初始化聊天模型。核心目标是提供灵活的模型管理机制，支持多种模型的动态加载与配置。

## 模型配置文件
模型的基本配置存储在 `config.yaml` 文件中，具体由 `AppConfig` 类加载。以下是模型配置的关键字段：

- **name**: 模型的唯一标识符。
- **display_name**: 模型的显示名称。
- **description**: 模型的描述信息。
- **use**: 模型提供者的类路径（例如 `langchain_openai.ChatOpenAI`）。
- **model**: 模型名称（例如 `minimax-m2.7`）。
- **supports_thinking**: 是否支持深度思考模式。
- **thinking**: 深度思考模式的快捷配置。
- **when_thinking_enabled**: 深度思考模式的完整配置。

### 示例配置
```yaml
models:
  - name: "minimax-m2.7"
    display_name: "MiniMax M2.7"
    description: "支持深度思考的高性能模型"
    use: "langchain_openai.ChatOpenAI"
    model: "MiniMax-M2.7"
    supports_thinking: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: "enabled"
```

## 模型工厂函数
模型的创建由 `models/factory.py` 中的 `create_chat_model` 函数负责。该函数的主要功能包括：

1. **加载配置**: 通过 `get_app_config` 获取应用配置。
2. **解析模型配置**: 根据模型名称提取对应的配置。
3. **动态加载类**: 使用 `resolve_class` 动态加载模型类。
4. **深度思考模式支持**: 根据配置启用或禁用深度思考模式。
5. **构建实例**: 使用配置和动态参数创建模型实例。

#### 核心方法
1. **`create_chat_model(name: str | None = None, thinking_enabled: bool = False, **kwargs)       ->BaseChatModel`**:
   - Args： name: 模型名称(minimax-2.7); 如果没有，使用config中第一个模型名字
   - 返回：BaseChatModel（LangChain 所有聊天模型的抽象基类），保证多模型接口统一
  
2. **`config = get_app_config()`**:
   - introduce： 获取 DeerFlow 配置实例。返回一个缓存的单例实例，并在底层配置文件路径或修改时间发生变化时自动重新加载。使用 `reload_app_config()` 强制重新加载，或使用 `reset_app_config()` 清除缓存。
  
   - Args： None
   - 返回：BaseChatModel（LangChain 所有聊天模型的抽象基类），保证多模型接口统一

3. **`model_config = config.get_model_config(name)`**:
   - Args： name: 模型名字
   - 返回：  model_config: name='minimax-m2.7' display_name='MiniMax M2.7' description=None  model='MiniMax-M2.7' use_responses_api=None output_version=None supports_thinking=True supports_reasoning_effort=False when_thinking_enabled=None supports_vision=True thinking=Nonebase_url='https://api.minimaxi.com/v1' request_timeout=600.0 max_retries=2 max_tokens=4096 temperature=1.0 use='langchain_openai:ChatOpenAI'

4. **`model_class = resolve_class(model_config.use, BaseChatModel)`**:
5. **`model_instance = model_class(**kwargs, **model_settings_from_config)`**:

6. **`callbacks = build_tracing_callbacks()`**:

### 函数签名
```python
def create_chat_model(name: str | None = None, thinking_enabled: bool = False, **kwargs) -> BaseChatModel:
```

### 关键逻辑
- **默认模型**: 如果未指定模型名称，使用配置中的第一个模型。
- **深度思考模式**: 合并 `thinking` 和 `when_thinking_enabled` 配置。
- **动态参数覆盖**: 支持通过 `kwargs` 动态覆盖配置。

### 示例代码
```python
model_instance = create_chat_model(name="minimax-m2.7", thinking_enabled=True)
```

## 主代理集成
主代理通过 `agents/lead_agent/agent.py` 中的 `make_lead_agent` 函数集成模型。

### 函数签名
```python
def make_lead_agent(config: RunnableConfig):
```

### 关键逻辑
1. **读取配置**: 从 `RunnableConfig` 中提取模型名称和深度思考模式。
2. **创建模型**: 调用 `create_chat_model` 创建模型实例。
3. **应用提示模板**: 使用 `apply_prompt_template` 设置系统提示。

### 示例代码
```python
agent = make_lead_agent(config={
    "configurable": {
        "model_name": "minimax-m2.7",
        "thinking_enabled": True
    }
})
```

## 总结
DeerFlow 项目通过配置驱动和动态工厂函数实现了灵活的模型选择机制。用户可以通过修改配置文件或调用工厂函数轻松切换模型，并根据需求启用高级功能（如深度思考模式）。