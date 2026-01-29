"""配置管理模块 - 使用 Pydantic 进行配置验证和管理"""
from __future__ import annotations

import os
from typing import List
from pydantic import BaseModel, Field, field_validator


class LLMConfig(BaseModel):
    """LLM API 配置"""
    api_url: str = Field(
        default="http://rinkoai.com/v1/chat/completions",
        description="LLM API 地址 (OpenAI格式)"
    )
    api_key: str = Field(
        default="",
        description="LLM API 密钥"
    )
    model_list: List[str] = Field(
        default=["deepseek-ai/DeepSeek-V3"],
        description="LLM模型列表，按优先级排序"
    )
    current_model_index: int = Field(
        default=0,
        ge=0,
        description="当前使用的模型索引"
    )
    temperature: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="LLM 生成文本的随机性"
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="最大重试次数"
    )
    timeout: int = Field(
        default=120,
        ge=30,
        le=300,
        description="API 调用超时时间(秒)"
    )
    max_concurrent: int = Field(
        default=10,
        ge=1,
        le=50,
        description="最大并发请求数"
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v or v == "YOUR_API_KEY":
            # 尝试从环境变量读取
            env_key = os.getenv("LLM_API_KEY", "")
            if env_key:
                return env_key
        return v


class PluginConfig(BaseModel):
    """插件基础配置"""
    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="2.1.0", description="配置文件版本")
    scene_view_mode: str = Field(
        default="2d",
        pattern="^(2d|3d)$",
        description="场景剖面图模式：'2d' 或 '3d'"
    )
    plotly_3d_output_format: str = Field(
        default="static",
        pattern="^(static|interactive)$",
        description="3D模式输出格式"
    )
    enable_scene_structure_image: bool = Field(
        default=True,
        description="是否生成场景剖面图"
    )
    auto_save_interval: int = Field(
        default=30,
        ge=10,
        le=300,
        description="自动保存间隔(秒)"
    )


class EnvironmentConfig(BaseModel):
    """环境演变系统配置"""
    enabled: bool = Field(default=True, description="是否启用环境演变系统")
    api_url: str = Field(default="", description="环境演变系统LLM API地址，留空使用主配置")
    api_key: str = Field(default="", description="环境演变系统LLM API密钥，留空使用主配置")
    model_list: List[str] = Field(
        default=["deepseek-ai/DeepSeek-V3"],
        description="环境演变系统LLM模型列表"
    )
    current_model_index: int = Field(default=0, ge=0)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)


class SaveConfig(BaseModel):
    """存档配置"""
    batch_save_interval: int = Field(
        default=30,
        ge=5,
        le=120,
        description="批量保存间隔(秒)"
    )
    max_auto_saves: int = Field(
        default=10,
        ge=3,
        le=50,
        description="最大自动存档数量"
    )
    compress_saves: bool = Field(
        default=True,
        description="是否压缩存档文件"
    )


class Config(BaseModel):
    """完整配置"""
    plugin: PluginConfig = Field(default_factory=PluginConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    save: SaveConfig = Field(default_factory=SaveConfig)


# 全局配置实例
_config_instance: Config | None = None


def get_config() -> Config:
    """获取全局配置实例"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def set_config(config: Config) -> None:
    """设置全局配置实例"""
    global _config_instance
    _config_instance = config
