"""游戏生成服务 - 生成场景、规则、背景故事"""
from __future__ import annotations

import logging
from typing import TypeAlias

from ..llm.client import LLMClient
from ..game.models import GameSession

logger = logging.getLogger(__name__)

# 类型定义
GameData: TypeAlias = dict[str, "str | int | float | bool | list | dict | None"]
AssignmentData: TypeAlias = dict[str, "str | list | dict"]
IdentityData: TypeAlias = dict[str, "str | list | dict"]


class GameGenerator:
    """游戏生成器 - 生成完整的规则怪谈游戏"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()

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
        
        # 创建游戏会话
        # 多人模式下：优先把“到来原因”作为对外展示的共同叙事入口，个人身份通过私聊下发
        player_identity = game_data.get("player_identity", "访客")
        if game_mode == "多人":
            arrival_reason = game_data.get("arrival_reason")
            if isinstance(arrival_reason, str) and arrival_reason.strip():
                player_identity = arrival_reason.strip()

        session = GameSession(
            group_id=group_id,
            scene_name=game_data.get("scene_name", "未知场景"),
            background=game_data.get("background", ""),
            player_identity=player_identity,
            hidden_truth=game_data.get("hidden_truth", ""),
            game_mode=game_mode,
            rules=game_data.get("rules", []),
            win_condition=game_data.get("win_condition", ""),
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
        return [
            {
                "npc_id": "guide_0",
                "name": npc_name,
                "role": npc_role,
                "attitude": npc_attitude,
                "home_area": "",
                "duty_areas": [],
                "current_location": "",
                "behavior_logic_summary": npc_behavior or "负责接待新来者、分派任务，并在异常出现前维持现场秩序。",
                "current_goal": "维持当前场域秩序并观察新来者",
                "last_action": "刚结束一次例行巡视",
                "audible_signature": "平稳的脚步声",
                "danger_level": "低",
                "can_speak": True,
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
                        "unique_rules": a.get("unique_rules", []),
                        "npc_attitudes": a.get("npc_attitudes", {}),
                        "exclusive_info": str(a.get("exclusive_info", "") or "").strip(),
                    }
                    for a in normalized_assignments
                ]

            mi = session.rule_network["multi_identity"]
            mi["assignments"] = normalized_assignments
            mi["identities"] = identities if isinstance(identities, list) else []
            mi["common_rules"] = common_rules if isinstance(common_rules, list) else []
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
        
        # Step 4: 生成NPC引导
        logger.info("Step 4: 生成NPC引导")
        npc_guidance = await self._generate_npc_guidance(step1_data, step3_data)
        
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

2. **背景故事**（200-300字）：表面正常的介绍，暗含诡异细节
   - 描述场景的基本情况（建立时间、用途、规模等）
   - 提及一些"奇怪的传闻"或"不成文的规矩"
   - 用平淡的语气描述异常现象（如"员工流动率很高"、"某些房间总是空着"）
   - 不要直接说"恐怖"、"悲剧"、"死亡"，而是用委婉暗示

3. **玩家身份**：普通的日常身份
   - 好的例子：新来的夜班护士、刚入职的便利店员工、新租户、实习生
   - 身份要让玩家觉得"这可能发生在我身上"

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
  "arrival_reason": "到来原因",
  "core_symbols": ["符号1", "符号2"],
  "hidden_truth": "隐藏真相"
}

**关键要求：**
- 只返回上面的JSON对象，不要有任何其他内容
- 不要使用markdown代码块（不要用```json```）
- 不要添加任何解释或说明
- 不要使用<think>标签
- 严禁使用emoji表情符号"""

        user_prompt = f"""请创作一个规则怪谈游戏的剧情导入。

游戏模式：{game_mode}

风格要求：
1. 日常场景（医院、公寓、便利店、图书馆等）
2. 表面正常，细节诡异
3. 用平淡语气描述异常
4. 避免直接的恐怖描写
5. 每次生成不同的场景

{"多人模式特别要求：\n1. 场景应该支持多种不同身份（如医院可以有护士、医生、病人、护工等）\n2. 所有叙述使用第二人称复数'你们'，不要出现'你'、'你的'等单人叙述\n3. arrival_reason 描述一行人来到场景的共同原因\n4. player_identity 描述为'你们各自的身份'或'一行人的不同身份'\n5. background 中使用'你们发现'、'你们注意到'等复数表述" if game_mode == "多人" else "单人模式要求：\n1. 使用第二人称单数'你'、'你的'进行叙述\n2. 描述玩家独自来到场景的原因"}


请直接返回JSON对象，不要有任何其他文字。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
            )

            data = response.parse_json()

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
  }
}

**重要：**
- 仅返回JSON，不要包含其他文字
- 严禁使用emoji表情符号"""

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

4. **矛盾规则对**（可选）：如果剧情有对抗势力（如A vs B），可以创建矛盾规则对
   - 规则A代表势力X，规则B代表势力Y
   - 两条规则直接矛盾，无法同时遵守
   - 标记related_npc（该规则代表谁）和opposing_npc（对抗谁）

5. **通关条件**：设定明确的通关条件
   - 如：在规定时间内找到出口、收集特定物品、存活到天亮等
   - 通关条件应该与规则和真相有逻辑关联

6. **规则隐藏逻辑**：规则应该有隐藏的逻辑和真相，需要玩家推理

**输出格式：**
{
  "rules": [
    {
      "text": "规则1",
      "is_true": true,
      "hidden_meaning": "隐藏含义",
      "rule_type": "fatal/harmful/double_edged/null",
      "related_npc": "NPC名称或null",
      "opposing_npc": "对抗NPC名称或null"
    }
  ],
  "win_condition": "通关条件",
  "clues": ["线索1", "线索2", "线索3"]
}

**重要：**
- 仅返回JSON，不要包含其他文字
- 严禁使用emoji表情符号
- rule_type字段必须填写，即使是null
- related_npc和opposing_npc如果没有就填null"""

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
            logger.info(f"规则系统生成成功: {len(data.get('rules', []))}条规则")
            return data

        except Exception as e:
            logger.error(f"生成规则系统失败: {e}")
            raise Exception(f"生成规则系统失败: {e}")
    
    async def _generate_npc_guidance(
        self,
        plot_data: GameData,
        rules_data: GameData
    ) -> GameData:
        """Step 4: 生成NPC引导和 NPC roster。"""
        system_prompt = """你是规则怪谈游戏的开场遭遇生成器。请基于场景、隐藏真相和规则，为玩家生成一个更像真实剧情的 NPC 初次接触片段。

你的目标不是“把规则宣布出来”，而是生成一个自然、沉浸、符合场景的开场遭遇：

1. 开场不一定必须有 NPC。
   - 可以有人接待、交接、分派任务
   - 也可以完全没有 NPC，由玩家独自在空房间、值班室、病床、休息室、储物间等地点醒来或恢复意识，再自行探索
2. 如果有 NPC，NPC 首要职责应是接待、分派任务、交接班、安排岗位、提醒异常、制造不安感，而不是像系统公告一样逐条宣读规则。
3. 开场大多数情况下不要直接完整告诉玩家规则。
4. 如果 NPC 开口提到注意事项，可以是零碎、口语化、带情绪或带个人立场的提醒，不要整理成清单。
5. natural_language 模式下允许 NPC 说出一部分规则性内容，也允许夹杂无关提醒、误导、虚假规则或彼此矛盾的说法；这些内容只是剧情中的口述信息，不代表系统确认后的规则。
6. natural_language 模式下，禁止 NPC 在开场直接完整宣读规则总表，也不要输出编号式、条文化、培训手册式的整段说明。
7. 允许 NPC 什么都不明说，只给任务、态度、暗示、误导、回避或模棱两可的话。
8. 如果使用 rule_carrier，也不要把载体内容完整展开；只描述 NPC 如何把某份纸面材料、便签、值班记录、工作守则留给玩家，真正内容留给后续探索。
9. 如果没有 NPC，开场重点应放在：
   - 玩家醒来时的身体感觉、环境异样、光线、声音、气味
   - 房间里留下的痕迹、物件、纸张、广播、门外动静
   - “为什么这里只有我”“刚刚这里发生了什么”的不安感
10. 对话必须像场景中的人说出来的话，不能像旁白总结、系统提示、玩法教程或客服说明。
11. 严禁出现以下表达：
   - “接下来你要遵守以下规则”
   - “这里有几条规则/一共X条规则”
   - “系统会……”
   - “请使用……命令”
   - 任何编号、分点、清单式口吻
12. natural_language 模式下，开场对话更适合：
   - 简短交接
   - 模糊警告
   - 任务催促
   - 不完整说明
   - 带个人情绪的抱怨或回避
13. natural_language 模式下，NPC 可以说“别去后面”“先干活”“十一点后别乱碰冷柜”“听见有人叫你也别急着回头”这类零散说法，但不要把它们包装成正式规则列表。
14. 如果是多人开场，NPC 可以面对“一行人”统一说话，但不要在公开对话里直接暴露每个人的私密身份规则。
15. 要保留危险感、含混感和世界内叙事感，让玩家觉得自己是在进入一个不对劲的地方，而不是在读玩法说明。

字段要求：

1. guidance_method
   - natural_language：NPC 用自然对话、交接、催促、盘问、命令、抱怨、含糊提醒等方式出场
   - rule_carrier：NPC 主要通过交付某种纸面/物件/记录完成开场接触
   - none：开场没有 NPC，玩家独自醒来或独自置身场景中

2. npc_behavior
   - 50-110 字
   - 第二人称视角
   - 只写玩家眼前看到的动作、神态、环境细节，不要写 NPC 台词
   - 如果 guidance_method 为 none，这里改为“玩家醒来或察觉环境异常时，眼前发生的事”

3. npc_dialogue
   - 120-260 字
   - 仅在 natural_language 下填写
   - 必须是 NPC 直接说出口的话
   - 允许停顿、岔开话题、欲言又止、重复强调某个异常点
   - 更适合“交接班”“分派任务”“催促开工”“低声提醒”“不耐烦地纠正”
   - 可以包含少量规则性提醒、误导、虚假说法、矛盾说法或无关紧要的规矩
   - 禁止出现完整规则总表、编号式注意事项、培训手册式说明

4. rule_carrier_title / rule_carrier_description
   - 仅在 rule_carrier 下填写
   - title 是物件或文书本身的名称，如“夜班交接单”“四层保洁记录”“值班室抽屉里的便签”
   - description 只描述它如何被递来、塞来、指给玩家、留在某处，不要把正文规则完整写出来

5. npc_roster
   - 如果 guidance_method 为 none，则允许为空列表
   - 生成 1-3 个可进入运行时模拟的 NPC
   - 至少包含一个负责开场接待或岗位分派的 NPC
   - 其他 NPC 可以是巡逻者、同事、沉默观察者、其他岗位人员
   - 每个 NPC 都要给出岗位区域、行为逻辑摘要、当前目标、开场前刚做过什么、可听特征
   - current_goal 必须是世界内目标，例如“整理夜班登记簿”“巡视四层病房”“确认新来者是否按要求到岗”，不要写“完成开场引导”

输出 JSON：
{
  "guidance_method": "natural_language 或 rule_carrier 或 none",
  "npc_name": "NPC姓名",
  "npc_role": "NPC角色",
  "npc_attitude": "NPC对玩家的态度或气质，如冷淡、疲惫、急躁、敷衍、和善、戒备",
  "npc_behavior": "玩家眼前看到的开场动作与氛围",
  "npc_dialogue": "NPC直接说出口的话",
  "rule_carrier_title": "载体名称",
  "rule_carrier_description": "NPC交付或指向载体的场景描述",
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
      "can_speak": true
    }
  ]
}

仅返回 JSON，不要包含其他说明。"""


        user_prompt = f"""请基于以下信息，生成一个自然、沉浸的 NPC 开场遭遇。

场景：{plot_data.get('scene_name', '')}
玩家身份：{plot_data.get('player_identity', '')}
隐藏真相：{plot_data.get('hidden_truth', '')}
完整规则：{rules_data.get('rules', [])}

如果适合，也可以完全不安排 NPC 出场，让玩家独自醒来并自行探索。"""


        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            data = response.parse_json()
            guidance_method = str(data.get("guidance_method", "natural_language") or "natural_language").strip().lower()
            if guidance_method == "none":
                data["guidance_method"] = "none"
                data["npc_name"] = ""
                data["npc_role"] = ""
                data["npc_attitude"] = ""
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
            # NPC引导失败不影响游戏，返回默认值
            return {
                "guidance_method": "natural_language",
                "npc_name": "值班人",
                "npc_role": "夜班交接员",
                "npc_attitude": "疲惫",
                "npc_behavior": "值班桌后的那个人抬头看了你一眼，指尖还压着没整理完的登记表。他没有立刻招呼你，只是先确认门有没有关好，才朝你招了招手。",
                "npc_dialogue": "新来的？先别站门口，把门带上。今晚人手不够，你先去熟悉自己该待的地方，没事别乱跑。要是听见哪间屋里有动静，又不确定是不是该你管的，就先回来找我，别自作主张。这里有人喜欢把话说一半，你最好学会自己分辨。",
                "npc_roster": self._build_default_npc_roster(
                    {
                        "npc_name": "值班人",
                        "npc_role": "夜班交接员",
                        "npc_attitude": "疲惫",
                        "npc_behavior": "值班桌后的那个人一边整理登记表，一边观察新来者的反应。",
                    }
                ),
            }

