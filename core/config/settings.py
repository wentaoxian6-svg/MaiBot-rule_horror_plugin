"""配置管理模块 - 使用 Pydantic 进行配置验证和管理。"""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field, field_validator


JsonDict = dict[str, Any]


def _resolve_api_key(value: str, env_name: str = "LLM_API_KEY") -> str:
    if not value or value == "YOUR_API_KEY":
        env_key = os.getenv(env_name, "")
        if env_key:
            return env_key
    return value


def _default_llm_models() -> list["LLMModelConfig"]:
    return [
        LLMModelConfig(
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


def _default_npc_sim_models() -> list["LLMModelConfig"]:
    return [
        LLMModelConfig(
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


class LLMModelConfig(BaseModel):
    """单个模型的完整配置。"""

    name: str = Field(default="deepseek-v4-pro", description="模型名称")
    enabled: bool = Field(default=True, description="是否启用该模型")
    api_url: str = Field(default="", description="模型专属 API 地址")
    api_key: str = Field(default="", description="模型专属 API 密钥")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="模型专属随机性")
    max_tokens: int | None = Field(default=None, ge=100, le=32000, description="模型专属最大输出 token 数")
    timeout: int | None = Field(default=None, ge=30, le=300, description="模型专属超时时间(秒)")
    headers: JsonDict = Field(default_factory=dict, description="模型专属附加请求头")
    extra_body: JsonDict = Field(default_factory=dict, description="模型专属附加请求体")

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        return _resolve_api_key(value)


class ModelSectionConfig(BaseModel):
    """可直接驱动 LLMClient 的配置段。"""

    api_url: str = Field(default="https://api.deepseek.com/chat/completions", description="默认 API 地址")
    api_key: str = Field(default="", description="默认 API 密钥")
    model_list: list[str] = Field(default_factory=list, description="简化模式模型列表")
    current_model_index: int = Field(default=0, ge=0, description="兼容旧逻辑的当前模型索引")
    temperature: float = Field(default=1.0, ge=0.0, le=2.0, description="默认随机性")
    max_concurrent: int = Field(default=10, ge=1, le=50, description="最大并发请求数")
    max_tokens: int = Field(default=8000, ge=100, le=32000, description="默认最大生成 token 数")
    max_retries: int = Field(default=3, ge=1, le=10, description="最大重试次数")
    timeout: int = Field(default=180, ge=30, le=300, description="默认请求超时时间(秒)")
    default_headers: JsonDict = Field(default_factory=dict, description="全局附加请求头")
    default_body: JsonDict = Field(default_factory=dict, description="全局附加请求体")
    models: list[LLMModelConfig] = Field(default_factory=list, description="完整模型配置列表")

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        return _resolve_api_key(value)


class LLMConfig(ModelSectionConfig):
    """主流程 LLM API 配置。"""

    models: list[LLMModelConfig] = Field(default_factory=_default_llm_models, description="完整模型配置列表")


class NPCSimConfig(ModelSectionConfig):
    """NPC 模拟模型配置。"""

    api_url: str = Field(default="", description="默认 API 地址")
    api_key: str = Field(default="", description="默认 API 密钥")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="默认随机性")
    max_tokens: int = Field(default=8000, ge=100, le=32000, description="默认最大生成 token 数")
    models: list[LLMModelConfig] = Field(default_factory=_default_npc_sim_models, description="完整模型配置列表")
    enabled: bool = Field(default=True, description="是否启用 NPC 模拟系统")
    trigger_on_every_action: bool = Field(default=True, description="是否在每次有效行动后触发 NPC 模拟")
    room_hearing_radius: int = Field(default=1, ge=0, le=3, description="房间级听觉传播半径")
    max_event_history: int = Field(default=20, ge=5, le=100, description="保留的事件历史数量")


class PluginConfig(BaseModel):
    """插件基础配置。"""

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="2.3.0", description="配置文件版本")
    auto_save_interval: int = Field(default=30, ge=10, le=300, description="自动保存间隔(秒)")
    font_path: str = Field(default="", description="图片渲染使用的字体文件路径（留空自动选择）")


class SaveConfig(BaseModel):
    """存档配置。"""

    batch_save_interval: int = Field(default=30, ge=5, le=120, description="批量保存间隔(秒)")
    max_auto_saves: int = Field(default=10, ge=3, le=50, description="最大自动存档数量")
    compress_saves: bool = Field(default=True, description="是否压缩存档文件")


class Config(BaseModel):
    """完整配置。"""

    plugin: PluginConfig = Field(default_factory=PluginConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    npc_sim: NPCSimConfig = Field(default_factory=NPCSimConfig)
    save: SaveConfig = Field(default_factory=SaveConfig)


_config_instance: Config | None = None


def get_config() -> Config:
    """获取全局配置实例。"""

    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def set_config(config: Config) -> None:
    """设置全局配置实例。"""

    global _config_instance
    _config_instance = config
