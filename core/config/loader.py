"""配置加载器 - 从TOML文件加载配置"""
from __future__ import annotations

import logging
from pathlib import Path


try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Python 3.10  # pyright: ignore[reportMissingImports]

from .settings import Config, LLMConfig, PluginConfig, EnvironmentConfig, SaveConfig, set_config

logger = logging.getLogger(__name__)


def load_config_from_file(config_path: str | Path | None = None) -> Config:
    """
    从TOML文件加载配置
    
    Args:
        config_path: 配置文件路径（可选）
    
    Returns:
        Config 对象
    """
    if config_path is None:
        # 默认配置路径
        base_dir = Path(__file__).parent.parent.parent
        config_file = base_dir / "config.toml"
    else:
        config_file = Path(config_path)
    
    if not config_file.exists():
        logger.warning(f"配置文件不存在: {config_file}，使用默认配置")
        return Config()
    
    try:
        with open(config_file, "rb") as f:
            config_data = tomllib.load(f)
        
        logger.info(f"从 {config_path} 加载配置")
        
        # 解析各个配置节
        plugin_config = PluginConfig(**config_data.get("plugin", {}))
        llm_config = LLMConfig(**config_data.get("llm", {}))
        environment_config = EnvironmentConfig(**config_data.get("environment", {}))
        save_config = SaveConfig(**config_data.get("save", {}))
        
        # 创建完整配置
        config = Config(
            plugin=plugin_config,
            llm=llm_config,
            environment=environment_config,
            save=save_config,
        )
        
        # 验证配置
        errors = validate_config(config)
        if errors:
            logger.warning(f"配置验证发现问题: {', '.join(errors)}")
        
        # 设置为全局配置
        set_config(config)
        
        return config
        
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}", exc_info=True)
        logger.warning("使用默认配置")
        return Config()


def validate_config(config: Config) -> list[str]:
    """
    验证配置的有效性
    
    Args:
        config: 配置对象
    
    Returns:
        错误列表（空列表表示无错误）
    """
    errors = []
    
    # 验证模型索引
    if config.llm.current_model_index >= len(config.llm.model_list):
        errors.append(
            f"LLM模型索引 {config.llm.current_model_index} 超出范围 "
            f"(模型列表长度: {len(config.llm.model_list)})"
        )
    
    if config.environment.current_model_index >= len(config.environment.model_list):
        errors.append(
            f"环境系统模型索引 {config.environment.current_model_index} 超出范围 "
            f"(模型列表长度: {len(config.environment.model_list)})"
        )
    
    # 验证API密钥
    if not config.llm.api_key or config.llm.api_key == "YOUR_API_KEY":
        errors.append("LLM API密钥未配置或无效")
    
    # 验证场景模式
    if config.plugin.scene_view_mode not in ["2d", "3d"]:
        errors.append(f"无效的场景模式: {config.plugin.scene_view_mode}")
    
    return errors


def reload_config(config_path: str | Path | None = None) -> Config:
    """
    重新加载配置文件
    
    Args:
        config_path: 配置文件路径（可选）
    
    Returns:
        Config 对象
    """
    return load_config_from_file(config_path)
