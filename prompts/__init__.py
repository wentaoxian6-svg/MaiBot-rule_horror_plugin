"""
Prompt 模板模块 - 用于 LLM 的提示词工具

历史上这里曾导出 11 个 Prompt 构造函数，但全代码库零调用点，
实际 Prompt 均以 inline 形式散落在 services 层。现仅导出 remove_emojis 工具函数。
"""
from .shared_prompts import remove_emojis

__all__ = ["remove_emojis"]
