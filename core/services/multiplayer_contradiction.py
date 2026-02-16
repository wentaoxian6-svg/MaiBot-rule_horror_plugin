"""多人模式规则矛盾系统

为不同玩家生成"表面一致、细节矛盾"的规则集合，用于多人模式的信息不对称与合作推理。

该文件曾被批量替换破坏（引号/全角标点落入语法层等），此处按原意重写并保持对外接口：
- PlayerRuleset
- MultiplayerContradictionSystem
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Mapping
from typing import TypeAlias

from ..llm.client import LLMClient, get_default_max_tokens

logger = logging.getLogger(__name__)

# 类型定义
RuleData: TypeAlias = dict[str, "str | int | bool | None"]
RulesetData: TypeAlias = dict[str, "str | bool | list | None"]
ContradictionSummary: TypeAlias = dict[str, dict[str, "bool | str | list[int]"]]


@dataclass
class PlayerRuleset:
    """玩家规则集"""

    player_id: str
    player_name: str
    rules: list[RuleData]
    is_deceptive: bool
    deception_target: str | None = None


class MultiplayerContradictionSystem:
    """多人模式规则矛盾系统"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()

    async def generate_contradictory_rules(
        self,
        base_rules: list[RuleData],
        players: Mapping[str, object],
    ) -> dict[str, PlayerRuleset]:
        """为不同玩家生成矛盾规则集"""

        player_ids = list(players.keys())
        if len(player_ids) < 2:
            return {
                pid: PlayerRuleset(
                    player_id=pid,
                    player_name=self._get_player_name(players.get(pid), default="未知玩家"),
                    rules=base_rules,
                    is_deceptive=False,
                )
                for pid in player_ids
            }

        result: dict[str, PlayerRuleset] = {}

        # 默认：第一个玩家看到基础规则；其他玩家看到“细微篡改”的规则
        target_player_id = player_ids[0]
        for i, pid in enumerate(player_ids):
            player_name = self._get_player_name(players.get(pid), default=f"玩家{i+1}")
            if i == 0:
                result[pid] = PlayerRuleset(
                    player_id=pid,
                    player_name=player_name,
                    rules=base_rules,
                    is_deceptive=False,
                )
            else:
                result[pid] = await self._generate_deceptive_rules(
                    player_id=pid,
                    player_name=player_name,
                    base_rules=base_rules,
                    target_player_id=target_player_id,
                )

        return result

    def _get_player_name(self, player_obj: object, default: str) -> str:
        if player_obj is None:
            return default
        # 兼容：player_obj 可能是 dict 或 Player
        if isinstance(player_obj, dict):
            return str(player_obj.get("name", default) or default)
        return str(getattr(player_obj, "name", default) or default)

    async def _generate_deceptive_rules(
        self,
        player_id: str,
        player_name: str,
        base_rules: list[RuleData],
        target_player_id: str,
    ) -> PlayerRuleset:
        """生成欺骗性规则集"""

        base_rules_text = "\n".join(
            f"{r.get('id', i+1)}. {r.get('text', r.get('content', str(r)))}" for i, r in enumerate(base_rules)
        )

        system_prompt = (
            "你是规则怪谈游戏的‘规则矛盾生成器’。你要为某个玩家生成一份与目标玩家略有矛盾的规则列表。\n\n"
            "要求：\n"
            "1) 规则数量尽量与基础规则一致\n"
            "2) 保留 id 字段（若基础规则没有 id，可自行从 1 开始）\n"
            "3) 矛盾要‘微妙且合理’，让玩家难以立刻判断谁对谁错\n"
            "4) 不要加入解释性长段落，每条规则保持简洁\n\n"
            "只返回 JSON：\n"
            "{\n"
            '  "rules": [\n'
            '    {"id": 1, "text": "规则内容", "is_trap": false}\n'
            "  ],\n"
            f'  "deception_target": "{target_player_id}"\n'
            "}"
        )

        user_prompt = (
            f"基础规则如下：\n{base_rules_text}\n\n"
            f"目标玩家ID: {target_player_id}\n"
            f"当前玩家ID: {player_id}\n"
            f"当前玩家名称: {player_name}\n\n"
            "请生成当前玩家看到的矛盾规则。"
        )

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            data_raw = resp.parse_json()
            data: RulesetData = data_raw if isinstance(data_raw, dict) else {}

            rules = data.get("rules", base_rules)
            if not isinstance(rules, list):
                rules = base_rules

            return PlayerRuleset(
                player_id=player_id,
                player_name=player_name,
                rules=rules,
                is_deceptive=True,
                deception_target=str(data.get("deception_target", target_player_id) or target_player_id),
            )
        except Exception as e:
            logger.error(f"生成欺骗性规则失败: {e}", exc_info=True)
            return PlayerRuleset(
                player_id=player_id,
                player_name=player_name,
                rules=base_rules,
                is_deceptive=False,
            )

    async def generate_role_specific_rules(
        self,
        base_rules: list[RuleData],
        player_id: str,
        player_name: str,
        role: str,
    ) -> PlayerRuleset:
        """为特定角色生成专属规则（可用于身份系统）"""

        base_rules_text = "\n".join(
            f"{r.get('id', i+1)}. {r.get('text', r.get('content', str(r)))}" for i, r in enumerate(base_rules)
        )

        system_prompt = (
            "你是规则怪谈游戏的‘角色规则生成器’。请基于角色身份，为该玩家生成一些专属规则。\n"
            "专属规则需要：贴合角色工作/权限/限制，且可与基础规则产生信息不对称。\n\n"
            "返回 JSON：\n"
            "{\n"
            '  "rules": [\n'
            '    {"id": 1, "text": "规则内容", "is_trap": false, "is_role_specific": true}\n'
            "  ]\n"
            "}"
        )

        user_prompt = (
            f"基础规则：\n{base_rules_text}\n\n"
            f"玩家ID: {player_id}\n"
            f"玩家名称: {player_name}\n"
            f"玩家角色: {role}\n\n"
            "请生成该角色专属规则。"
        )

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            data_raw = resp.parse_json()
            data: RulesetData = data_raw if isinstance(data_raw, dict) else {}

            rules = data.get("rules", base_rules)
            if not isinstance(rules, list):
                rules = base_rules

            return PlayerRuleset(
                player_id=player_id,
                player_name=player_name,
                rules=rules,
                is_deceptive=False,
            )
        except Exception as e:
            logger.error(f"生成角色规则失败: {e}", exc_info=True)
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
    ) -> list[RuleData]:
        """获取某个玩家的规则列表"""

        ruleset = player_rulesets.get(player_id)
        return ruleset.rules if ruleset else []

    def is_rule_contradiction(
        self,
        player_rulesets: dict[str, PlayerRuleset],
        rule_id: int,
    ) -> bool:
        """判断某条规则在不同玩家间是否存在文本差异（视为矛盾）"""

        texts: set[str] = set()
        for rs in player_rulesets.values():
            for rule in rs.rules:
                try:
                    rid = int(rule.get("id", -1))
                except Exception:
                    continue
                if rid == rule_id:
                    texts.add(str(rule.get("text", rule.get("content", ""))) or "")
        # 文本数量 > 1 即存在差异
        return len(texts) > 1

    def get_contradiction_summary(
        self, player_rulesets: dict[str, PlayerRuleset]
    ) -> ContradictionSummary:
        """生成矛盾摘要"""

        summary: ContradictionSummary = {}
        for rs in player_rulesets.values():
            summary[rs.player_id] = {
                "is_deceptive": rs.is_deceptive,
                "deception_target": rs.deception_target,
                "contradicted_rules": [],
            }

        all_ids: set[int] = set()
        for rs in player_rulesets.values():
            for rule in rs.rules:
                try:
                    all_ids.add(int(rule.get("id", -1)))
                except Exception:
                    continue

        for rid in sorted(x for x in all_ids if x >= 0):
            if self.is_rule_contradiction(player_rulesets, rid):
                for pid in summary:
                    summary[pid]["contradicted_rules"].append(rid)

        return summary
