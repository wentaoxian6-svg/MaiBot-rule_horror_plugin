from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase

from .common import ConfigDefaults


def _default_llm_models() -> list["LLMModelSectionConfig"]:
    return [
        LLMModelSectionConfig(
            name="deepseek-v4-pro",
            enabled=True,
            api_url="https://api.deepseek.com/chat/completions",
            api_key="",
            temperature=1.0,
            max_tokens=8000,
            timeout=180,
            headers={},
            extra_body={
                "reasoning_effort": "high",
                "thinking": {"type": "enabled"},
            },
        )
    ]


def _default_npc_sim_models() -> list["LLMModelSectionConfig"]:
    return [
        LLMModelSectionConfig(
            name="deepseek-ai/DeepSeek-V4-Flash",
            enabled=True,
            api_url="https://api.siliconflow.cn/v1/chat/completions",
            api_key="",
            temperature=0.7,
            max_tokens=8000,
            timeout=180,
            headers={},
            extra_body={},
        )
    ]


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用规则怪谈插件")
    config_version: str = Field(default="2.3.0", description="配置文件版本")
    auto_save_interval: int = Field(default=ConfigDefaults.AUTO_SAVE_INTERVAL, description="自动保存间隔(秒)")
    font_path: str = Field(default="", description="图片渲染使用的字体文件路径（留空自动选择）")


class LLMModelSectionConfig(PluginConfigBase):
    """单模型配置。"""

    name: str = Field(default="deepseek-v4-pro", description="模型名称")
    enabled: bool = Field(default=True, description="是否启用该模型")
    api_url: str = Field(default="", description="模型专属 API 地址")
    api_key: str = Field(default="", description="模型专属 API 密钥")
    temperature: float = Field(default=1.0, description="模型专属生成随机性")
    max_tokens: int = Field(default=8000, description="模型专属最大输出 token 数")
    timeout: int = Field(default=180, description="模型专属请求超时时间")
    headers: dict[str, object] = Field(default_factory=dict, description="模型专属附加请求头")
    extra_body: dict[str, object] = Field(default_factory=dict, description="模型专属附加请求体")


class LLMSectionConfig(PluginConfigBase):
    """LLM 配置。"""

    __ui_label__ = "LLM"
    __ui_order__ = 1

    api_url: str = Field(default="https://api.deepseek.com/chat/completions", description="默认 API 地址")
    api_key: str = Field(default="", description="默认 API 密钥")
    model_list: list[str] = Field(default_factory=list, description="简化模式模型列表")
    temperature: float = Field(default=ConfigDefaults.TEMPERATURE, description="默认生成随机性")
    max_concurrent: int = Field(default=ConfigDefaults.MAX_CONCURRENT_REQUESTS, description="最大并发请求数")
    max_tokens: int = Field(default=8000, description="默认最大输出 token 数")
    max_retries: int = Field(default=3, description="最大重试次数")
    timeout: int = Field(default=180, description="默认请求超时时间")
    default_headers: dict[str, object] = Field(default_factory=dict, description="所有模型共享的附加请求头")
    default_body: dict[str, object] = Field(default_factory=dict, description="所有模型共享的附加请求体")
    models: list[LLMModelSectionConfig] = Field(default_factory=_default_llm_models, description="完整模型配置")


class NPCSimSectionConfig(PluginConfigBase):
    """NPC 模拟配置。"""

    __ui_label__ = "NPC 模拟"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="是否启用 NPC 模拟系统")
    trigger_on_every_action: bool = Field(default=True, description="是否在每次有效行动后触发一次 NPC 模拟")
    room_hearing_radius: int = Field(default=1, description="房间级听觉传播半径")
    max_event_history: int = Field(default=20, description="最多保留的 NPC 事件历史数量")
    api_url: str = Field(default="", description="默认 API 地址；留空时回退主 LLM 配置")
    api_key: str = Field(default="", description="默认 API 密钥；留空时回退主 LLM 配置")
    model_list: list[str] = Field(default_factory=list, description="简化模式模型列表")
    temperature: float = Field(default=0.7, description="默认生成随机性")
    max_concurrent: int = Field(default=ConfigDefaults.MAX_CONCURRENT_REQUESTS, description="最大并发请求数")
    max_tokens: int = Field(default=8000, description="默认最大输出 token 数")
    max_retries: int = Field(default=3, description="最大重试次数")
    timeout: int = Field(default=180, description="默认请求超时时间")
    default_headers: dict[str, object] = Field(default_factory=dict, description="所有模型共享的附加请求头")
    default_body: dict[str, object] = Field(default_factory=dict, description="所有模型共享的附加请求体")
    models: list[LLMModelSectionConfig] = Field(default_factory=_default_npc_sim_models, description="完整模型配置")


class SaveSectionConfig(PluginConfigBase):
    """存档配置。"""

    __ui_label__ = "存档"
    __ui_order__ = 3

    batch_save_interval: int = Field(default=ConfigDefaults.BATCH_SAVE_INTERVAL, description="批量保存间隔(秒)")
    max_auto_saves: int = Field(default=10, description="最大自动存档数量")
    compress_saves: bool = Field(default=True, description="是否压缩存档文件")


class RuleHorrorPluginConfig(PluginConfigBase):
    """规则怪谈插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    llm: LLMSectionConfig = Field(default_factory=LLMSectionConfig)
    npc_sim: NPCSimSectionConfig = Field(default_factory=NPCSimSectionConfig)
    save: SaveSectionConfig = Field(default_factory=SaveSectionConfig)
