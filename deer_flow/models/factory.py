import logging
import os

from langchain.chat_models import BaseChatModel

# from ..config.app_config import get_app_config
# from ..reflection.resolvers import resolve_class
from config.app_config import get_app_config
from reflection.resolvers import resolve_class
from tracing.factory import build_tracing_callbacks

logger = logging.getLogger(__name__)


def _deep_merge_dicts(base: dict | None, override: dict) -> dict:
    """Recursively merge two dictionaries without mutating the inputs."""
    merged = dict(base or {})
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged

# def create_chat_model(name: str | None = None, thinking_enabled: bool = False, **kwargs) -> BaseChatModel:
#     """
#     创建单一聊天模型实例，默认使用qwen3.5-plus
#     """
#     model_name = name or "qwen3.5-plus"

#     # Use DashScope OpenAI-compatible endpoint for Qwen models.
#     base_url = kwargs.pop("base_url", None) or os.getenv("OPENAI_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
#     api_key = kwargs.pop("api_key", None) or os.getenv("DASHSCOPE_API_KEY")
#     if not api_key:
#         raise ValueError("DASHSCOPE_API_KEY is required to use qwen models.")

#     try:
#         from langchain_openai import ChatOpenAI
#     except ImportError as exc:
#         raise ImportError("Missing dependency: langchain-openai. Install it before creating chat models.") from exc

#     extra_body = dict(kwargs.pop("extra_body", {}) or {})
#     if thinking_enabled:
#         extra_body["enable_thinking"] = True

#     if model_name != "qwen3.5-plus":
#         logger.warning("Model '%s' is not explicitly configured; using DashScope-compatible ChatOpenAI init.", model_name)

#     return ChatOpenAI(
#         model=model_name,
#         api_key=api_key,
#         base_url=base_url,
#         extra_body=extra_body or None,
#         **kwargs,
#     )

def create_chat_model(name: str | None = None, thinking_enabled: bool = False, **kwargs) -> BaseChatModel:
    """Create a chat model instance from the config.

    Args:
        name: The name of the model to create. If None, the first model in the config will be used.
        通用的聊天模型工厂函数，核心目标：基于配置驱动动态创建符合 LangChain 规范的聊天模型实例
    Returns:
        A chat model instance.
        BaseChatModel（LangChain 所有聊天模型的抽象基类），保证多模型接口统一
    """
    config = get_app_config()
    if name is None:
        name = config.models[0].name
    # 根据模型名提取该模型的专属配置
    model_config = config.get_model_config(name)
    # model_config: name='minimax-m2.7' display_name='MiniMax M2.7' description=None  model='MiniMax-M2.7' use_responses_api=None output_version=None supports_thinking=True supports_reasoning_effort=False when_thinking_enabled=None supports_vision=True thinking=None
    # base_url='https://api.minimaxi.com/v1' request_timeout=600.0 max_retries=2 max_tokens=4096 temperature=1.0
    # use='langchain_openai:ChatOpenAI'
    
    # print(f"model_config: {model_config}")
    
    if model_config is None:
        raise ValueError(f"Model {name} not found in config") from None

    model_class = resolve_class(model_config.use, BaseChatModel)
    model_settings_from_config = model_config.model_dump(
        exclude_none=True,
        exclude={
            "use",
            "name",
            "display_name",
            "description",
            "supports_thinking",
            "supports_reasoning_effort",
            "when_thinking_enabled",
            "thinking",
            "supports_vision",
        },
    )
    # Compute effective when_thinking_enabled by merging in the `thinking` shortcut field.
    # The `thinking` shortcut is equivalent to setting when_thinking_enabled["thinking"].

    # 合并「深度思考模式」的配置.（when_thinking_enabled 是完整配置，thinking 是快捷配置）

    has_thinking_settings = (model_config.when_thinking_enabled is not None) or (model_config.thinking is not None)
    effective_wte: dict = dict(model_config.when_thinking_enabled) if model_config.when_thinking_enabled else {}

    if model_config.thinking is not None:
        merged_thinking = {**(effective_wte.get("thinking") or {}), **model_config.thinking}
        effective_wte = {**effective_wte, "thinking": merged_thinking}
    if thinking_enabled and has_thinking_settings:
        if not model_config.supports_thinking:
            raise ValueError(f"Model {name} does not support thinking. Set `supports_thinking` to true in the `config.yaml` to enable thinking.") from None
        if effective_wte:
            model_settings_from_config.update(effective_wte)

    # 用户关闭深度思考模式时，适配不同模型的「思考禁用逻辑」
    if not thinking_enabled and has_thinking_settings:
        if effective_wte.get("extra_body", {}).get("thinking", {}).get("type"):
            # OpenAI-compatible gateway: thinking is nested under extra_body
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"thinking": {"type": "disabled"}},
            )
            model_settings_from_config["reasoning_effort"] = "minimal"
        # TODO: 暂不考虑
        # elif disable_chat_template_kwargs := _vllm_disable_chat_template_kwargs(effective_wte.get("extra_body", {}).get("chat_template_kwargs") or {}):
        #     # vLLM uses chat template kwargs to switch thinking on/off.
        #     model_settings_from_config["extra_body"] = _deep_merge_dicts(
        #         model_settings_from_config.get("extra_body"),
        #         {"chat_template_kwargs": disable_chat_template_kwargs},
        #     )
        elif effective_wte.get("thinking", {}).get("type"):
            # Native langchain_anthropic: thinking is a direct constructor parameter
            model_settings_from_config["thinking"] = {"type": "disabled"}

    # For Codex Responses API models: map thinking mode to reasoning_effort
    # 针对 Codex 模型（OpenAI 代码模型）的专属适配 
    # TODO：暂不考虑   
    # from models.openai_codex_provider import CodexChatModel

    # if issubclass(model_class, CodexChatModel):
    #     # The ChatGPT Codex endpoint currently rejects max_tokens/max_output_tokens.
    #     model_settings_from_config.pop("max_tokens", None)

    #     # Use explicit reasoning_effort from frontend if provided (low/medium/high)
    #     explicit_effort = kwargs.pop("reasoning_effort", None)
    #     if not thinking_enabled:
    #         model_settings_from_config["reasoning_effort"] = "none"
    #     elif explicit_effort and explicit_effort in ("low", "medium", "high", "xhigh"):
    #         model_settings_from_config["reasoning_effort"] = explicit_effort
    #     elif "reasoning_effort" not in model_settings_from_config:
    #         model_settings_from_config["reasoning_effort"] = "medium"

    # **kwargs（临时参数）优先级 > **model_settings_from_config（配置文件参数），支持动态覆盖
    model_instance = model_class(**kwargs, **model_settings_from_config)

    # model_instance: client=<openai.resources.chat.completions.completions.Completions object at 0x000001CB72553200> async_client=<openai.resources.chat.completions.completions.AsyncCompletions object at 0x000001CB73C87380> root_client=<openai.OpenAI object at 0x000001CB7179E600> root_async_client=<openai.AsyncOpenAI object at 0x000001CB7284C8C0> model_name='qwen3.5-plus' temperature=0.7 model_kwargs={} openai_api_key=SecretStr('**********') openai_api_base='https://dashscope.aliyuncs.com/compatible-mode/v1' max_tokens=8192
    # print(f"model_instance: {model_instance}")

    # model_class: <class 'langchain_openai.chat_models.base.ChatOpenAI'>
    # model_settings_from_config: {'model': 'qwen3.5-plus', 'api_key': 'sk-ae17d41aa05c485eb7b85b8d9a0cc606', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'max_tokens': 8192, 'temperature': 0.7}
    # print(f"model_class: {model_class}, model_settings_from_config: {model_settings_from_config}")
    
    # build_tracing_callbacks(): 构建链路追踪 / 日志回调（如 LangSmith、自定义日志）
    callbacks = build_tracing_callbacks()
    if callbacks:
        existing_callbacks = model_instance.callbacks or []
        model_instance.callbacks = [*existing_callbacks, *callbacks]
        logger.debug(f"Tracing attached to model '{name}' with providers={len(callbacks)}")
    return model_instance