"""行动处理服务 - 处理玩家行动并生成反馈"""
from __future__ import annotations

import logging
from typing import Any

from ...npc_system import NPCMemory, NPCAttitude
from ..llm.client import LLMClient, get_default_max_tokens

from ..game.models import Player, GameSession
from .item_manager import ItemManager

logger = logging.getLogger(__name__)


class ActionResult:
    """行动结果"""
    def __init__(
        self,
        description: str,
        sanity_change: int = 0,
        health_change: int = 0,
        discovered_clues: list[str] | None = None,
        triggered_event: str | None = None,
        is_fatal: bool = False,
        violated_rule: str | None = None,
    ):
        self.description: str = description
        self.sanity_change: int = sanity_change
        self.health_change: int = health_change
        self.discovered_clues: list[str] = discovered_clues or []
        self.triggered_event: str | None = triggered_event
        self.is_fatal: bool = is_fatal
        self.violated_rule: str | None = violated_rule


class ActionProcessor:
    """行动处理器 - 处理玩家行动"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()
        self.item_manager: ItemManager = ItemManager()

    async def process_action(
        self,
        action: str,
        player: Player,
        session: GameSession,
    ) -> ActionResult:
        """
        处理玩家行动
        
        Args:
            action: 行动描述
            player: 玩家对象
            session: 游戏会话
        
        Returns:
            ActionResult 对象
        """
        logger.info(f"处理行动: {player.name} - {action}")
        
        # 首先检查是否是使用物品的行动
        item_used, item_effect_text = self.item_manager.check_and_use_item(action, player, session)
        if item_used:
            logger.info(f"玩家使用了物品，跳过LLM判定")
            # 使用物品不需要LLM判定，直接返回结果
            return ActionResult(
                description=item_effect_text or "你使用了物品。",
                sanity_change=0,
                health_change=0,
                discovered_clues=[],
                triggered_event=None,
                is_fatal=False,
                violated_rule=None,
            )
        
        # 检查是否是休息行动
        is_resting, rest_effect_text, time_cost = self.item_manager.check_and_rest(action, player, session)
        if is_resting:
            logger.info(f"玩家休息了，花费 {time_cost} 分钟")
            # 更新游戏时间
            if session.time_manager:
                elapsed_minutes = session.time_manager.get("elapsed_minutes", 0) + time_cost
                session.time_manager["elapsed_minutes"] = elapsed_minutes
                
                # 更新时间描述
                if elapsed_minutes < 60:
                    session.time_manager["current_time"] = "深夜"
                    session.time_manager["time_description"] = "午夜时分，周围一片死寂"
                elif elapsed_minutes < 180:
                    session.time_manager["current_time"] = "凌晨"
                    session.time_manager["time_description"] = "黎明前的黑暗，空气中弥漫着不安"
                else:
                    session.time_manager["current_time"] = "黎明"
                    session.time_manager["time_description"] = "东方泛起鱼肚白，但黑暗仍未完全消散"
            
            # 休息不需要LLM判定，直接返回结果
            return ActionResult(
                description=rest_effect_text or "你休息了一会儿。",
                sanity_change=0,
                health_change=0,
                discovered_clues=[],
                triggered_event=None,
                is_fatal=False,
                violated_rule=None,
            )
        
        # NPC交互：按“是否在场 + 态度/记忆 + 玩家语气/行为”做动态判定
        npc_result = self._maybe_handle_npc_interaction(action, player, session)
        if npc_result is not None:
            return npc_result




        # 构建上下文
        context = self._build_context(player, session)
        
        # 调用LLM判定行动结果
        result_data = await self._judge_action(action, context)

        
        # 检查是否发现关键物品
        key_item_found = False
        found_items = result_data.get("found_items", [])
        item_details = result_data.get("item_details", {})
        
        if found_items and item_details:
            is_key_item = item_details.get("is_key_item", "否")
            if is_key_item == "是":
                key_item_found = True
                # 添加关键物品到背包
                player.inventory.append({
                    "name": item_details.get("item_name", found_items[0]),
                    "type": item_details.get("item_type", "线索"),
                    "description": item_details.get("item_description", ""),
                    "observation_hint": item_details.get("observation_hint", ""),
                    "is_key_item": True,
                })
            else:
                # 添加普通物品到背包
                for item in found_items:
                    player.inventory.append({
                        "name": item,
                        "type": "物品",
                        "description": "",
                        "is_key_item": False,
                    })
        elif found_items:
            # 添加普通物品到背包
            for item in found_items:
                player.inventory.append({
                    "name": item,
                    "type": "物品",
                    "description": "",
                    "is_key_item": False,
                })
        
        # 创建行动结果
        result = ActionResult(
            description=result_data.get("description", "你执行了行动。"),
            sanity_change=result_data.get("sanity_change", 0),
            health_change=result_data.get("health_change", 0),
            discovered_clues=result_data.get("discovered_clues", []),
            triggered_event=result_data.get("triggered_event"),
            is_fatal=result_data.get("is_fatal", False),
            violated_rule=result_data.get("violated_rule"),
        )
        
        # 应用状态变化
        self._apply_changes(player, result)
        
        # 更新环境记忆
        self._update_environment_memory(action, player, session)
        
        # 检查是否需要规则变异（如果发现关键物品，触发规则变异）
        await self._check_rule_mutation(action, player, session, result, key_item_found)
        
        logger.info(f"行动处理完成: 理智{result.sanity_change:+d}, 体力{result.health_change:+d}, 关键物品={key_item_found}")
        return result

    def _maybe_handle_npc_interaction(self, action: str, player: Player, session: GameSession) -> ActionResult | None:
        """尝试处理 NPC 交互。

        目标：
        - 不再“硬编码 NPC 永远回答/永远知道一切”。
        - NPC 是否回应、回应多少、是否回避，取决于：在场性 + 态度/记忆 + 玩家语气。
        - 行为结果会推进 `environment_state.known_rule_indices`，形成可追踪的客观变化。
        """
        if not action.strip():
            return None

        talk_keywords = ["询问", "问", "打听", "请教", "搭话", "对话", "交谈", "叫住", "喊", "招呼"]
        if not any(k in action for k in talk_keywords):
            return None

        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        npcs = env_state.get("npcs", []) if isinstance(env_state.get("npcs", []), list) else []
        if not npcs:
            return None

        player_loc = str(player.location or "")

        def npc_loc(npc: dict[str, Any]) -> str:
            return str(npc.get("current_location") or npc.get("location") or npc.get("home_location") or "")

        # 选择目标NPC：优先匹配名字，其次取同地点的第一个
        target: dict[str, Any] | None = None
        for npc in npcs:
            if not isinstance(npc, dict):
                continue
            name = str(npc.get("name") or "")
            if name and name in action:
                target = npc
                break

        if target is None:
            same_place = [npc for npc in npcs if isinstance(npc, dict) and npc_loc(npc) == player_loc]
            target = same_place[0] if same_place else None

        if target is None:
            # 玩家在聊天但附近没有任何NPC
            return None

        name = str(target.get("name") or session.npc_guidance.get("npc_name") or "NPC")
        loc = npc_loc(target) or "附近"

        # 不在场：允许玩家“喊人”，但给出符合直觉的反馈
        if player_loc and npc_loc(target) and npc_loc(target) != player_loc:
            return ActionResult(description=f"你朝{loc}的方向叫了叫{name}，回应只有回声。你此刻在{player_loc}，而他不在这里。")

        # 载入/初始化记忆
        mem = NPCMemory.from_dict(target.get("memory", {}) if isinstance(target.get("memory"), dict) else {})
        pid = str(player.player_id)
        mem.initialize_attitude_vector(pid)

        # 语气/方式对态度的即时影响
        polite = any(k in action for k in ["请", "麻烦", "您好", "劳驾", "拜托", "求"]) 
        aggressive = any(k in action for k in ["滚", "闭嘴", "威胁", "砸", "杀", "打", "逼", "掐"]) 

        # 计算帮助意愿
        vec = mem.get_attitude_vector(pid)
        affection = float(vec.get("affection", 50.0))
        trust = float(vec.get("trust", 50.0))
        suspicion = float(vec.get("suspicion", 0.0))
        hostility = float(vec.get("hostility", 0.0))
        fear = float(vec.get("fear", 0.0))

        score = (affection + trust) - (suspicion + hostility * 1.2 + fear * 0.8)
        if polite:
            score += 8
        if aggressive:
            score -= 25

        attitude = mem.get_attitude(pid)

        # 是否在问规则
        ask_rule_keywords = ["规则", "规矩", "守则", "注意事项", "剩下", "其他", "还有", "没说完", "补充"]
        asking_rules = any(k in action for k in ask_rule_keywords)

        # 根据分数决定：0=拒绝/回避，1=少量，2=中等，3=较多
        if hostility >= 60 or score < -20 or attitude in {NPCAttitude.HOSTILE}:
            help_level = 0
        elif suspicion >= 70 or score < 10 or attitude in {NPCAttitude.SUSPICIOUS}:
            help_level = 0
        elif score < 45:
            help_level = 1
        elif score < 85:
            help_level = 2
        else:
            help_level = 3

        # 更新态度向量（记录这次互动带来的变化）
        if aggressive:
            mem.update_attitude_vector(pid, hostility_delta=10, trust_delta=-10, suspicion_delta=8)
        elif polite:
            mem.update_attitude_vector(pid, trust_delta=5, affection_delta=3, suspicion_delta=-2)
        else:
            # 中性互动：轻微降低陌生感
            mem.update_attitude_vector(pid, trust_delta=1)

        # 记录互动
        game_time = 0
        if isinstance(session.time_manager, dict):
            game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)
        mem.record_interaction(pid, "talk", {"action": action, "location": player_loc}, game_time)

        # 写回 NPC 记忆
        target["memory"] = mem.to_dict()

        if not asking_rules:
            # 普通搭话：依据态度给一句“像人”的回应（不强行输出规则）
            if help_level == 0:
                if attitude == NPCAttitude.HOSTILE:
                    text = f"你试着向{loc}的{name}搭话。他抬眼看了你一下，目光像钉子：『别挡路。』"
                elif attitude == NPCAttitude.SUSPICIOUS:
                    text = f"你试着向{loc}的{name}搭话。他没有立刻回答，只反问：『你问这个干什么？』"
                else:
                    text = f"你试着向{loc}的{name}搭话。他像是在听远处的动静，只敷衍地嗯了一声。"
            else:
                npc_dialogue = str((session.npc_guidance or {}).get("npc_dialogue") or "").strip()
                if npc_dialogue:
                    text = f"你试着向{loc}的{name}搭话。{name}低声道：『{npc_dialogue}』"
                else:
                    text = f"你试着向{loc}的{name}搭话。他压低嗓音：『别大声。这里不喜欢热闹。』"
            return ActionResult(description=text)

        # 询问规则：根据态度决定是否补充“新的已知规则”
        known: list[int] = []
        if isinstance(env_state.get("known_rule_indices"), list):
            known = [int(x) for x in env_state.get("known_rule_indices", []) if isinstance(x, int)]

        all_rules: list[str] = [r.get("text", str(r)) for r in (session.rules or [])]
        unknown = [i for i in range(len(all_rules)) if i not in set(known)]

        if help_level == 0 or not unknown:
            if attitude == NPCAttitude.HOSTILE:
                text = f"你压低声音向{loc}的{name}问起规矩。他的手指停在台面上，冷冷地敲了两下：『我没义务教你。』"
            elif attitude == NPCAttitude.SUSPICIOUS:
                text = f"你压低声音向{loc}的{name}问起规矩。他盯着你看了几秒：『你先把刚才那几条记牢。问太多，容易出事。』"
            else:
                text = f"你压低声音向{loc}的{name}问起规矩。他摇了摇头：『现在不方便。』"
            return ActionResult(description=text)

        reveal_count = min(help_level, len(unknown))
        newly = unknown[:reveal_count]
        known2 = sorted(set(known + newly))
        env_state["known_rule_indices"] = known2

        def cn_num(n: int) -> str:
            table = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
            return table[n - 1] if 1 <= n <= len(table) else str(n)

        parts: list[str] = []
        for j, idx in enumerate(newly, 1):
            rule_text = all_rules[idx].strip()
            if rule_text:
                parts.append(f"第{cn_num(j)}，{rule_text}")

        prefix = "你压低声音向{loc}的{name}问起剩下的规矩。".format(loc=loc, name=name)
        if help_level >= 3:
            mid = "他像是权衡了几秒，终于把话说得更明白："
        elif help_level == 2:
            mid = "他不耐烦地叹了口气，还是补了两句："
        else:
            mid = "他犹豫了一下，只补了一条："

        text = f"{prefix}{mid}『{'；'.join(parts)}』"
        return ActionResult(description=text)

    def _update_environment_memory(self, action: str, player: Player, session: GameSession) -> None:

        """更新环境记忆"""
        # 记录访问的位置
        if player.location:
            session.add_visited_location(player.location)
        
        # 检测互动的物体（简单的关键词匹配）
        interaction_keywords = ["打开", "关闭", "拿起", "放下", "使用", "检查", "触摸", "推", "拉", "按"]
        for keyword in interaction_keywords:
            if keyword in action:
                # 提取物体名称（简化版）
                words = action.replace(keyword, "").strip().split()
                if words:
                    obj = words[0]
                    session.add_interacted_object(f"{keyword}{obj}")
                break
        
        # 记录时间事件
        if "等待" in action or "休息" in action:
            session.add_time_event(f"{player.name}在{player.location}{action}")
    
    async def _check_rule_mutation(
        self,
        action: str,
        player: Player,
        session: GameSession,
        result: ActionResult,
        key_item_found: bool = False,
    ) -> None:
        """检查是否需要规则变异（基于原版plugin_old.py的精确逻辑）"""
        # 如果理智崩坏，不触发规则变异
        if player.sanity == 0:
            return
        
        # 如果发现关键物品，触发规则变异
        if key_item_found:
            await self._trigger_rule_mutation(session, player, "关键物品")
            return
    
    async def _trigger_rule_mutation(
        self,
        session: GameSession,
        player: Player,
        trigger_reason: str = "随机",
    ) -> dict[str, Any]:
        """触发规则变异（基于原版plugin_old.py的精确逻辑）
        
        Returns:
            包含变异信息的字典，如果不需要变异则返回空字典
        """
        if not session.rules:
            return {}
        
        # 收集所有玩家的行动和推理历史
        all_actions = []
        all_reasoning = []
        for p in session.players.values():
            all_actions.extend([a.get("action", "") for a in p.action_history])
            all_reasoning.extend(p.reasoning_history)
        
        # 第一步：评估是否需要规则变异
        evaluation_prompt = f"""
你是规则怪谈的裁判。请根据以下信息，判断是否需要让规则发生变化。

触发原因：{trigger_reason}
场景：{session.scene_name}
原始规则：{[r.get("text", str(r)) for r in session.rules]}
隐藏真相：{session.hidden_truth}
通关条件：{session.win_condition}
玩家行动记录：{all_actions[-10:] if len(all_actions) > 10 else all_actions}
玩家推理记录：{all_reasoning[-10:] if len(all_reasoning) > 10 else all_reasoning}

判断标准（根据剧情推进来判断是否需要规则变化）：
1. **贴合剧情推进**：规则变化应该与当前的剧情发展相匹配，在合适的时机出现
2. **发现的合理性**：玩家发现的物品、信息或触发的事件应该能够自然地引出规则变化
3. **增强紧张感**：规则变化应该能够增强游戏的紧张感和悬疑感，让玩家感到不安

**特别注意**：
- 仅仅发现普通物品（如笔记本、钥匙、工具等）不足以触发规则变化，除非这些物品包含了重要信息
- 仅仅进入新房间或新区域不足以触发规则变化，除非这个区域有特殊意义
- 仅仅进行常规探索或观察不足以触发规则变化
- 规则变化应该让玩家感到"原来如此"或"事情不对劲"，而非"怎么又变了"
- 规则变化不是必须的，如果当前剧情不需要规则变化，就不要强行变化
- **规则变化与玩家是否推理出规则的影响无关，玩家没推理出来就没推理出来，不要为了引导玩家而变化规则**

如果规则变化是必要的，请详细说明原因；如果不需要变化，请详细说明为什么当前不需要变化。

请返回JSON格式：
{{
  "should_mutate": "是/否",
  "reason": "详细说明是否需要规则变化的原因，必须具体说明玩家的行动或推理如何与剧情推进相关",
  "mutation_type": "如果需要变化，说明变化的类型（如：增加新规则/修改现有规则/规则冲突）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        try:
            evaluation_response = await self.llm_client.call(
                prompt=evaluation_prompt,
                temperature=0.7,
                max_tokens=get_default_max_tokens(),
            )
            evaluation_data = evaluation_response.parse_json()
        except Exception as e:
            logger.error(f"规则变异评估失败: {e}")
            return {}
        
        if evaluation_data.get("should_mutate") != "是":
            logger.info(f"评估结果：不需要规则变化 - {evaluation_data.get('reason', '')}")
            return {}
        
        logger.info(f"评估结果：需要规则变化 - {evaluation_data.get('reason', '')}")
        
        # 第二步：生成变异后的规则
        mutation_prompt = f"""
基于以下原始规则和玩家至今的行动记录，模拟'场景意识'对玩家行为的反应，对其中1-2条规则进行细微但令人不安的篡改或增添一条'补充条款'，使其看起来像是早已存在但被忽视了。

触发原因：{trigger_reason}
变异类型：{evaluation_data.get('mutation_type', '未知')}
原始规则：{[r.get("text", str(r)) for r in session.rules]}
玩家行动记录：{all_actions[-5:] if len(all_actions) > 5 else all_actions}
玩家推理记录：{all_reasoning[-5:] if len(all_reasoning) > 5 else all_reasoning}

要求：
1. 对1-2条规则进行细微的篡改或补充
2. 篡改应该令人不安，暗示规则本身是有意识的、会学习的
3. 篡改后的规则应该看起来像是原本就存在，只是之前被玩家忽视了
4. **规则变化方式**：
   - 可以让新规则与原本的旧规则冲突（如：原本说"禁止进入404室"，现在改为"必须进入404室"）
   - 可以更改条件（如：原本"禁止在22:00-06:00期间离开房间"，现在改为"禁止在24:00-08:00期间离开房间"）
   - 可以增加新的限制或放宽限制
   - 要贴合剧情推进，让玩家感到规则在根据他们的行为调整
5. **新规则必须简洁、直接，每条规则严格控制在30-50字之间**
6. **只说明禁止、允许或要求做的行为，不解释原因**
7. **使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX**
8. **严禁在规则中包含"如果"、"鉴于"、"因为"、"所以"等解释性词语**
9. **严禁在规则中包含多个句子或分号，每条规则只能是一个简单句**
10. **严禁在规则中添加背景故事或额外说明**
11. 返回格式：{{"mutated_rules": ["新规则文本"], "hint": "一句暗示规则已变的低语（如：墙上的文字似乎更潦草了）"}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        try:
            mutation_response = await self.llm_client.call(
                prompt=mutation_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            mutation_data = mutation_response.parse_json()
            
            mutated_rules = mutation_data.get("mutated_rules", [])
            hint = mutation_data.get("hint", "")
            
            if mutated_rules:
                old_rules = [r.get("text", str(r)) for r in session.rules]
                
                # 更新规则
                session.rules = [{"text": rule} for rule in mutated_rules]
                
                # 记录变异
                session.add_rule_mutation(
                    old_rule=str(old_rules),
                    new_rule=str(mutated_rules),
                    reason=trigger_reason,
                )
                
                logger.info(f"规则变异成功: {old_rules} -> {mutated_rules}")
                
                return {
                    "hint": hint,
                    "old_rules": old_rules,
                    "new_rules": mutated_rules,
                }
            
        except Exception as e:
            logger.error(f"规则变异生成失败: {e}")
        
        return {}

    def _build_context(self, player: Player, session: GameSession) -> dict[str, Any]:
        """构建行动判定上下文（尽量保证“图里有什么，行动里也承认有什么”）"""

        # 场景结构只传摘要，避免 prompt 过长
        ss = session.scene_structure or {}
        scene_structure_summary = {
            "building_type": ss.get("building_type", ""),
            "overall_layout": ss.get("overall_layout", ""),
            "special_areas": ss.get("special_areas", [])[:8] if isinstance(ss.get("special_areas"), list) else ss.get("special_areas", []),
        }

        npc_guidance = session.npc_guidance or {}
        npc_guidance_summary = {
            "guidance_method": npc_guidance.get("guidance_method", ""),
            "npc_name": npc_guidance.get("npc_name", ""),
            "npc_role": npc_guidance.get("npc_role", ""),
            "npc_attitude": npc_guidance.get("npc_attitude", ""),
            "npc_behavior": npc_guidance.get("npc_behavior", ""),
            "npc_dialogue": npc_guidance.get("npc_dialogue", ""),
        }

        env_state = session.environment_state or {}

        # 优先基于结构化的 env_state['npcs'] 推断“当前在场NPC”，避免出现叙事割裂
        npcs_present: list[dict[str, Any]] = []
        if isinstance(env_state, dict):
            npcs = env_state.get("npcs", [])
            if isinstance(npcs, list):
                for npc in npcs:
                    if not isinstance(npc, dict):
                        continue
                    npc_location = str(npc.get("current_location") or npc.get("location") or "")
                    if npc_location and npc_location == str(player.location or ""):
                        npcs_present.append(
                            {
                                "name": npc.get("name", ""),
                                "role": npc.get("role", ""),
                                "attitude": npc.get("attitude", ""),
                                "location": npc_location,
                                "danger_level": npc.get("danger_level", ""),
                            }
                        )

            # 兼容旧字段：如果没有结构化NPC，就回退到 npcs_present
            if not npcs_present and isinstance(env_state.get("npcs_present"), list):
                npcs_present = env_state.get("npcs_present", [])

        return {
            "scene_name": session.scene_name,
            "background": session.background,
            "rules": [r.get("text", str(r)) for r in session.rules],
            "hidden_truth": session.hidden_truth,
            "player_sanity": player.sanity,
            "player_health": player.health,
            "player_location": player.location,
            "time": session.time_manager or {},
            "scene_structure": scene_structure_summary,
            "npc_guidance": npc_guidance_summary,
            "npcs_present": npcs_present,
            "recent_actions": [a.get("action", "") for a in player.action_history[-3:]],
        }



    async def _judge_action(self, action: str, context: dict[str, Any]) -> dict[str, Any]:
        """使用LLM判定行动结果（支持理智值动态描述和关键物品系统）"""
        
        # 根据理智值构建描述风格提示
        sanity = context['player_sanity']
        if sanity == 0:
            sanity_style = """
**理智崩坏模式（理智值=0）**：
- 直接与玩家对话，使用第二人称"你"
- 否认"死亡"概念，描述为"接纳"、"融合"、"永恒"
- 暗示规则是牢笼，打破它才能获得自由
- 描述充满诱导性，试图颠覆玩家的全部逻辑
- 大量使用核心象征符号
- 描述应该让玩家感到"安心"和"解脱"
- 语气温柔但诡异，充满暗示和诱导
- 例如："你终于明白了，那些规则不过是束缚你的枷锁。放下它们，接纳真实的自己..."
"""
        elif sanity < 30:
            sanity_style = """
**理智低下模式（理智值<30）**：
- 描述开始变得混乱和恐怖
- 出现幻觉和错觉（墙壁在呼吸、影子在移动、听到不存在的声音）
- 时间和空间感知混乱（走廊变得无限长、房间的形状在扭曲）
- 声音变得扭曲，颜色变得诡异（红色变得刺眼、黑暗中有东西在蠕动）
- 开始怀疑自己的感知（"这是真的吗？还是我的幻觉？"）
- 核心象征符号频繁出现，变得扭曲和诡异
- 描述充满不安和恐惧，但不要直接说"你感到恐惧"
- 例如："走廊的尽头似乎在远离你，墙上的裂缝像是在呼吸，你听到了低语声，但转头却什么也看不到..."
"""
        elif sanity < 70:
            sanity_style = """
**理智中等模式（理智值40-70）**：
- 描述开始出现混乱和恐惧元素
- 偶尔出现轻微幻觉（影子的形状不太对、声音听起来很远）
- 感官变得敏感（注意到更多细节、声音变得刺耳）
- 注意到更多诡异的细节（墙上的污渍像是某种图案、空气中有奇怪的味道）
- 核心象征符号偶尔出现
- 描述带有紧张和不安，但仍保持一定的理性
- 例如："你注意到墙上的污渍形成了奇怪的图案，空气中弥漫着一股说不出的味道，让你感到不适..."
"""
        else:
            sanity_style = """
**理智正常模式（理智值>70）**：
- 描述客观清晰、冷静理性
- 感官描述准确（视觉、听觉、嗅觉、触觉、味觉）
- 逻辑清晰，注意到环境的细节
- 核心象征符号自然融入场景
- 描述平静但带有潜在的不安（暗示危险但不直接说明）
- 例如："房间里很安静，只有远处传来的滴水声。墙上挂着一幅画，画中的人物似乎在注视着你..."
"""
        
        system_prompt = f"""你是规则怪谈游戏的行动判定系统。你需要根据玩家的行动和游戏规则，判定行动的结果。

{sanity_style}

**判定原则**：
1. 检查行动是否违反规则
2. 根据隐藏真相判断行动的真实后果
3. 表面安全的行动可能危险，表面危险的行动可能安全
4. 使用感官描述而非状态描述（不要说"你感到恐惧"，而是描述让人恐惧的场景）
5. 营造恐怖和不安的氛围
6. **根据玩家当前理智值（{sanity}/100）调整描述风格**

**理智值变化规则**：
- 违反规则：-10到-30
- 目睹恐怖场景：-5到-15
- 发现真相线索：-3到-10
- 安全的探索：-1到-3
- 使用关键物品：+5到+15（用美好的语言描述，让玩家感到"安心"和"解脱"）

**体力值变化规则**：
- 受伤：-10到-50
- 剧烈运动：-5到-15
- 休息：+5到+10
- 死亡：-100

**关键物品系统（非常重要）**：
- 关键物品是能够触发规则变异的重要物品
- 只有极少数物品应该是关键物品（如：带有奇怪符号的物品、与场景历史相关的物品、暗示真相的物品等）
- 普通物品（如笔记本、钥匙、工具等）不应该是关键物品
- 关键物品的发现应该与剧情推进相关

返回JSON格式：
{{
    "description": "详细的场景描述（200-300字，根据理智值调整风格，包含视觉、听觉、触觉等多感官体验）",
    "sanity_change": -5,
    "health_change": 0,
    "discovered_clues": ["发现的线索"],
    "found_items": ["发现的物品列表（如果有）"],
    "item_details": {{
        "item_name": "物品名称",
        "item_type": "物品类型（线索/工具/物资/其他）",
        "item_description": "物品的详细描述",
        "observation_hint": "物品的观察描述（令人不安的细节或暗示）",
        "is_key_item": "是否为关键物品（是/否）"
    }},
    "triggered_event": "触发的事件描述",
    "is_fatal": false,
    "violated_rule": "违反的规则（如果有）"
}}"""

        user_prompt = f"""场景：{context['scene_name']}
背景：{context['background']}

规则：
{chr(10).join(f"{i+1}. {r}" for i, r in enumerate(context['rules']))}

隐藏真相：{context['hidden_truth']}

玩家状态：
- 理智：{context['player_sanity']}/100
- 体力：{context['player_health']}/100
- 位置：{context['player_location']}

最近行动：
{chr(10).join(f"- {a}" for a in context['recent_actions']) if context['recent_actions'] else "无"}

玩家行动：{action}

请判定行动结果，并根据玩家理智值（{sanity}/100）调整描述风格。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            
            return response.parse_json()
            
        except Exception as e:
            logger.error(f"判定行动失败: {e}")
            # 根据理智值返回不同的默认描述
            if sanity == 0:
                default_desc = f"你{action}。一切都变得如此清晰，那些所谓的'规则'不过是虚妄的束缚。你感到前所未有的自由和解脱..."
            elif sanity < 30:
                default_desc = f"你{action}。周围的一切开始扭曲，墙壁在呼吸，影子在蠕动。你听到了低语声，但不知道是从哪里传来的..."
            elif sanity < 70:
                default_desc = f"你{action}。空气中弥漫着一股奇怪的味道，让你感到不适。你注意到周围的细节变得格外清晰..."
            else:
                default_desc = f"你{action}。周围的环境似乎没有什么变化，但你感觉有些不安。"
            
            return {
                "description": default_desc,
                "sanity_change": -2,
                "health_change": 0,
                "discovered_clues": [],
                "found_items": [],
                "item_details": {},
                "triggered_event": None,
                "is_fatal": False,
                "violated_rule": None,
            }

    def _apply_changes(self, player: Player, result: ActionResult) -> None:
        """应用状态变化"""
        player.sanity = max(0, min(100, player.sanity + result.sanity_change))
        player.health = max(0, min(100, player.health + result.health_change))
        
        # 添加发现的线索到背包
        for clue in result.discovered_clues:
            player.inventory.append({
                "type": "clue",
                "name": clue,
                "description": "一条重要的线索",
            })
        
        # 检查死亡
        if result.is_fatal or player.health <= 0:
            from ..game.models import PlayerStatus
            player.status = PlayerStatus.DEAD
