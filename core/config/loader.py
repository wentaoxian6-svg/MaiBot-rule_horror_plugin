"""配置加载器 - 从 TOML 文件加载配置。"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import tomllib

from .settings import (
    Config,
    LLMConfig,
    LLMModelConfig,
    NPCSimConfig,
    PluginConfig,
    SaveConfig,
    get_config,
    set_config,
)

logger = logging.getLogger(__name__)


def _deep_merge(dst: dict[str, object], src: object) -> None:
    if not isinstance(src, dict):
        return
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value


def _extract_models(raw_section: dict[str, object]) -> list[LLMModelConfig]:
    models: list[LLMModelConfig] = []
    raw_models = raw_section.get("models", [])
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict):
                models.append(LLMModelConfig(**item))
    return models


def _build_llm_config(raw_llm: object) -> LLMConfig:
    llm_data = dict(raw_llm) if isinstance(raw_llm, dict) else {}

    llm_base = {
        "api_url": llm_data.get("api_url", "https://api.deepseek.com/chat/completions"),
        "api_key": llm_data.get("api_key", ""),
        "model_list": llm_data.get("model_list", []),
        "current_model_index": llm_data.get("current_model_index", 0),
        "temperature": llm_data.get("temperature", 1.0),
        "max_concurrent": llm_data.get("max_concurrent", 10),
        "max_tokens": llm_data.get("max_tokens", 8000),
        "max_retries": llm_data.get("max_retries", 3),
        "timeout": llm_data.get("timeout", 180),
        "default_headers": llm_data.get("default_headers", {}),
        "default_body": llm_data.get("default_body", {}),
        "models": _extract_models(llm_data),
    }
    return LLMConfig(**llm_base)


def _build_npc_sim_config(raw_npc_sim: object) -> NPCSimConfig:
    npc_sim_data = dict(raw_npc_sim) if isinstance(raw_npc_sim, dict) else {}

    npc_sim_base = {
        "enabled": npc_sim_data.get("enabled", True),
        "trigger_on_every_action": npc_sim_data.get("trigger_on_every_action", True),
        "room_hearing_radius": npc_sim_data.get("room_hearing_radius", 1),
        "max_event_history": npc_sim_data.get("max_event_history", 20),
        "tick_idle_threshold_seconds": npc_sim_data.get("tick_idle_threshold_seconds", 60),
        "tick_idle_scale_factor": npc_sim_data.get("tick_idle_scale_factor", 1.0),
        "tick_max_minutes_per_tick": npc_sim_data.get("tick_max_minutes_per_tick", 15),
        "api_url": npc_sim_data.get("api_url", ""),
        "api_key": npc_sim_data.get("api_key", ""),
        "model_list": npc_sim_data.get("model_list", []),
        "current_model_index": npc_sim_data.get("current_model_index", 0),
        "temperature": npc_sim_data.get("temperature", 0.7),
        "max_concurrent": npc_sim_data.get("max_concurrent", 10),
        "max_tokens": npc_sim_data.get("max_tokens", 8000),
        "max_retries": npc_sim_data.get("max_retries", 3),
        "timeout": npc_sim_data.get("timeout", 180),
        "default_headers": npc_sim_data.get("default_headers", {}),
        "default_body": npc_sim_data.get("default_body", {}),
        "models": _extract_models(npc_sim_data),
    }
    return NPCSimConfig(**npc_sim_base)


def apply_plugin_config_overrides(overrides: Mapping[str, object]) -> Config:
    current = get_config()
    base = current.model_dump()
    _deep_merge(base, dict(overrides))
    config = Config(**base)
    set_config(config)
    return config


def load_config_from_file(config_path: str | Path | None = None) -> Config:
    """从 TOML 文件加载配置。"""

    if config_path is None:
        config_file = Path(__file__).parent.parent.parent / "config.toml"
    else:
        config_file = Path(config_path)

    if not config_file.exists():
        logger.warning("配置文件不存在: %s，使用默认配置", config_file)
        config = Config()
        set_config(config)
        return config

    try:
        with config_file.open("rb") as file:
            config_data = tomllib.load(file)

        logger.info("从 %s 加载配置", config_file)
        config = Config(
            plugin=PluginConfig(**config_data.get("plugin", {})),
            llm=_build_llm_config(config_data.get("llm", {})),
            npc_sim=_build_npc_sim_config(config_data.get("npc_sim", {})),
            save=SaveConfig(**config_data.get("save", {})),
        )
        errors = validate_config(config)
        for error in errors:
            logger.warning("配置验证提示: %s", error)

        set_config(config)
        return config
    except Exception as exc:
        logger.error("加载配置文件失败: %s", exc, exc_info=True)
        logger.warning("使用默认配置")
        config = Config()
        set_config(config)
        return config


def validate_config(config: Config) -> list[str]:
    """验证配置的有效性。"""

    errors: list[str] = []
    enabled_models = [model for model in config.llm.models if model.enabled]

    if enabled_models:
        for model in enabled_models:
            if not model.name.strip():
                errors.append("存在未填写名称的启用模型配置")
            resolved_api_url = str(model.api_url or config.llm.api_url).strip()
            if not resolved_api_url:
                errors.append(f"模型 {model.name!r} 缺少 api_url")
    elif not config.llm.model_list:
        errors.append("未配置可用模型：请填写 model_list 或 [[llm.models]]")

    if not config.llm.api_key and not any(str(model.api_key).strip() for model in enabled_models):
        errors.append("LLM API 密钥为空，插件可加载，但实际调用模型前需要补充密钥")

    if config.npc_sim.enabled:
        npc_enabled_models = [model for model in config.npc_sim.models if model.enabled]
        if npc_enabled_models:
            for model in npc_enabled_models:
                resolved_api_url = str(model.api_url or config.npc_sim.api_url or config.llm.api_url).strip()
                if not resolved_api_url:
                    errors.append(f"NPC 模型 {model.name!r} 缺少 api_url")
        elif not config.npc_sim.model_list and not config.llm.models and not config.llm.model_list:
            errors.append("NPC 模拟未配置独立模型，也无法回退主 llm 模型")

    return errors


def reload_config(config_path: str | Path | None = None) -> Config:
    """重新加载配置文件。"""

    return load_config_from_file(config_path)
