"""Prompt 构建器 - 支持模板和外部配置"""
from __future__ import annotations

import os
import re
from typing import Optional
from string import Template

# PyYAML 是可选依赖：某些环境可能装在不同解释器/虚拟环境中
try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


class PromptBuilder:
    """Prompt 构建器"""

    def __init__(self, prompts_dir: Optional[str] = None, enable_cache: bool = True):
        if prompts_dir is None:
            # 默认 prompts 目录
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            prompts_dir = os.path.join(base_dir, "prompts")
        self.prompts_dir = prompts_dir
        self._enable_cache = enable_cache
        self._cache: dict[str, str] = {}
        self._cache_mtime: dict[str, float] = {}  # 记录文件修改时间

    def _load_template(self, name: str) -> str:
        """加载模板文件"""
        # 检查缓存是否有效（文件是否被修改）
        if self._enable_cache and name in self._cache:
            yaml_path = os.path.join(self.prompts_dir, f"{name}.yaml")
            txt_path = os.path.join(self.prompts_dir, f"{name}.txt")

            current_mtime = 0.0
            if os.path.exists(yaml_path):
                current_mtime = os.path.getmtime(yaml_path)
            elif os.path.exists(txt_path):
                current_mtime = os.path.getmtime(txt_path)

            # 如果文件未修改，使用缓存
            if current_mtime == self._cache_mtime.get(name, 0.0):
                return self._cache[name]

        # 尝试加载 YAML 格式的 prompt
        yaml_path = os.path.join(self.prompts_dir, f"{name}.yaml")
        if os.path.exists(yaml_path) and yaml is not None:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                template = (data or {}).get("template", "")
                if self._enable_cache:
                    self._cache[name] = template
                    self._cache_mtime[name] = os.path.getmtime(yaml_path)
                return template

        # 尝试加载纯文本格式的 prompt
        txt_path = os.path.join(self.prompts_dir, f"{name}.txt")
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                template = f.read()
                if self._enable_cache:
                    self._cache[name] = template
                    self._cache_mtime[name] = os.path.getmtime(txt_path)
                return template

        # YAML 文件存在但缺少 PyYAML 时，给出更明确的错误
        if os.path.exists(yaml_path) and yaml is None:
            raise ModuleNotFoundError(
                "缺少 PyYAML，无法解析 prompt 的 .yaml 文件。请使用同一个 Python 环境执行：python -m pip install pyyaml"
            )

        raise FileNotFoundError(f"找不到 prompt 模板: {name}")

    def clear_cache(self) -> None:
        """清除模板缓存"""
        self._cache.clear()
        self._cache_mtime.clear()

    def build(
        self,
        template_name: str,
        **kwargs: object,
    ) -> str:
        """
        构建 prompt

        Args:
            template_name: 模板名称
            **kwargs: 模板变量

        Returns:
            构建后的 prompt 字符串
        """
        template = self._load_template(template_name)
        result = Template(template).safe_substitute(**kwargs)

        # 检查是否有未替换的变量
        unresolved = re.findall(r'\$\{(\w+)}', result)
        if unresolved:
            import warnings
            warnings.warn(
                f"模板 '{template_name}' 存在未替换的变量: {set(unresolved)}. "
                f"请确保提供了所有必需的参数: {kwargs.keys()}",
                UserWarning,
                stacklevel=2
            )

        return result

    def build_from_string(self, template: str, **kwargs: object) -> str:
        """从字符串模板构建 prompt"""
        return Template(template).safe_substitute(**kwargs)


# 预定义的 Prompt 模板（作为后备）
DEFAULT_PROMPTS = {
    "scene_generation": """
你是一位精通规则怪谈创作的游戏设计师。请生成一个恐怖或诡异的规则怪谈的剧情导入和隐藏真相。

**游戏模式：${game_mode}**

**创作要求：**

1. **场景选择**：选择一个具有恐怖潜力的场景（如：深夜的医院、废弃的学校、神秘的公寓、古老的庄园、荒凉的工厂、阴森的地铁站、诡异的酒店等）

2. **背景故事**：描述场景的历史、发生过什么、为什么诡异
   - 必须包含具体的历史事件或悲剧
   - 描述场景的异常现象（如：时间错乱、空间扭曲、超自然现象等）
   - 暗示场景背后隐藏的真相（不要直接揭示）

3. **玩家身份**：描述玩家在这个场景中的身份或角色
   - 身份应与场景和剧情相符
   - 可以暗示身份与场景历史有某种联系
   - 如果是多人模式，请使用复数形式"你们都是..."

4. **核心象征符号**：生成2-3个"核心象征符号"
   - 符号可以是数字、图案、旋律、花纹、颜色、物品等
   - 每个符号需要有一个简短的描述

请以JSON格式返回，包含以下字段：
- scene_name: 场景名称
- background: 背景故事
- player_identity: 玩家身份
- core_symbols: 核心符号列表
- hidden_truth: 隐藏真相
""",
    "rule_generation": """
基于以下场景信息，生成规则怪谈的规则：

场景：${scene_name}
背景：${background}
玩家身份：${player_identity}

要求：
1. 生成5-8条规则
2. 规则应该看似合理但暗藏杀机
3. 部分规则可能是假的或误导性的
4. 规则之间应该有逻辑关联

请以JSON格式返回规则列表。
""",
}


class SimplePromptBuilder:
    """简单 Prompt 构建器（不依赖外部文件）"""

    def __init__(self):
        self.templates = DEFAULT_PROMPTS.copy()

    def build(self, template_name: str, **kwargs: object) -> str:
        """构建 prompt"""
        if template_name not in self.templates:
            raise KeyError(f"未知的模板: {template_name}")
        return Template(self.templates[template_name]).safe_substitute(**kwargs)

    def add_template(self, name: str, template: str) -> None:
        """添加新模板"""
        self.templates[name] = template
