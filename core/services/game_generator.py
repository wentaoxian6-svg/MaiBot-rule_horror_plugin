"""游戏生成服务 - 生成场景、规则、背景故事"""
from __future__ import annotations

import logging
from typing import Any


from ..llm.client import LLMClient
from ..game.models import GameSession

logger = logging.getLogger(__name__)


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
            await self._generate_multi_identity_system(
                session,
                game_data,
                player_count=player_count,
                player_names=player_names,
                player_ids=player_ids,
            )
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

    async def _generate_multi_identity_system(
        self,
        session: GameSession,
        game_data: dict[str, Any],
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
   - 有些规则是共同的（所有人都要遵守）
   - 有些规则是身份特定的
   - 有些规则可能对立（例如：护士被告知避开某个房间，但医生被要求去检查那个房间）

3. **NPC态度**：NPC对不同身份的态度不同
   - 例如：护士长对新护士严厉，对医生恭敬，对病人冷淡

4. **信息不对称**：每个身份知道的信息不完全相同
   - 鼓励玩家之间交流信息
   - 拼凑完整真相需要多个身份的信息

**输出格式：**
{{
  "assignments": [
    {{
      "player_id": "玩家QQ号",
      "player_name": "玩家昵称",
      "identity_name": "身份名称",
      "identity_description": "身份描述（50字内）",
      "unique_rules": [
        {{"text": "该身份特有的规则1", "is_true": true, "hidden_meaning": "隐藏含义"}},
        {{"text": "该身份特有的规则2", "is_true": false, "hidden_meaning": "隐藏含义"}}
      ],
      "npc_attitudes": {{
        "NPC名称1": "对该身份的态度描述",
        "NPC名称2": "对该身份的态度描述"
      }},
      "exclusive_info": "该身份独有的信息或线索"
    }}
  ],
  "common_rules": [
    {{"text": "所有身份共同的规则1", "is_true": true, "hidden_meaning": "隐藏含义"}}
  ]
}}

**重要：**
- 仅返回JSON，不要包含其他文字或标签
- 严禁使用emoji表情符号
- 本次玩家数量：{desired}
- `assignments` 必须包含 {desired} 条
- 每个玩家 2-3 条独特规则
- 1-2 条共同规则
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

请为每位玩家分配一个不同的身份，并生成对应的个人规则与独有信息。"""



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
            normalized_assignments: list[dict[str, Any]] = []
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

            # 多人模式规则展示/判定：优先使用共同规则作为“公用规则表”
            if isinstance(common_rules, list) and common_rules:
                session.rules = common_rules

            count = len(normalized_assignments) if normalized_assignments else len(mi.get("identities", []) or [])
            logger.info(f"多身份系统生成成功: {count}个分配")


            
        except Exception as e:
            logger.error(f"生成多身份系统失败: {e}")

    async def _generate_scene_and_rules(self, game_mode: str) -> dict[str, Any]:
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
    
    async def _generate_plot_and_truth(self, game_mode: str) -> dict[str, Any]:
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

{"多人模式特别要求：\n1. 场景应该支持多种不同身份（如医院可以有护士、医生、病人、护工等）\n2. arrival_reason 使用第二人称复数‘你们’，描述一行人来到场景的共同原因" if game_mode == "多人" else ""}


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
    
    async def _generate_scene_structure(self, plot_data: dict[str, Any]) -> dict[str, Any]:
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
        plot_data: dict[str, Any],
        structure_data: dict[str, Any],
        game_mode: str
    ) -> dict[str, Any]:
        """Step 3: 生成规则系统"""
        scene_structure = structure_data.get("scene_structure", {})
        
        system_prompt = """你是一位精通规则怪谈创作的游戏设计师。请基于剧情导入、场景结构和隐藏真相，生成规则系统。

**规则设计原则：**

1. **规则数量**：生成5-8条规则

2. **规则与场景呼应**：规则应该与剧情导入和场景结构紧密呼应
   - 规则应该反映场景的历史和异常现象
   - 规则应该与玩家的身份和任务相关

3. **通关条件**：设定明确的通关条件
   - 如：在规定时间内找到出口、收集特定物品、存活到天亮等
   - 通关条件应该与规则和真相有逻辑关联

4. **规则隐藏逻辑**：规则应该有隐藏的逻辑和真相，需要玩家推理

**输出格式：**
{
  "rules": [
    {"text": "规则1", "is_true": true, "hidden_meaning": "隐藏含义"},
    {"text": "规则2", "is_true": false, "hidden_meaning": "隐藏含义"}
  ],
  "win_condition": "通关条件",
  "clues": ["线索1", "线索2", "线索3"]
}

**重要：**
- 仅返回JSON，不要包含其他文字
- 严禁使用emoji表情符号"""

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
        plot_data: dict[str, Any],
        rules_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Step 4: 生成NPC引导"""
        system_prompt = """你是规则怪谈游戏的NPC引导生成器。请基于场景和规则，生成NPC引导系统。

**NPC引导要求：**

1. **引导方式**：选择一种引导方式
   - natural_language：NPC通过自然对话告知规则/注意事项（更口语、更像真人）
   - rule_carrier：NPC发放书面材料（工作守则、员工手册等）

2. **NPC设定**：
   - NPC姓名：符合场景设定
   - NPC角色：如护士长、物业管理员、前台接待等
   - NPC态度：如警告、提醒、指示等

3. **行为描述**：用第二人称视角描述NPC的动作（50-90字）
   - 例如："物业管理员走向你，递给你一串钥匙和一张纸。他的眼神闪烁不定，似乎有话要说。"

4. **对话内容**（如果是natural_language）：用第一人称对话形式（120-220字）
   - 例如："新来的住户？听着，这栋楼的规矩很多。晚上8点后千万别去9层，那里...不太对劲。"
   - 对话要自然流畅，可以包含规则要点，但要用口语化方式表达
   - **关键一致性约束**：
     - 不要宣称具体数量（禁止出现“有X条规则/规矩/死规则”这种说法）
     - 如果必须强调，使用“有几条规矩/几条注意事项”即可

5. **规则载体**（如果是rule_carrier）：
   - 规则载体标题：如"夜班护士工作守则"
   - 规则载体描述：描述NPC如何发放规则载体（50-100字）

**输出格式：**
{
  "guidance_method": "natural_language 或 rule_carrier",
  "npc_name": "NPC姓名",
  "npc_role": "NPC角色",
  "npc_attitude": "NPC态度",
  "npc_behavior": "NPC行为描述（50-90字，第二人称视角）",
  "npc_dialogue": "NPC对话（如果是natural_language，120-220字，第一人称对话）",
  "rule_carrier_title": "规则载体标题（如果是rule_carrier）",
  "rule_carrier_description": "规则载体描述（如果是rule_carrier，50-100字）"
}

**重要：**
- 仅返回JSON，不要包含其他文字
- 严禁使用emoji表情符号
- npc_behavior和npc_dialogue要严格区分
- npc_behavior用第二人称视角（"他走向你"）
- npc_dialogue用第一人称对话（"听着，..."）
- 不要在npc_dialogue中混入第三人称描述"""


        user_prompt = f"""请基于以下信息，生成NPC引导。

场景：{plot_data.get('scene_name', '')}
玩家身份：{plot_data.get('player_identity', '')}

请生成NPC引导。"""


        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            data = response.parse_json()
            logger.info(f"NPC引导生成成功: {data.get('guidance_method', 'unknown')}")
            return data

        except Exception as e:
            logger.error(f"生成NPC引导失败: {e}")
            # NPC引导失败不影响游戏，返回默认值
            return {
                "guidance_method": "natural_language",
                "npc_name": "引导者",
                "npc_role": "神秘人",
                "npc_attitude": "警告",
                "npc_behavior": "一个神秘的身影出现在你面前。",
                "npc_dialogue": "欢迎来到这里。记住，遵守规则，才能活下去。",
            }

    def _get_default_game(self) -> dict[str, Any]:
        """获取默认游戏（已废弃，不再使用）"""
        raise NotImplementedError("默认场景已移除，请确保 LLM API 正常工作")
