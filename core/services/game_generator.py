"""游戏生成服务 - 生成场景、规则、背景故事"""
from __future__ import annotations

import logging
import re
from typing import TypeAlias

from ...common.models import JsonObject
from ..llm.client import LLMClient
from ..game.models import GameSession, Rule

logger = logging.getLogger(__name__)

# 类型定义
GameData: TypeAlias = dict[str, "str | int | float | bool | list | dict | None"]
AssignmentData: TypeAlias = dict[str, "str | list | dict"]
IdentityData: TypeAlias = dict[str, "str | list | dict"]


class GameGenerator:
    """游戏生成器 - 生成完整的规则怪谈游戏"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()

    @staticmethod
    def _normalize_free_text(text: object) -> str:
        """清理自由文本里的列表感和多余空白。"""
        normalized = str(text or "").replace("\r\n", "\n").strip()
        normalized = re.sub(r"(?m)^\s*(?:[-*•]+|\d+[\.、])\s*", "", normalized)
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\n{2,}", "\n", normalized)
        return normalized.strip()

    @classmethod
    def _sanitize_background_text(cls, text: object) -> str:
        normalized = cls._normalize_free_text(text)
        if not normalized:
            return ""

        blocked = (
            "规则", "规矩", "守则", "须知", "提醒", "目标", "任务", "通关", "玩家", "你要", "你必须",
            "你不能", "你不要", "你需要", "你们要", "你们必须", "你们不能", "你们不要",
        )
        directive = re.compile(r"(?:必须|禁止|不得|严禁|务必|不要|不能).{0,28}(?:。|！|？|$)")
        sentences = [item.strip() for item in re.split(r"(?<=[。！？])", normalized) if item.strip()]
        kept: list[str] = []
        for sentence in sentences:
            if any(token in sentence for token in blocked):
                continue
            if directive.search(sentence):
                continue
            kept.append(sentence)

        return "".join(kept).strip()

    @classmethod
    def _compress_narrative_text(
        cls,
        text: object,
        *,
        max_units: int,
        max_chars: int,
        clause_mode: bool = False,
    ) -> str:
        """把生成文本压成更接近现场片段的短叙事。"""
        normalized = cls._normalize_free_text(text)
        if not normalized:
            return ""

        splitter = r"[。！？!?；;\n]+" if not clause_mode else r"[。！？!?；;，,\n]+"
        units = [item.strip(" \"'“”‘’") for item in re.split(splitter, normalized) if item.strip(" \"'“”‘’")]
        if not units:
            return normalized[:max_chars].rstrip("，,；;、 ") + "。"

        selected: list[str] = []
        total_chars = 0
        for unit in units:
            compact_unit = re.sub(r"\s+", "", unit)
            if not compact_unit:
                continue
            if clause_mode and len(compact_unit) > 26:
                short_clauses = [frag.strip() for frag in re.split(r"[，,]", compact_unit) if frag.strip()]
                if short_clauses:
                    compact_unit = short_clauses[0]
            projected = total_chars + len(compact_unit)
            if selected and projected > max_chars:
                break
            selected.append(compact_unit)
            total_chars = projected
            if len(selected) >= max_units or total_chars >= max_chars:
                break

        if not selected:
            selected = [units[0][:max_chars].strip()]

        sentence_end = "。" if not clause_mode else "，"
        text_body = sentence_end.join(item.rstrip("，,；;、。") for item in selected if item.strip())
        text_body = text_body.rstrip("，,；;、。")
        return f"{text_body}{'。' if text_body else ''}"

    @classmethod
    def _sanitize_npc_guidance_texts(cls, data: GameData) -> GameData:
        sanitized = dict(data)
        sanitized["npc_behavior"] = cls._normalize_free_text(sanitized.get("npc_behavior", ""))
        sanitized["conversation_intent"] = cls._normalize_free_text(sanitized.get("conversation_intent", ""))
        sanitized["npc_dialogue"] = ""
        sanitized["rule_carrier_description"] = cls._normalize_free_text(sanitized.get("rule_carrier_description", ""))
        hinted_raw = sanitized.get("hinted_rule_texts", [])
        sanitized["hinted_rule_texts"] = [
            str(item).strip() for item in hinted_raw if str(item).strip()
        ][:2] if isinstance(hinted_raw, list) else []
        return sanitized

    def _normalize_rules(
        self,
        rules: object,
        *,
        default_source: str,
        source_type: str,
    ) -> list[JsonObject]:
        """把旧版规则字典统一归一到新版结构。"""
        if not isinstance(rules, list):
            return []

        normalized: list[JsonObject] = []
        for index, raw_rule in enumerate(rules):
            rule = Rule.from_dict(raw_rule, index)
            if not rule.source:
                rule.source = default_source
            if not rule.source_type:
                rule.source_type = source_type
            if not rule.constraint:
                rule.constraint = rule.surface_text
            normalized.append(rule.to_dict())
        return normalized

    @staticmethod
    def _clamp_ratio(value: object, default: float) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        if isinstance(value, str):
            try:
                return max(0.0, min(1.0, float(value.strip())))
            except Exception:
                return default
        return default

    def _normalize_npc_roster(
        self,
        roster: object,
        npc_guidance: GameData,
    ) -> list[JsonObject]:
        """为运行时 NPC 补齐信息偏差相关字段。"""
        raw_roster = roster if isinstance(roster, list) else []
        if not raw_roster:
            raw_roster = self._build_default_npc_roster(npc_guidance)

        normalized: list[JsonObject] = []
        default_attitude = str(npc_guidance.get("npc_attitude", "") or "").strip()
        for index, raw_npc in enumerate(raw_roster):
            if not isinstance(raw_npc, dict):
                continue

            attitude_text = str(raw_npc.get("attitude", default_attitude) or default_attitude).strip()
            info_reliability = self._clamp_ratio(raw_npc.get("knowledge_reliability"), 0.75)
            deception_tendency = self._clamp_ratio(raw_npc.get("deception_tendency"), 0.15)
            corruption_level = self._clamp_ratio(raw_npc.get("corruption_level"), 0.0)
            if any(keyword in attitude_text for keyword in ["友好", "温和", "热情"]):
                deception_tendency = min(deception_tendency, 0.2)
            elif any(keyword in attitude_text for keyword in ["警告", "严厉", "冷淡", "不耐烦"]):
                info_reliability = min(info_reliability, 0.7)
            elif any(keyword in attitude_text for keyword in ["敌对", "威胁"]):
                deception_tendency = max(deception_tendency, 0.45)

            bias_tags = raw_npc.get("bias_tags", [])
            normalized.append(
                {
                    **raw_npc,
                    "npc_id": str(raw_npc.get("npc_id", f"guide_{index}") or f"guide_{index}").strip(),
                    "attitude": attitude_text,
                    "knowledge_reliability": info_reliability,
                    "deception_tendency": deception_tendency,
                    "corruption_level": corruption_level,
                    "current_state": str(raw_npc.get("current_state", "稳定") or "稳定").strip(),
                    "bias_tags": [str(item).strip() for item in bias_tags if str(item).strip()] if isinstance(bias_tags, list) else [],
                    "known_rule_ids": [str(item).strip() for item in raw_npc.get("known_rule_ids", []) if str(item).strip()] if isinstance(raw_npc.get("known_rule_ids", []), list) else [],
                }
            )
        return normalized

    async def generate_game(
        self,
        group_id: str,
        game_mode: str = "单人",
        player_count: int | None = None,
        player_names: list[str] | None = None,
        player_ids: list[str] | None = None,
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
        
        player_identity = game_data.get("player_identity", "访客")

        session = GameSession(
            group_id=group_id,
            scene_name=game_data.get("scene_name", "未知场景"),
            background=game_data.get("background", ""),
            player_identity=player_identity,
            hidden_truth=game_data.get("hidden_truth", ""),
            game_mode=game_mode,
            rules=game_data.get("rules", []),
            win_condition=game_data.get("win_condition", ""),
            completion_conditions=game_data.get("completion_conditions", {}),
            clues=game_data.get("clues", []),
            core_symbols=game_data.get("core_symbols", []),
            scene_structure=game_data.get("scene_structure", {}),
            npc_guidance=game_data.get("npc_guidance", {}),
        )

        
        # 记录场景结构信息
        if session.scene_structure:
            logger.info(f"场景结构已添加: {session.scene_structure.get('building_type', '未知')}")
        
        # 记录NPC引导信息
        if session.npc_guidance:
            logger.info(f"NPC引导已添加: {session.npc_guidance.get('guidance_method', '未知')}")
        
        # 生成规则网络
        await self._generate_rule_network(session)
        
        # 如果是多人模式，生成多身份系统和协作规则
        if game_mode == "多人":
            try:
                await self._generate_multi_identity_system(
                    session,
                    game_data,
                    player_count=player_count,
                    player_names=player_names,
                    player_ids=player_ids,
                )
                await self._generate_collaborative_rules(session)
            except Exception as e:
                logger.error(f"多人模式身份系统生成失败: {e}")
                raise RuntimeError(f"多人模式身份系统生成失败: {e}") from e


        
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
            )

            collab_data = response.parse_json()
            session.rule_network["collaborative_rules"] = collab_data.get("collaborative_rules", [])
            logger.info(f"协作规则生成成功: {len(session.rule_network['collaborative_rules'])}条")
            
        except Exception as e:
            logger.error(f"生成协作规则失败: {e}")

    @staticmethod
    def _extract_rule_text(rule: object) -> str:
        """提取规则文本。"""
        if isinstance(rule, dict):
            return str(rule.get("text", rule.get("content", "")) or "").strip()
        return str(rule or "").strip()

    def _build_fallback_multiplayer_rule_carriers(
        self,
        session: GameSession,
        assignments: list[AssignmentData],
        common_rules: list[object],
    ) -> list[JsonObject]:
        """在模型未直接生成规则载体时，按身份与区域兜底构造。"""
        rooms: list[str] = []
        scene_structure = session.scene_structure if isinstance(session.scene_structure, dict) else {}
        floors = scene_structure.get("floors", [])
        if isinstance(floors, list):
            for floor in floors:
                if not isinstance(floor, dict):
                    continue
                raw_rooms = floor.get("areas", floor.get("rooms", []))
                if isinstance(raw_rooms, list):
                    for room in raw_rooms:
                        room_name = str(room).strip()
                        if room_name and room_name not in rooms:
                            rooms.append(room_name)
        special_areas = scene_structure.get("special_areas", [])
        if isinstance(special_areas, list):
            for room in special_areas:
                room_name = str(room).strip()
                if room_name and room_name not in rooms:
                    rooms.append(room_name)
        if not rooms:
            rooms = [session.scene_name or "起始位置"]

        carriers: list[JsonObject] = []
        carrier_index = 0

        def add_carrier(
            title: str,
            location: str,
            revealed_rules: list[str],
            visible_to: JsonObject,
            *,
            description: str = "",
            initially_visible: bool = False,
            requires_action: bool = True,
        ) -> None:
            nonlocal carrier_index
            normalized_rules = [text for text in revealed_rules if text]
            if not normalized_rules:
                return
            carriers.append(
                {
                    "carrier_id": f"carrier_{carrier_index}",
                    "title": title,
                    "location": location,
                    "area_scope": location,
                    "visible_to": visible_to,
                    "revealed_rules": normalized_rules,
                    "carrier_type": "规则载体",
                    "description": description or f"你在{location}发现了一份可供核对的纸面记录。",
                    "is_discovered": False,
                    "discovered_by": [],
                    "initially_visible": initially_visible,
                    "requires_action": requires_action,
                }
            )
            carrier_index += 1

        for index, assignment in enumerate(assignments):
            if not isinstance(assignment, dict):
                continue
            player_id = str(assignment.get("player_id", "") or "").strip()
            if not player_id:
                continue
            duty_area = str(assignment.get("duty_area", "") or "").strip() or rooms[index % len(rooms)]
            identity_name = str(assignment.get("identity_name", "岗位记录") or "岗位记录").strip()
            unique_rules_raw = assignment.get("unique_rules", [])
            unique_rule_texts = [
                self._extract_rule_text(rule)
                for rule in unique_rules_raw
                if self._extract_rule_text(rule)
            ] if isinstance(unique_rules_raw, list) else []
            if unique_rule_texts:
                add_carrier(
                    title=f"{identity_name}相关记录",
                    location=duty_area,
                    revealed_rules=unique_rule_texts[:2],
                    visible_to={"player_ids": [player_id]},
                    description=f"这份记录更像是写给{identity_name}的岗位备忘。",
                    initially_visible=False,
                )

        common_rule_texts = [
            self._extract_rule_text(rule)
            for rule in common_rules
            if self._extract_rule_text(rule)
        ]
        for index, rule_text in enumerate(common_rule_texts):
            add_carrier(
                title="公共注意事项",
                location=rooms[index % len(rooms)],
                revealed_rules=[rule_text],
                visible_to={"all_players": True},
                description="这是一条被留在公共区域的注意事项。",
                initially_visible=False,
            )

        return carriers

    def _normalize_rule_carriers(
        self,
        raw_carriers: object,
        session: GameSession,
        assignments: list[AssignmentData],
        common_rules: list[object],
    ) -> list[JsonObject]:
        """归一化模型生成的规则载体，必要时回退本地兜底。"""
        if not isinstance(raw_carriers, list):
            return self._build_fallback_multiplayer_rule_carriers(session, assignments, common_rules)

        carriers: list[JsonObject] = []
        for index, item in enumerate(raw_carriers):
            if not isinstance(item, dict):
                continue
            carrier_id = str(item.get("carrier_id", f"carrier_{index}") or f"carrier_{index}").strip()
            location = str(item.get("location", item.get("area_scope", "")) or "").strip()
            title = str(item.get("title", "规则载体") or "规则载体").strip()
            description = str(item.get("description", "") or "").strip()
            carrier_type = str(item.get("carrier_type", "规则载体") or "规则载体").strip()
            revealed_rules_raw = item.get("revealed_rules", [])
            revealed_rules = [
                self._extract_rule_text(rule)
                for rule in revealed_rules_raw
                if self._extract_rule_text(rule)
            ] if isinstance(revealed_rules_raw, list) else []
            visible_to = item.get("visible_to", {"all_players": True})
            if not isinstance(visible_to, dict):
                visible_to = {"all_players": True}

            carriers.append(
                {
                    "carrier_id": carrier_id or f"carrier_{index}",
                    "title": title,
                    "location": location or session.scene_name or "起始位置",
                    "area_scope": str(item.get("area_scope", location) or location or session.scene_name or "起始位置").strip(),
                    "visible_to": visible_to,
                    "revealed_rules": revealed_rules,
                    "carrier_type": carrier_type,
                    "description": description,
                    "is_discovered": bool(item.get("is_discovered", False)),
                    "discovered_by": [str(x).strip() for x in item.get("discovered_by", []) if str(x).strip()] if isinstance(item.get("discovered_by", []), list) else [],
                    "initially_visible": bool(item.get("initially_visible", False)),
                    "requires_action": bool(item.get("requires_action", True)),
                }
            )

        return carriers or self._build_fallback_multiplayer_rule_carriers(session, assignments, common_rules)

    @staticmethod
    def _build_default_npc_roster(npc_guidance: GameData) -> list[JsonObject]:
        """为 NPC 引导结果生成最小可用 roster。"""
        npc_name = str(npc_guidance.get("npc_name", "引导者") or "引导者").strip()
        npc_role = str(npc_guidance.get("npc_role", "引导 NPC") or "引导 NPC").strip()
        npc_behavior = str(npc_guidance.get("npc_behavior", "") or "").strip()
        npc_attitude = str(npc_guidance.get("npc_attitude", "") or "").strip()
        conversation_intent = str(npc_guidance.get("conversation_intent", "") or "").strip()
        return [
            {
                "npc_id": "guide_0",
                "name": npc_name,
                "role": npc_role,
                "attitude": npc_attitude,
                "home_area": "",
                "duty_areas": [],
                "current_location": "",
                "behavior_logic_summary": npc_behavior or f"{npc_role}正在处理自己的日常事务。",
                "current_goal": conversation_intent or "处理眼前尚未完成的事情",
                "last_action": npc_behavior,
                "audible_signature": "",
                "danger_level": "低",
                "can_speak": True,
                "knowledge_reliability": 0.75,
                "deception_tendency": 0.1,
                "corruption_level": 0.0,
                "current_state": "稳定",
                "bias_tags": [],
                "known_rule_ids": [],
            }
        ]

    async def _generate_multi_identity_system(
        self,
        session: GameSession,
        game_data: GameData,
        player_count: int | None = None,
        player_names: list[str] | None = None,
        player_ids: list[str] | None = None,
    ) -> None:

        """生成多身份系统（多人模式）"""
        # 保留参数以便未来扩展
        _ = game_data

        # 优先使用 QQ 号列表（更稳定），让 LLM 为“每个 QQ 号”生成一条分配结果

        players: list[dict[str, str]] = []
        if isinstance(player_ids, list) and player_ids:
            for i, pid in enumerate(player_ids):
                pid_str = str(pid).strip()
                if not pid_str:
                    continue
                name = ""
                if isinstance(player_names, list) and i < len(player_names):
                    name = str(player_names[i] or "").strip()
                players.append({"player_id": pid_str, "player_name": name})

        desired = len(players) if players else 3
        if not players and isinstance(player_count, int) and player_count > 0:
            desired = max(2, min(4, player_count))

        names_text = "、".join([str(x) for x in (player_names or []) if str(x).strip()])
        if names_text:
            names_text = f"玩家名单：{names_text}"


        system_prompt = f"""你是规则怪谈游戏的多身份系统生成器。你需要为多人模式的每一位玩家分配不同身份。

**多身份系统要求：**

1. **身份多样性**：每个身份在场景中的角色不同
   - 例如医院：新来的护士、实习医生、住院病人、资深护工
   - 例如公寓：新租户、物业管理员、老住户、维修工

2. **规则差异**：每个身份有部分独特规则（2-3条）
   - 有些规则是共同的（所有人都要遵守），但不要默认开场直接告诉所有玩家
   - 有些规则是身份特定的
   - 有些规则可能对立（例如：护士被告知避开某个房间，但医生被要求去检查那个房间）
   - 规则更适合作为后台真相、可发现载体、观察提示、NPC误导信息存在，而不是直接发给玩家
   - 如果规则系统已生成矛盾规则对（同一对 NPC 的对立规则），规则载体和初始观察不应按身份阵营限制接触：玩家应能通过探索、潜入、观察 NPC 行为或从他人处交流，获得对方阵营的规则信息。这样玩家才能利用对方规则设局、下套或诱导违规，提升对抗的策略性与沉浸感

3. **NPC态度**：NPC对不同身份的态度不同
   - 例如：护士长对新护士严厉，对医生恭敬，对病人冷淡

4. **信息不对称**：每个身份知道的信息不完全相同
   - 鼓励玩家之间交流信息
   - 拼凑完整真相需要多个身份的信息

5. **任务与责任区域**：
   - 每个身份都要有一个明确任务
   - 每个身份都要有一个责任区域或重点活动区域
   - 任务和责任区域应影响他更容易接触哪些规则载体和 NPC

6. **初始信息**：
   - 默认不要直接发完整规则正文
   - 可以给初始观察、岗位提醒、模糊暗示、可见规则载体编号
   - 只有在确实合理时才给少量开场可见规则载体

7. **规则载体分布**：
   - 尽量把规则放入场景中的规则载体，而不是直接塞给玩家
   - 不同身份、不同任务区域、不同班次可看到不同载体
   - 某些载体可多人共享，某些只能单人看见
   - 开场可以没有任何直接规则

**输出格式：**
{{
  "assignments": [
    {{
      "player_id": "玩家QQ号",
      "player_name": "玩家昵称",
      "identity_name": "身份名称",
      "identity_description": "身份描述（50字内）",
      "task_brief": "该玩家当前必须完成或优先处理的任务",
      "duty_area": "该玩家负责的区域或主要活动区域",
      "initial_observations": [
        "玩家开场能立刻观察到的现象1",
        "玩家开场能立刻观察到的现象2"
      ],
      "initial_visible_carrier_ids": ["carrier_1"],
      "unique_rules": [
        {{"text": "该身份特有的后台规则1", "is_true": true, "hidden_meaning": "隐藏含义"}},
        {{"text": "该身份特有的后台规则2", "is_true": false, "hidden_meaning": "隐藏含义"}}
      ],
      "npc_attitudes": {{
        "NPC名称1": "对该身份的态度描述",
        "NPC名称2": "对该身份的态度描述"
      }},
      "exclusive_info": "该身份独有的信息或线索"
    }}
  ],
  "common_rules": [
    {{"text": "所有身份共同的后台规则1", "is_true": true, "hidden_meaning": "隐藏含义"}}
  ],
  "rule_carriers": [
    {{
      "carrier_id": "carrier_0",
      "title": "值班备忘",
      "location": "护士站",
      "area_scope": "护士站",
      "visible_to": {{"player_ids": ["玩家QQ号"]}},
      "revealed_rules": ["玩家实际能从该载体读到的规则或注意事项"],
      "carrier_type": "纸质记录/告示/物品标签/口头流言",
      "description": "玩家看到这个载体时的描述",
      "initially_visible": false,
      "requires_action": true
    }}
  ],
  "identity_groups": [
    {{
      "group_name": "同身份或同任务组名称",
      "members": ["player_id_1", "player_id_2"]
    }}
  ],
  "shared_visibility_groups": [
    {{
      "group_name": "共享载体视野组",
      "members": ["player_id_1", "player_id_2"]
    }}
  ]
}}

**重要：**
- 仅返回JSON，不要包含其他文字或标签
- 严禁使用emoji表情符号
- 本次玩家数量：{desired}
- `assignments` 必须包含 {desired} 条
- 每个玩家 2-3 条后台独特规则
- 1-2 条后台共同规则
- 每个玩家必须有 `task_brief`、`duty_area`、`initial_observations`
- `rule_carriers` 应尽量覆盖单人可见、共享可见、公共可见三种情况中的至少两种
- `player_id` 必须与输入完全一致"""



        players_text = "\n".join(
            [
                f"- {p.get('player_id','')} {p.get('player_name','')}".strip()
                for p in players
                if str(p.get("player_id", "")).strip()
            ]
        )
        if players_text:
            players_text = f"玩家列表（必须逐条分配，player_id 必须原样返回）：\n{players_text}"

        user_prompt = f"""请为以下场景生成多身份系统。

场景：{session.scene_name}
背景：{session.background}
玩家身份（单人模式）：{session.player_identity}
隐藏真相：{session.hidden_truth}
{names_text}
{players_text}

请为每位玩家分配一个不同的身份，并生成对应的任务、责任区域、初始观察、后台规则与独有信息。"""



        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            identity_data = response.parse_json()

            # 保存多身份数据到session
            if "multi_identity" not in session.rule_network:
                session.rule_network["multi_identity"] = {}

            assignments = identity_data.get("assignments", [])
            identities = identity_data.get("identities", [])
            common_rules = identity_data.get("common_rules", [])

            # 兼容：优先使用 assignments（按 QQ 号逐人分配），同时也生成 identities 结构便于旧逻辑回退
            normalized_assignments: list[AssignmentData] = []
            if isinstance(assignments, list):
                for a in assignments:
                    if isinstance(a, dict) and str(a.get("player_id", "")).strip():
                        normalized_assignments.append(a)

            if normalized_assignments:
                identities = [
                    {
                        "player_id": str(a.get("player_id", "")).strip(),
                        "player_name": str(a.get("player_name", "") or "").strip(),
                        "identity_name": str(a.get("identity_name", "") or "").strip(),
                        "identity_description": str(a.get("identity_description", "") or "").strip(),
                        "task_brief": str(a.get("task_brief", "") or "").strip(),
                        "duty_area": str(a.get("duty_area", "") or "").strip(),
                        "initial_observations": a.get("initial_observations", []),
                        "initial_visible_carrier_ids": a.get("initial_visible_carrier_ids", []),
                        "unique_rules": self._normalize_rules(
                            a.get("unique_rules", []),
                            default_source=f"{str(a.get('identity_name', '') or '身份简报').strip()}",
                            source_type="identity_brief",
                        ),
                        "npc_attitudes": a.get("npc_attitudes", {}),
                        "exclusive_info": str(a.get("exclusive_info", "") or "").strip(),
                    }
                    for a in normalized_assignments
                ]

            for assignment in normalized_assignments:
                if not isinstance(assignment, dict):
                    continue
                identity_name = str(assignment.get("identity_name", "") or "身份简报").strip()
                assignment["unique_rules"] = self._normalize_rules(
                    assignment.get("unique_rules", []),
                    default_source=identity_name,
                    source_type="identity_brief",
                )

            mi = session.rule_network["multi_identity"]
            mi["assignments"] = normalized_assignments
            mi["identities"] = identities if isinstance(identities, list) else []
            mi["common_rules"] = self._normalize_rules(
                common_rules if isinstance(common_rules, list) else [],
                default_source="多人共同背景",
                source_type="shared_brief",
            )
            mi["identity_groups"] = identity_data.get("identity_groups", []) if isinstance(identity_data.get("identity_groups", []), list) else []
            mi["shared_visibility_groups"] = identity_data.get("shared_visibility_groups", []) if isinstance(identity_data.get("shared_visibility_groups", []), list) else []
            mi["npc_attitudes"] = {
                str(item.get("player_id", "") or "").strip(): item.get("npc_attitudes", {})
                for item in normalized_assignments
                if isinstance(item, dict) and str(item.get("player_id", "") or "").strip()
            }
            mi["rule_carriers"] = self._normalize_rule_carriers(
                identity_data.get("rule_carriers", []),
                session,
                normalized_assignments,
                common_rules if isinstance(common_rules, list) else [],
            )

            count = len(normalized_assignments) if normalized_assignments else len(mi.get("identities", []) or [])
            logger.info(f"多身份系统生成成功: {count}个分配")


            
        except Exception as e:
            logger.error(f"生成多身份系统失败: {e}")

    async def _generate_scene_and_rules(self, game_mode: str) -> GameData:
        """分步生成场景和规则（避免一次生成过多内容）"""
        
        # Step 1: 生成剧情导入和隐藏真相
        logger.info("Step 1: 生成剧情导入和隐藏真相")
        step1_data = await self._generate_plot_and_truth(game_mode)
        
        # Step 2: 生成场景结构
        logger.info("Step 2: 生成场景结构")
        step2_data = await self._generate_scene_structure(step1_data)
        
        # Step 3: 生成规则系统
        logger.info("Step 3: 生成规则系统")
        step3_data = await self._generate_rules_system(step1_data, step2_data, game_mode)
        step3_data["rules"] = self._normalize_rules(
            step3_data.get("rules", []),
            default_source="场景正式规则",
            source_type="system",
        )
        
        # Step 4: 生成NPC引导
        logger.info("Step 4: 生成NPC引导")
        npc_guidance = await self._generate_npc_guidance(step1_data, step2_data, step3_data, game_mode)
        npc_guidance["npc_roster"] = self._normalize_npc_roster(npc_guidance.get("npc_roster", []), npc_guidance)
        
        # 合并所有数据
        game_data = {
            **step1_data,
            **step2_data,
            **step3_data,
            "npc_guidance": npc_guidance,
        }
        
        logger.info(f"场景生成完成: {game_data.get('scene_name', 'Unknown')}")
        return game_data
    
    async def _generate_plot_and_truth(self, game_mode: str) -> GameData:
        """Step 1: 生成剧情导入和隐藏真相"""
        system_prompt = """你是一位精通规则怪谈创作的游戏设计师。你必须严格按照JSON格式返回数据。

**重要：你必须只返回JSON，不要有任何其他文字、解释、标签或markdown代码块。直接输出纯JSON对象。**

**规则怪谈的核心特征：**
- 日常场景中的诡异感
- 表面正常，细节不对劲
- 通过规则暗示危险，而非直接描述恐怖
- 诡异氛围而非恐怖剧情

**创作要求：**

1. **场景选择**：选择一个日常、具体的场景
   - 好的例子：市立医院、老旧公寓楼、24小时便利店、郊区图书馆、老式电影院
   - 避免：废弃的XX、神秘的XX、诡异的XX、阴森的XX
   - 场景名称要具体平实，如"青山医院"、"枫叶公寓"

2. **背景故事**（160-240字）：只介绍场所本身的公共背景
   - 只写场所的年代、用途、规模、服务对象、经营或管理现状、公开传闻与长期存在的异常迹象
   - 使用第三人称客观叙述，不出现“你”“你们”“玩家”，不描述任何人此刻进入、醒来、搬入、报到或接受任务
   - 严禁写规则、守则、禁令、行动建议、任务、目标、通关条件、NPC台词或玩家当下看到的场景
   - 不使用“必须、禁止、不得、不要、务必”等命令式表达
   - 背景只负责回答“这是一个什么地方，它过去和现在大致怎样”，不要承担开场剧情功能

3. **玩家身份**：只写一个与场所自然匹配的日常身份
   - 用一句简洁名词性短语描述，不编造到来原因，不附带任务、目标、规则或剧情

4. **核心象征符号**：1-2个（不要超过2个！）
   - 符号应该是场景中反复出现的元素
   - 例如：数字（房间号、时间）、颜色（红色灯、蓝色门）、声音（钟声、脚步声）

5. **隐藏真相**（150-200字）：解释诡异现象的真相
   - 真相应该合乎逻辑但令人不安
   - 真相解释了为什么会有这些规则
   - 不要过于血腥或直白

**输出格式（必须严格遵守）：**
{
  "scene_name": "场景名称",
  "background": "背景故事",
  "player_identity": "玩家身份",
  "arrival_reason": "可留空，不用于背景或身份展示",
  "core_symbols": ["符号1", "符号2"],
  "hidden_truth": "隐藏真相"
}

**关键要求：**
- 只返回上面的JSON对象，不要有任何其他内容
- 不要使用markdown代码块（不要用```json```）
- 不要添加任何解释或说明
- 不要使用<think>标签
- 严禁使用emoji表情符号"""

        # 提取模式要求到变量，避免 f-string 表达式包含反斜杠（Python 3.10 限制）
        if game_mode == "多人":
            mode_requirements = (
                "多人模式特别要求：\n"
                "1. 场景应支持多种合理身份\n"
                "2. background 仍使用第三人称客观叙述，不出现玩家视角\n"
                "3. player_identity 写成简洁的公共身份概括，个人身份由后续系统生成\n"
                "4. arrival_reason 留空"
            )
        else:
            mode_requirements = (
                "单人模式要求：\n"
                "1. background 使用第三人称客观叙述\n"
                "2. player_identity 只写身份本身\n"
                "3. arrival_reason 留空"
            )

        user_prompt = f"""请创作一个规则怪谈游戏的剧情导入。

游戏模式：{game_mode}

风格要求：
1. 从现实生活中选择具体场所，不受医院、公寓、便利店等常见题材限制
2. 表面正常，细节诡异
3. 用平淡语气描述异常
4. 避免直接的恐怖描写
5. 每次生成不同的场景

{mode_requirements}


请直接返回JSON对象，不要有任何其他文字。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
            )

            data = response.parse_json()

            background = self._sanitize_background_text(data.get("background", ""))
            if len(background) < 80:
                background_response = await self.llm_client.call(
                    prompt=f"""请只重写场所背景。

场景名称：{data.get('scene_name', '')}
原始背景：{data.get('background', '')}

要求：
1. 160-240字，第三人称客观介绍场所的年代、用途、规模、服务对象、经营管理现状、公开传闻和长期异常迹象。
2. 不出现玩家视角、到来过程、身份、任务、目标、规则、规矩、守则、禁令、行动建议、NPC台词或当前场景。
3. 不使用“必须、禁止、不得、不要、务必”等命令式表达。
4. 只输出背景正文。""",
                    system_prompt="你只负责撰写场所公共背景，不写开场剧情、规则、身份、目标或对话。",
                    temperature=0.8,
                )
                background = self._sanitize_background_text(background_response.clean_content)
            data["background"] = background
            data["player_identity"] = self._normalize_free_text(data.get("player_identity", ""))
            data["arrival_reason"] = ""

            # 强制限制核心象征符号最多2个
            if "core_symbols" in data and len(data["core_symbols"]) > 2:
                data["core_symbols"] = data["core_symbols"][:2]
                logger.warning(f"核心象征符号超过2个，已截断为: {data['core_symbols']}")

            logger.info(f"剧情导入生成成功: {data.get('scene_name', 'Unknown')}")
            return data
            
        except Exception as e:
            logger.error(f"生成剧情导入失败: {e}")
            raise Exception(f"生成剧情导入失败: {e}")
    
    async def _generate_scene_structure(self, plot_data: GameData) -> GameData:
        """Step 2: 生成场景结构"""
        system_prompt = """你是一个专业的规则怪谈生成器。请基于剧情导入，生成场景结构。

**要求：**
1. 确定建筑类型（如：医院、学校、公寓、庄园等）
2. 描述建筑的总体布局（如：L型、U型、回字形、多层建筑等）
3. 列出所有楼层（包括地上和地下），每层列出主要区域
4. 列出通道、楼梯、电梯等连接方式
5. 列出特殊区域（如：地下室、天台、禁闭室等）
6. 场景结构应该与剧情导入的背景和氛围相符
7. 除了内部结构，还要额外给出面向玩家的自然语言场景印象，不要写成清单、导览词或系统说明

**输出格式：**
{
  "scene_structure": {
    "building_type": "建筑类型描述",
    "overall_layout": "总体布局描述",
    "floors": [
      {"name": "一楼", "rooms": ["房间1", "房间2"]},
      {"name": "二楼", "rooms": ["房间3", "房间4"]}
    ],
    "connections": ["连接通道1", "连接通道2"],
    "special_areas": ["特殊区域1", "特殊区域2"]
  },
  "scene_impression": "150字左右的纯文本，从玩家视角描述这个地方带来的第一空间印象，不要分点",
  "exploration_hint": "1-2句自然的探索提醒，暗示哪些位置值得留意，不要写成任务列表"
}

**重要：**
- 仅返回JSON，不要包含其他文字
- 严禁使用emoji表情符号
- `scene_impression` 必须是完整段落，不要出现 1. 2. 3. 或项目符号
- `exploration_hint` 必须像叙事中的提醒，不要写“建议你去……”这种教程口吻"""

        user_prompt = f"""请基于以下剧情导入，生成场景结构。

场景：{plot_data.get('scene_name', '')}
背景：{plot_data.get('background', '')}
玩家身份：{plot_data.get('player_identity', '')}

请生成场景结构。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            data = response.parse_json()
            scene_structure = data.get("scene_structure", {})
            if isinstance(scene_structure, dict):
                scene_impression = str(data.get("scene_impression", "") or "").strip()
                exploration_hint = str(data.get("exploration_hint", "") or "").strip()
                if scene_impression:
                    scene_structure["scene_impression"] = scene_impression
                if exploration_hint:
                    scene_structure["exploration_hint"] = exploration_hint
            logger.info(f"场景结构生成成功")
            return data

        except Exception as e:
            logger.error(f"生成场景结构失败: {e}")
            raise Exception(f"生成场景结构失败: {e}")
    
    async def _generate_rules_system(
        self,
        plot_data: GameData,
        structure_data: GameData,
        game_mode: str
    ) -> GameData:
        """Step 3: 生成规则系统"""
        scene_structure = structure_data.get("scene_structure", {})
        
        system_prompt = """你是一位精通规则怪谈创作的游戏设计师。请基于剧情导入、场景结构和隐藏真相，生成规则系统。

**规则设计原则：**

1. **规则数量**：生成5-8条规则

2. **规则与场景呼应**：规则应该与剧情导入和场景结构紧密呼应
   - 规则应该反映场景的历史和异常现象
   - 规则应该与玩家的身份和任务相关

3. **规则类型标记**（重要）：为每条规则标记类型
   - fatal: 即死规则，触犯立即导致死亡（如"午夜12点必须离开地下室"）
   - harmful: 有害规则，触犯会受到惩罚但不立即致命（如被NPC追杀、环境恶化）
   - double_edged: 双刃剑规则，触犯有风险但能获得关键线索或NPC帮助
   - None: 普通规则，让系统根据剧情判断

4. **矛盾规则对**（可选，LLM 自主判断是否需要）：
   当且仅当隐藏真相中存在明确的对抗势力（如两个 NPC 阵营、两种相互排斥的规则体系、场所内的历史对抗）时，才生成矛盾规则对。

   **判断标准**：
   - 隐藏真相中存在两个或以上对立的势力/NPC/派系
   - 双方的诉求直接冲突，无法同时满足
   - 玩家选择遵守一方就会触犯另一方

   **不要生成的情况**：
   - 单纯的误导性规则（不是矛盾规则对）
   - 普通的复杂规则（不是矛盾规则对）
   - 没有明确对抗势力背景时不要强行制造矛盾

   **若生成，必须成对出现**：
   - 规则A：related_npc=X，opposing_npc=Y（遵守X会触犯Y）
   - 规则B：related_npc=Y，opposing_npc=X（遵守Y会触犯X）
   - 两条规则的文本内容直接矛盾，无法同时遵守
   - related_npc 和 opposing_npc 应当是隐藏真相中提及的对抗势力名称（可以是 NPC 名、势力名或角色名），系统会在后续 NPC 引导生成阶段对齐

   **示例**（仅当剧情适合时参考）：
   - 场景：医院，对抗势力：护士长 vs 地下实验室研究员
     - 规则A（related_npc=护士长，opposing_npc=研究员）："午夜前必须完成三楼病房巡查，不得进入地下室"
     - 规则B（related_npc=研究员，opposing_npc=护士长）："每晚必须进入地下室取回实验样本，不得在三楼逗留"

5. **通关条件（win_condition）**：用一句话告诉玩家“怎样算通关”
   - 必须是 12-30 字的单句，可直接判定是否达成，如“活着撑到天亮并离开这栋楼”“找到失踪的同事并带他出去”
   - 只写达成状态本身，不写过程要求、行为限制、注意事项或“否则会怎样”
   - 不使用“并且”“同时”串联多个独立条件；一句里最多一个动作加一个结果
   - 严禁把规则内容塞进 win_condition；规则归规则，通关条件归通关条件

6. **通关结构化硬门槛（completion_conditions）**：与 win_condition 严格对应、可被代码确定性校验的结构化条件
   - 这是一个对象，最多包含四个字段，每个字段都可单独省略，但整体对象必须非空
   - `required_items`：玩家通关时背包必须持有的关键物品名称数组（1-3 个），名称需与线索/物品命名一致，如 `["地下室钥匙", "值班记录本"]`
   - `required_location`：玩家通关时必须身处（或其名称包含）的最终位置字符串，如 `"一楼出口大厅"`，必须来自场景结构里真实存在的区域名
   - `required_action`：玩家通关前必须执行过的目标动作短语（动词+宾语），如 `"用钥匙打开出口大门"`，代码会按文本包含匹配玩家行动历史
   - `required_npc_state`：通关所需的 NPC 态度/状态映射，键为 NPC 的 npc_id 或 name，值为期望的 attitude 或 current_state 关键词，如 `{"guide_0": "友好"}`；不需要时可省略
   - 这些条件合在一起必须等价于 win_condition 描述的达成状态：当且仅当全部条件满足时才算“结构化通关”
   - 不要把规则约束写进这里；这里只描述“通关瞬间玩家应当处于的可观测状态”

7. **规则隐藏逻辑**：规则应该有隐藏的逻辑和真相，需要玩家推理
8. **显式语义字段**（重要）：
   - 用 `condition` 表示触发前提
   - 用 `constraint` 表示玩家需要遵守的行为约束
   - 用 `consequence` 表示违反后的后果
   - 用 `source` 表示规则来源
   - 用 `reliability` 表示这条规则作为玩家可接触信息时的可靠度，范围 0.0~1.0
9. **结构化违规条件**（Task 20，重要）：为每条规则输出 `conditions` 对象，供运行时确定性匹配判定违规事实
   - `time_window`：违规时间窗，如 "22:00-04:00"（表示该时段内违反规则才受罚）；无时间约束时填 null
   - `location`：违规位置（玩家在该位置做某事才违反规则），如 "走廊""地下室"；无位置约束时填 null
   - `action_keywords`：触发违规的动作关键词数组，如 ["跑", "大声喊叫"]；无动作约束时填空数组 []
   - `precondition`：违规前置状态，如 "持有手电筒""未穿制服"；无前置状态约束时填 null
   - 各子字段可省略或填 null/空数组，表示该维度不做约束
   - 运行时会先用 conditions 做确定性匹配（时间窗/位置/动作关键词/前置状态全部满足才算违规），再让 LLM 叙事化后果

**输出格式：**
{
  "rules": [
    {
      "text": "规则1",
      "is_true": true,
      "hidden_meaning": "隐藏含义",
      "condition": "触发条件",
      "conditions": {
        "time_window": "22:00-04:00 或 null",
        "location": "走廊 或 null",
        "action_keywords": ["跑", "大声"],
        "precondition": "持有手电筒 或 null"
      },
      "constraint": "行为约束",
      "consequence": "违反后果",
      "source": "规则来源，如值班守则/NPC口述/广播",
      "reliability": 0.92,
      "rule_type": "fatal/harmful/double_edged/null",
      "related_npc": "NPC名称或null",
      "opposing_npc": "对抗NPC名称或null"
    }
  ],
  "win_condition": "通关条件",
  "completion_conditions": {
    "required_items": ["关键物品A"],
    "required_location": "最终位置名",
    "required_action": "目标动作短语",
    "required_npc_state": {"npc_id": "期望态度或状态"}
  },
  "clues": ["线索1", "线索2", "线索3"]
}

**重要：**
- 仅返回JSON，不要包含其他文字
- 严禁使用emoji表情符号
- rule_type字段必须填写，即使是null
- related_npc和opposing_npc如果没有就填null
- condition、constraint、consequence、source、reliability 必须填写
- conditions 必须填写（可填空对象 {{}}，表示无条件约束，规则文本本身描述约束）
- `completion_conditions` 必须是非空对象，至少包含 `required_items` / `required_location` / `required_action` / `required_npc_state` 中的一项；不需要的字段可省略，但整体不能为空对象"""

        user_prompt = f"""请基于以下信息，生成规则系统。

场景：{plot_data.get('scene_name', '')}
背景：{plot_data.get('background', '')}
玩家身份：{plot_data.get('player_identity', '')}
隐藏真相：{plot_data.get('hidden_truth', '')}

场景结构：
- 建筑类型：{scene_structure.get('building_type', '')}
- 总体布局：{scene_structure.get('overall_layout', '')}

游戏模式：{game_mode}

请生成规则系统。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            data = response.parse_json()
            # 通关结构化硬门槛（Task 22）：必须由 LLM 在生成阶段产出，缺失或畸形直接抛错，禁止兜底
            cc = data.get("completion_conditions")
            if not isinstance(cc, dict) or not cc:
                raise RuntimeError("规则系统未生成结构化通关条件 completion_conditions")
            recognized_keys = {"required_items", "required_location", "required_action", "required_npc_state"}
            if not any(key in cc for key in recognized_keys):
                raise RuntimeError("completion_conditions 至少需要包含一项结构化通关条件")
            logger.info(f"规则系统生成成功: {len(data.get('rules', []))}条规则")
            return data

        except Exception as e:
            logger.error(f"生成规则系统失败: {e}")
            raise Exception(f"生成规则系统失败: {e}")
    
    async def _generate_npc_guidance(
        self,
        plot_data: GameData,
        structure_data: GameData,
        rules_data: GameData,
        game_mode: str,
    ) -> GameData:
        """Step 4: 生成NPC引导和 NPC roster。"""
        system_prompt = """你负责为规则怪谈建立开场时存在的人物与信息入口，但不要撰写开场正文，也不要预写 NPC 台词。

你的任务是先确定“现场有没有人、是谁、正在做什么、为什么会与玩家发生交流、玩家最初能接触到哪些规则信息”。真正说出口的话将在下一阶段根据现场即时生成。

要求：
1. NPC 必须属于这个场所，而不是为了引导玩家临时出现的工具人。他应有自己的工作、麻烦、目的、偏见和注意力焦点。
2. 不要默认使用接待员、值班人、交接员、前台、保安或管理员。只有场景确实需要时才能选择这些角色。
3. NPC 不必主动欢迎或指导玩家。他可以忙于别的事、认错人、避开玩家、向玩家求助、质问玩家、无视玩家，或只因一个具体事件开口。
4. 不要生成教程、任务清单或通关提示。
5. NPC 不掌握完整规则总表和隐藏真相。他只可能零散知道你在 `hinted_rule_texts` 中选出的内容，其余规则他并不清楚。
6. `npc_behavior` 只描述此刻可见的动作、神态、位置和正在处理的具体事情，必须能让读者看清空间关系。
7. `conversation_intent` 只写 NPC 此刻为什么可能开口、想从玩家那里得到什么、想隐瞒什么或想让交流走向哪里；不要写任何直接台词。
8. 只有当这个场所此刻确实不该有人时才使用 `none`（如凌晨的空置楼层）。大多数有人气的场所应选择 natural_language 或 rule_carrier，不要为了省事全部留空。
9. `rule_carrier` 表示现场存在一个玩家可见或可拾取的自然载体（守则、告示、值班记录、便签等）。载体在开场只需“被看见”，内容由玩家主动查看后获得。
10. NPC roster 中的角色必须使用场景结构里真实存在的区域，行为目标必须是世界内目标。

字段要求：

1. guidance_method
   - natural_language：NPC 在场，可能在交流中自然带出零散的注意事项
   - rule_carrier：开场信息主要来自一份现场可见的纸面/物件/记录
   - none：开场既没有人也没有明显的信息载体，玩家完全自行探索

2. npc_behavior
   - 只写可见动作、神态、具体位置和正在处理的事情
   - 不写玩家感受，不写台词，不写“负责引导玩家”

3. conversation_intent
   - 描述 NPC 的交流动机、关注点和隐瞒点
   - 禁止写引号内台词或完整对话

4. hinted_rule_texts
   - 从提供的正式规则中挑选 0-2 条，表示 NPC 平时耳闻或亲身遵守、可能在交流中顺口带出的内容
   - 只填规则原文，改写工作交给下一阶段；natural_language 下建议至少选 1 条
   - none 或 rule_carrier 模式下可留空列表

5. rule_carrier_title / rule_carrier_description
   - 仅在 rule_carrier 下填写
   - title 是物件或文书本身的名称，如“夜班交接单”“四层保洁记录”“值班室抽屉里的便签”
   - description 描述它此刻在现场的位置与状态（贴在哪、压在哪、由谁递来），不要把正文规则完整写出来
   - 40-100 字，1-2 句即可

6. npc_roster
   - 如果 guidance_method 为 none，则允许为空列表
   - 生成 1-3 个可进入运行时模拟的 NPC
   - 角色职业与行为应由场景决定，不要求任何人负责接待或岗位分派
   - 每个 NPC 都要给出岗位区域、行为逻辑摘要、当前目标、开场前刚做过什么、可听特征
   - current_goal 必须是世界内目标，例如“整理夜班登记簿”“巡视四层病房”“确认新来者是否按要求到岗”，不要写“完成开场引导”
   - knowledge_reliability 表示这名 NPC 掌握信息的可靠度，0.0~1.0
   - deception_tendency 表示这名 NPC 故意误导玩家的倾向，0.0~1.0
   - corruption_level 表示这名 NPC 受到异常污染的程度，0.0~1.0
   - bias_tags 表示这名 NPC 的认知偏差，例如“迷信”“服从权威”“排外”“护短”
   - known_rule_ids 可以填写该 NPC 更清楚的规则 ID，不知道就留空

输出 JSON：
{
  "guidance_method": "natural_language 或 rule_carrier 或 none",
  "npc_name": "NPC姓名",
  "npc_role": "NPC角色",
  "npc_attitude": "NPC对玩家的态度或气质，如冷淡、疲惫、急躁、敷衍、和善、戒备",
  "npc_behavior": "玩家眼前看到的开场动作与氛围",
  "conversation_intent": "NPC此刻的交流动机、关注点和隐瞒点",
  "hinted_rule_texts": ["NPC可能顺口带出的规则原文"],
  "npc_dialogue": "留空",
  "rule_carrier_title": "载体名称",
  "rule_carrier_description": "载体此刻在现场的位置与状态",
  "npc_roster": [
    {
      "npc_id": "guide_0",
      "name": "NPC姓名",
      "role": "NPC角色",
      "attitude": "总体态度",
      "home_area": "常驻区域",
      "duty_areas": ["主要活动区域1", "主要活动区域2"],
      "behavior_logic_summary": "单体行动逻辑摘要",
      "current_goal": "世界内当前目标",
      "last_action": "开场前刚做过什么",
      "audible_signature": "玩家可听到的典型动静",
      "danger_level": "低/中/高",
      "can_speak": true,
      "knowledge_reliability": 0.7,
      "deception_tendency": 0.2,
      "corruption_level": 0.1,
      "current_state": "稳定/紧张/被污染/戒备",
      "bias_tags": ["该NPC的认知偏差标签"],
      "known_rule_ids": ["rule_0"]
    }
  ]
}

仅返回 JSON，不要包含其他说明。"""


        scene_structure = structure_data.get("scene_structure", {})
        rules_raw = rules_data.get("rules", []) if isinstance(rules_data, dict) else []
        rule_texts: list[str] = []
        if isinstance(rules_raw, list):
            for rule in rules_raw:
                if isinstance(rule, dict):
                    text = str(rule.get("text", "") or "").strip()
                    if text:
                        rule_texts.append(text)
        user_prompt = f"""请建立开场现场中的人物状态与初始信息入口，不要生成台词。

场景：{plot_data.get('scene_name', '')}
公共背景：{plot_data.get('background', '')}
玩家身份：{plot_data.get('player_identity', '')}
游戏模式：{game_mode}
建筑类型：{scene_structure.get('building_type', '') if isinstance(scene_structure, dict) else ''}
总体布局：{scene_structure.get('overall_layout', '') if isinstance(scene_structure, dict) else ''}
楼层与区域：{scene_structure.get('floors', []) if isinstance(scene_structure, dict) else []}
特殊区域：{scene_structure.get('special_areas', []) if isinstance(scene_structure, dict) else []}

本局正式规则（仅用于挑选 hinted_rule_texts，禁止全部塞给NPC）：
{chr(10).join(f"- {text}" for text in rule_texts) if rule_texts else "- 无"}

不要让角色承担“向玩家解释玩法”的职责。只有场所此刻确实无人时才选择 none。"""


        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            data = response.parse_json()
            data = self._sanitize_npc_guidance_texts(data)
            guidance_method = str(data.get("guidance_method", "natural_language") or "natural_language").strip().lower()
            if guidance_method == "none":
                data["guidance_method"] = "none"
                data["npc_name"] = ""
                data["npc_role"] = ""
                data["npc_attitude"] = ""
                data["conversation_intent"] = ""
                data["hinted_rule_texts"] = []
                data["npc_dialogue"] = ""
                data["rule_carrier_title"] = ""
                data["rule_carrier_description"] = ""
                data["npc_roster"] = []
                logger.info("NPC引导生成成功: none")
                return data

            npc_roster = data.get("npc_roster", [])
            if not isinstance(npc_roster, list) or not npc_roster:
                data["npc_roster"] = self._build_default_npc_roster(data)
            logger.info(f"NPC引导生成成功: {data.get('guidance_method', 'unknown')}")
            return data

        except Exception as e:
            logger.error(f"生成NPC引导失败: {e}")
            fallback = {
                "guidance_method": "none",
                "npc_name": "",
                "npc_role": "",
                "npc_attitude": "",
                "npc_behavior": "",
                "conversation_intent": "",
                "hinted_rule_texts": [],
                "npc_dialogue": "",
                "rule_carrier_title": "",
                "rule_carrier_description": "",
                "npc_roster": [],
            }
            return self._sanitize_npc_guidance_texts(fallback)

