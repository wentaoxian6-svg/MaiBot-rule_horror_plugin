"""多人模式规则矛盾系统 - 为不同玩家生成不同的规则"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..llm.client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


@dataclass
class PlayerRuleset:
    """玩家规则集"""
    player_id: str
    player_name: str
    rules: list[dict[str, Any]]
    is_deceptive: bool
    deception_target: Optional[str] = None


class MultiplayerContradictionSystem:
    """多人模式规则矛盾系统 - 为不同玩家生成不同的规则"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def generate_contradictory_rules(
        self,
        base_rules: list[dict[str, Any]],
        players: dict[str, Any],
    ) -> dict[str, PlayerRuleset]:
        """
        为不同玩家生成矛盾的规则

        Args:
            base_rules: 基础规则列表
            players: 玩家字典 {player_id: player_info}

        Returns:
            玩家规则集字典 {player_id: PlayerRuleset}
        """
        player_ids = list(players.keys())

        if len(player_ids) < 2:
            # 单人模式，直接返回基础规则
            return {
                pid: PlayerRuleset(
                    player_id=pid,
                    player_name=players[pid].get("name", "未知玩家"),
                    rules=base_rules,
                    is_deceptive=False,
                )
                for pid in player_ids
            }

        # 多人模式，生成矛盾规则
        result = {}

        for i, player_id in enumerate(player_ids):
            player_name = players[player_id].get("name", f"玩家{i+1}")

            # 为每个玩家生成不同的规则集
            if i == 0:
                # 第一个玩家看到基础规则
                ruleset = PlayerRuleset(
                    player_id=player_id,
                    player_name=player_name,
                    rules=base_rules,
                    is_deceptive=False,
                )
            else:
                # 其他玩家看到矛盾的规则
                ruleset = await self._generate_deceptive_rules(
                    player_id=player_id,
                    player_name=player_name,
                    base_rules=base_rules,
                    target_player_id=player_ids[0],
                )

            result[player_id] = ruleset

        return result

    async def _generate_deceptive_rules(
        self,
        player_id: str,
        player_name: str,
        base_rules: list[dict[str, Any]],
        target_player_id: str,
    ) -> PlayerRuleset:
        """生成欺骗性规则"""
        system_prompt = """你是一个规则怪谈游戏的规则矛盾生成器。你的任务是为特定玩家生成与其他玩家矛盾的规则。

规则矛盾策略：
1. 修改部分规则的内容，使其与其他玩家看到的规则不同
2. 保持规则的格式和数量一致
3. 制造认知失调：让玩家A和玩家B对同一事物有不同的认知
4. 添加暗示其他玩家不可信的规则
5. 保持规则的可信度，让玩家难以察觉矛盾

请以 JSON 格式返回：
{
    "rules": [
        {"id": 1, "text": "修改后的规则内容", "is_trap": false},
        {"id": 2, "text": "修改后的规则内容", "is_trap": true}
    ],
    "deception_target": "目标玩家ID"
}

注意：
- 规则数量应该与基础规则一致
- 规则ID应该保持一致
- 修改应该微妙但有效
- 添加的矛盾规则应该看起来合理"""

        # 构建基础规则文本
        base_rules_text = "\n".join([
            f"{rule['id']}. {rule['text']}"
            for rule in base_rules
        ])

        user_prompt = f"""基础规则：
{base_rules_text}

目标玩家ID：{target_player_id}

当前玩家ID：{player_id}
当前玩家名称：{player_name}

请为当前玩家生成与其他玩家矛盾的规则。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=800,
            )

            result = response.parse_json()

            return PlayerRuleset(
                player_id=player_id,
                player_name=player_name,
                rules=result.get("rules", base_rules),
                is_deceptive=True,
                deception_target=result.get("deception_target", target_player_id),
            )

        except Exception as e:
            logger.error(f"生成欺骗性规则失败: {e}")
            # 返回基础规则作为备用
            return PlayerRuleset(
                player_id=player_id,
                player_name=player_name,
                rules=base_rules,
                is_deceptive=False,
            )

    async def generate_role_specific_rules(
        self,
        base_rules: list[dict[str, Any]],
        player_id: str,
        player_name: str,
        role: str,
    ) -> PlayerRuleset:
        """
        为特定角色生成规则

        Args:
            base_rules: 基础规则列表
            player_id: 玩家ID
            player_name: 玩家名称
            role: 玩家角色（如"医生"、"护士"、"学生"等）

        Returns:
            PlayerRuleset 对象
        """
        system_prompt = """你是一个规则怪谈游戏的角色规则生成器。你的任务是为特定角色生成专属规则。

角色规则策略：
1. 根据角色身份添加专属规则
2. 修改部分规则以适应角色特性
3. 保持规则的可信度
4. 角色规则应该与场景和背景相符

请以 JSON 格式返回：
{
    "rules": [
        {"id": 1, "text": "修改后的规则内容", "is_trap": false},
        {"id": 2, "text": "修改后的规则内容", "is_trap": true},
        {"id": 4, "text": "角色专属规则", "is_role_specific": true}
    ]
}

注意：
- 角色专属规则的ID应该从基础规则数量+1开始
- 角色规则应该与角色身份相关
- 保持规则的可信度"""

        # 构建基础规则文本
        base_rules_text = "\n".join([
            f"{rule['id']}. {rule['text']}"
            for rule in base_rules
        ])

        user_prompt = f"""基础规则：
{base_rules_text}

玩家ID：{player_id}
玩家名称：{player_name}
玩家角色：{role}

请为该角色生成专属规则。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=800,
            )

            result = response.parse_json()

            return PlayerRuleset(
                player_id=player_id,
                player_name=player_name,
                rules=result.get("rules", base_rules),
                is_deceptive=False,
            )

        except Exception as e:
            logger.error(f"生成角色规则失败: {e}")
            return PlayerRuleset(
                player_id=player_id,
                player_name=player_name,
                rules=base_rules,
                is_deceptive=False,
            )

    def get_player_rules(
        self,
        player_rulesets: dict[str, PlayerRuleset],
        player_id: str,
    ) -> list[dict[str, Any]]:
        """
        获取特定玩家的规则

        Args:
            player_rulesets: 玩家规则集字典
            player_id: 玩家ID

        Returns:
            规则列表
        """
        ruleset = player_rulesets.get(player_id)
        if ruleset:
            return ruleset.rules
        return []

    def is_rule_contradiction(
        self,
        player_rulesets: dict[str, PlayerRuleset],
        rule_id: int,
    ) -> bool:
        """
        检查特定规则是否在不同玩家之间存在矛盾

        Args:
            player_rulesets: 玩家规则集字典
            rule_id: 规则ID

        Returns:
            是否存在矛盾
        """
        rules_texts = set()

        for ruleset in player_rulesets.values():
            for rule in ruleset.rules:
                if rule["id"] == rule_id:
                    rules_texts.add(rule["text"])

        return len(rules_texts) > 1

    def get_contradiction_summary(
        self,
        player_rulesets: dict[str, PlayerRuleset],
    ) -> dict[str, Any]:
        """
        获取规则矛盾摘要

        Args:
            player_rulesets: 玩家规则集字典

        Returns:
            矛盾摘要字典
        """
        contradictions = {}

        for ruleset in player_rulesets.values():
            contradictions[ruleset.player_id] = {
                "is_deceptive": ruleset.is_deceptive,
                "deception_target": ruleset.deception_target,
                "contradicted_rules": [],
            }

        # 检查每条规则是否存在矛盾
        all_rule_ids = set()
        for ruleset in player_rulesets.values():
            for rule in ruleset.rules:
                all_rule_ids.add(rule["id"])

        for rule_id in all_rule_ids:
            if self.is_rule_contradiction(player_rulesets, rule_id):
                for player_id in contradictions:
                    contradictions[player_id]["contradicted_rules"].append(rule_id)

        return contradictions
