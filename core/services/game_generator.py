"""游戏生成服务 - 生成场景、规则、背景故事"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..llm.client import LLMClient
from ..game.models import GameSession

logger = logging.getLogger(__name__)


class GameGenerator:
    """游戏生成器 - 生成完整的规则怪谈游戏"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def generate_game(
        self,
        group_id: str,
        game_mode: str = "单人",
    ) -> GameSession:
        """
        生成完整的游戏会话
        
        Args:
            group_id: 群组ID
            game_mode: 游戏模式（单人/多人）
        
        Returns:
            GameSession 对象
        """
        logger.info(f"开始生成游戏: {group_id}, 模式: {game_mode}")
        
        # 生成场景和规则
        game_data = await self._generate_scene_and_rules(game_mode)
        
        # 创建游戏会话
        session = GameSession(
            group_id=group_id,
            scene_name=game_data.get("scene_name", "未知场景"),
            background=game_data.get("background", ""),
            player_identity=game_data.get("player_identity", "访客"),
            hidden_truth=game_data.get("hidden_truth", ""),
            game_mode=game_mode,
            rules=game_data.get("rules", []),
            win_condition=game_data.get("win_condition", ""),
            clues=game_data.get("clues", []),
            core_symbols=game_data.get("core_symbols", []),
        )
        
        # 生成规则网络
        await self._generate_rule_network(session)
        
        # 如果是多人模式，生成协作规则
        if game_mode == "多人":
            await self._generate_collaborative_rules(session)
        
        logger.info(f"游戏生成完成: {session.scene_name}")
        return session
    
    async def _generate_rule_network(self, session: GameSession) -> None:
        """生成规则网络（规则与真相的因果关系）"""
        system_prompt = """你是规则怪谈游戏的规则网络生成系统。你需要为每条规则建立与隐藏真相的因果关系。

规则网络的作用：
1. 帮助玩家通过规则推理出隐藏真相
2. 规则之间形成逻辑链条
3. 每条规则都与真相的某个要素相关

返回JSON格式：
{
    "rule_connections": [
        {
            "rule": "规则内容",
            "related_truth_elements": ["真相要素1", "真相要素2"],
            "causal_relationship": "因果关系描述"
        }
    ]
}"""

        user_prompt = f"""规则：
{chr(10).join(f"{i+1}. {r.get('text', str(r))}" for i, r in enumerate(session.rules))}

隐藏真相：{session.hidden_truth}

请为每条规则建立与隐藏真相的因果关系。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=1000,
            )
            
            network_data = response.parse_json()
            session.rule_network["rule_connections"] = network_data.get("rule_connections", [])
            logger.info(f"规则网络生成成功: {len(session.rule_network['rule_connections'])}条连接")
            
        except Exception as e:
            logger.error(f"生成规则网络失败: {e}")
    
    async def _generate_collaborative_rules(self, session: GameSession) -> None:
        """生成协作规则（多人模式）"""
        system_prompt = """你是规则怪谈游戏的协作规则生成系统。你需要生成1-2条需要多个玩家协作才能发现或触发的规则。

协作规则的特点：
1. 需要2-3名玩家同时行动
2. 单个玩家无法完成
3. 鼓励玩家之间的沟通和合作
4. 协作成功后会揭示重要线索或真相

返回JSON格式：
{
    "collaborative_rules": [
        {
            "rule": "需要协作发现的规则",
            "required_players": 2,
            "required_actions": ["玩家1的行动", "玩家2的行动"],
            "trigger_condition": "触发条件描述",
            "reward": "协作成功后的奖励（线索或真相）",
            "discovered": false
        }
    ]
}"""

        user_prompt = f"""场景：{session.scene_name}
背景：{session.background}
隐藏真相：{session.hidden_truth}

请生成1-2条协作规则。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=800,
            )
            
            collab_data = response.parse_json()
            session.rule_network["collaborative_rules"] = collab_data.get("collaborative_rules", [])
            logger.info(f"协作规则生成成功: {len(session.rule_network['collaborative_rules'])}条")
            
        except Exception as e:
            logger.error(f"生成协作规则失败: {e}")

    async def _generate_scene_and_rules(self, game_mode: str) -> dict[str, Any]:
        """生成场景和规则"""
        system_prompt = """你是一位精通规则怪谈创作的游戏设计师。你需要创作一个完整的规则怪谈游戏。

规则怪谈的特点：
1. 表面规则与隐藏真相的矛盾
2. 遵守规则可能导致危险，违反规则可能安全
3. 规则之间存在逻辑矛盾
4. 需要推理出隐藏真相才能通关

请生成一个完整的规则怪谈游戏，包含：
- 场景名称和背景故事
- 玩家身份和到来原因
- 5-8条规则（部分真实，部分误导）
- 隐藏真相
- 通关条件
- 核心线索和符号

以JSON格式返回：
{
    "scene_name": "场景名称",
    "background": "背景故事（200-300字）",
    "player_identity": "玩家身份",
    "arrival_reason": "到来原因",
    "rules": [
        {"text": "规则1", "is_true": true, "hidden_meaning": "隐藏含义"},
        {"text": "规则2", "is_true": false, "hidden_meaning": "隐藏含义"}
    ],
    "hidden_truth": "隐藏真相（完整描述）",
    "win_condition": "通关条件",
    "clues": ["线索1", "线索2", "线索3"],
    "core_symbols": ["符号1", "符号2"]
}"""

        user_prompt = f"""请创作一个规则怪谈游戏。

游戏模式：{game_mode}

要求：
1. 场景要有独特性和吸引力
2. 规则要有逻辑矛盾和误导性
3. 隐藏真相要合理且可推理
4. 通关条件要明确
5. 使用克系、新怪谈（New Weird）、Liminal Space风格

请生成游戏内容。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=2000,
            )
            
            game_data = response.parse_json()
            logger.info(f"场景生成成功: {game_data.get('scene_name', 'Unknown')}")
            return game_data
            
        except Exception as e:
            logger.error(f"生成场景失败: {e}")
            # 返回默认场景
            return self._get_default_game()

    def _get_default_game(self) -> dict[str, Any]:
        """获取默认游戏（当生成失败时使用）"""
        return {
            "scene_name": "深夜便利店",
            "background": "这是一家24小时营业的便利店，位于城市的边缘地带。店内灯光明亮，货架整齐，但总有一种说不出的诡异感。墙上贴着几条规则，似乎是给夜班员工的提醒。",
            "player_identity": "夜班店员",
            "arrival_reason": "你是新来的夜班店员，今晚是你的第一个夜班。",
            "rules": [
                {"text": "晚上11点后，如果有顾客进店，必须微笑服务", "is_true": False, "hidden_meaning": "晚上11点后进店的不是人"},
                {"text": "如果听到敲门声，不要开门", "is_true": True, "hidden_meaning": "门外的东西很危险"},
                {"text": "冰柜里的饮料必须保持满的", "is_true": True, "hidden_meaning": "饮料是用来安抚某种存在的"},
                {"text": "如果灯光闪烁，立即躲到收银台下", "is_true": True, "hidden_meaning": "灯光闪烁意味着危险来临"},
                {"text": "不要进入仓库", "is_true": False, "hidden_meaning": "仓库是唯一安全的地方"},
            ],
            "hidden_truth": "这家便利店建在一个旧墓地上，晚上会有'东西'来购物。真正的规则是：不要与'顾客'对视，不要回应他们的问话，保持冰柜满的，灯光闪烁时躲起来。仓库是唯一安全的地方。",
            "win_condition": "在早上6点前存活，或者发现仓库的秘密并逃离",
            "clues": ["冰柜里的饮料总是很快就空了", "收银台下有旧的血迹", "仓库门上有抓痕"],
            "core_symbols": ["冰柜", "灯光", "仓库门"],
        }
