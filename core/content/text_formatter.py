"""文本格式化工具"""
from __future__ import annotations

import re


class TextFormatter:
    """文本格式化器"""

    @staticmethod
    def clean_llm_response(response: str) -> str:
        """清理 LLM 响应中的 markdown 代码块"""
        response = response.strip()

        # 移除 markdown 代码块
        if response.startswith("```json"):
            response = response[7:]
        elif response.startswith("```"):
            response = response[3:]

        if response.endswith("```"):
            response = response[:-3]

        # 移除可能的语言标识
        response = re.sub(r"^\s*json\s*", "", response, flags=re.IGNORECASE)

        return response.strip()

    @staticmethod
    def truncate(text: str, max_length: int, suffix: str = "...") -> str:
        """截断文本"""
        if len(text) <= max_length:
            return text
        return text[: max_length - len(suffix)] + suffix

    @staticmethod
    def format_list(items: list[str], bullet: str = "•") -> str:
        """格式化列表"""
        return "\n".join(f"{bullet} {item}" for item in items)

    @staticmethod
    def format_dict(data: dict[str, object], indent: int = 0) -> str:
        """格式化字典"""
        lines = []
        prefix = "  " * indent
        for key, value in data.items():
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.append(TextFormatter.format_dict(value, indent + 1))
            elif isinstance(value, list):
                lines.append(f"{prefix}{key}:")
                for item in value:
                    lines.append(f"{prefix}  - {item}")
            else:
                lines.append(f"{prefix}{key}: {value}")
        return "\n".join(lines)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """清理文件名"""
        # 移除非法字符
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        # 限制长度
        return filename[:100]

    @staticmethod
    def build_immersion_enhancement(
        sanity: int,
        fear_level: int,
        location: str,
        time_of_day: str,
    ) -> str:
        """构建沉浸感增强文本"""
        enhancements = []

        if sanity < 30:
            enhancements.append("你的意识开始模糊，现实与幻觉的界限变得不清晰...")
        elif sanity < 60:
            enhancements.append("你感到一丝不安，似乎有什么东西在暗处注视着你...")

        if fear_level > 70:
            enhancements.append("恐惧几乎要将你吞噬，你的心跳快得无法控制...")
        elif fear_level > 40:
            enhancements.append("你感到紧张，手心开始出汗...")

        if time_of_day in ["深夜", "凌晨"]:
            enhancements.append("四周一片寂静，只有你的呼吸声在黑暗中回响...")

        if location in ["地下室", "阁楼", "废弃病房"]:
            enhancements.append("这里弥漫着腐朽和霉变的气味，让人感到窒息...")

        return "\n".join(enhancements) if enhancements else ""
