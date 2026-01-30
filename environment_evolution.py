# pyright: reportDeprecated=false
# pyright: reportExplicitAny=false
# pyright: reportAny=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnannotatedClassAttribute=false
# pyright: reportUnusedParameter=false

import os
import json
import random
import re
import aiohttp
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime

from .shared_prompts import RULE_DESIGN_PRINCIPLES

PLUGIN_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(PLUGIN_DIR, "data")


class EnvironmentEvolutionSystem:
    """环境演化系统 - 独立控制游戏环境、NPC和随机事件"""
    
    def __init__(self, game_states: Dict[str, Any]):
        self.game_states = game_states
        
    async def initialize_environment(self, group_id: str, scene_type: str, 
                                      player_identity: str, building_type: str) -> Dict[str, Any]:
        """初始化环境演化系统"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return {}
        
        environment_data = {
            "npcs": [],
            "time": {
                "current_time": "深夜",
                "elapsed_minutes": 0,
                "last_update": datetime.now().isoformat(),
                "time_phase": "午夜"
            },
            "environment_state": {
                "lighting": "昏暗",
                "temperature": "寒冷",
                "sounds": ["寂静"],
                "smells": ["霉味"],
                "atmosphere": "压抑"
            },
            "active_events": [],
            "event_history": [],
            "npc_interactions": [],
            "environmental_changes": [],
            "identity_system": {
                "current_identity": player_identity,
                "identity_history": [player_identity],
                "access_permissions": {},
                "identity_guides": {}
            }
        }
        
        game_state["environment_evolution"] = environment_data
        
        return environment_data
    
    async def generate_npcs(self, group_id: str, scene_type: str, 
                            player_identity: str, building_type: str,
                            api_url: str, api_key: str, model_list: List[str],
                            current_model_index: int, temperature: float,
                            game_mode: str = "单人") -> List[Dict[str, Any]]:
        """生成NPC角色"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return []
        
        num_npcs = random.randint(3, 5)
        all_npcs = []
        existing_roles = []
        
        for i in range(num_npcs):
            existing_roles_text = ", ".join(existing_roles) if existing_roles else "无"
            
            game_mode_text = f"游戏模式：{game_mode}" if game_mode else "游戏模式：单人"
            
            prompt = f"""
你是一位规则怪谈游戏设计师。请为以下场景生成1个NPC角色。

场景类型：{scene_type}
建筑类型：{building_type}
玩家身份：{player_identity}
{game_mode_text}
背景故事：{game_state.get('background', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}

已生成的NPC角色：{existing_roles_text}

**NPC设计要求：**

1. **角色多样性**：NPC应该有不同的身份和角色（如：医生、护士、病人、保安、清洁工等），请避免与已生成的NPC角色重复
2. **性格特征**：每个NPC应该有独特的性格特征
3. **行为模式**：描述NPC的日常行为和活动规律
4. **与真相的关系**：每个NPC与隐藏真相的关系（知情者、受害者、加害者、旁观者等）
5. **对玩家的态度**：NPC对玩家的态度（友好、敌对、中立、神秘等）
6. **特殊能力或限制**：NPC是否有特殊能力或受到某种限制
7. **出现地点**：NPC通常出现的地点
8. **出现时间**：NPC通常出现的时间段

**多人模式NPC设计（非常重要）：**
- 如果游戏模式是"多人"，请考虑NPC对不同玩家的态度可能不同
- NPC可能对某种身份的玩家友好，对另一种身份的玩家敌对
- 或者NPC对第一个遇到的玩家伪装友好，对后续玩家暴露敌意
- 或者NPC根据玩家的行为动态调整态度
- 请在"attitude_to_player"字段中描述这种动态关系，例如：
  * "对医生玩家友好，对病人玩家敌对"
  * "对第一个遇到的玩家伪装友好，对后续玩家暴露敌意"
  * "对遵守规则的玩家友好，对违反规则的玩家敌对"
  * "一视同仁，对所有玩家态度相同"

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "npcs": [
    {{
      "name": "NPC姓名",
      "role": "角色身份",
      "personality": "性格特征",
      "behavior": "行为模式",
      "truth_relation": "与真相的关系",
      "attitude_to_player": "对玩家的态度（多人模式：描述对不同玩家的动态关系）",
      "special_ability": "特殊能力或限制",
      "location": "通常出现的地点",
      "time_appearance": "通常出现的时间段",
      "dialogue_examples": ["对话示例1", "对话示例2"],
      "danger_level": "危险等级（低/中/高/极高）",
      "movement_history": []
    }}
  ]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
            """
            
            llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
            if not llm_response:
                print(f"[环境演化] 生成第{i+1}个NPC失败：LLM API调用失败")
                continue
            
            result = self._parse_llm_json_response(llm_response, "生成NPC")
            if not result:
                print(f"[环境演化] 生成第{i+1}个NPC失败：JSON解析失败")
                continue
            
            npcs = result.get("npcs", [])
            if npcs:
                for npc in npcs:
                    role = npc.get("role", "")
                    if role and role not in existing_roles:
                        existing_roles.append(role)
                all_npcs.extend(npcs)
                print(f"[环境演化] 成功生成第{i+1}个NPC")
        
        if game_state.get("environment_evolution"):
            game_state["environment_evolution"]["npcs"] = all_npcs
        
        return all_npcs
    
    async def generate_complete_rules(self, group_id: str, scene_name: str,
                                          player_identity: str, building_type: str,
                                          api_url: str, api_key: str, model_list: List[str],
                                          current_model_index: int, temperature: float) -> Optional[Dict[str, Any]]:
        """生成完整的场景规则
        
        Args:
            group_id: 群组ID
            scene_name: 场景名称
            player_identity: 玩家身份
            building_type: 建筑类型
            api_url: API地址
            api_key: API密钥
            model_list: 模型列表
            current_model_index: 当前模型索引
            temperature: 温度参数
            
        Returns:
            完整的规则信息
        """
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        prompt = f"""
你是一位精通规则怪谈创作的游戏设计师。请基于以下剧情导入、场景结构和隐藏真相，生成规则怪谈的规则系统。

**基础信息：**
剧情导入：
- 场景：{scene_name}
- 背景：{game_state.get('background', '')}
- 玩家身份：{player_identity}

场景结构：
- 建筑类型：{building_type}

隐藏真相（非常重要）：
{game_state.get('hidden_truth', '')}

{RULE_DESIGN_PRINCIPLES}

**额外规则要求（环境演化系统特定）：**

1. **规则与环境绑定（非常重要）：**
   - 请将至少2-3条规则与场景中特定的、可交互的环境细节直接关联
   - 例如，如果规则是"不要理会走廊尽头的呼救声"，那么与之关联的环境可以是"走廊尽头的温度总是异常低，且墙上有抓痕"
   - 这样，玩家在探索到该位置时，能通过环境感知强化对规则的记忆和怀疑
   - 环境绑定应该自然、巧妙，不要过于明显

2. **规则间的潜在冲突（非常重要）：**
   - 请尝试构建至少一组存在潜在矛盾的规则
   - 例如，规则A："午夜后必须留在自己的房间内。" 规则B："公寓中没有404室。"规则C："公寓中有404室。"
   - 实际上公寓中有404室，但是仅在午夜后才会出现，此时玩家将陷入遵守A还是出门寻找404室的两难境地
   - 请在 hidden_truth 中解释这种矛盾的本质（如：B、C两条规则来自不同势力）
   - 在 death_triggers 中隐含相关触发条件

3. **死亡触发条件要求（环境演化系统特定）：**
   - 列出会导致死亡的行为
   - 死亡条件应该与规则和真相有逻辑关联
   - 死亡条件应该具有一定的隐蔽性，不是一眼就能看穿
   - 死亡条件应该给玩家一定的容错空间
   - 死亡条件的描述应该简洁、明确

**输出格式：**
   - 每条规则都应该与隐藏真相中的某个要素有直接的因果关系
   - 规则不是孤立的，而是形成了一个相互关联的规则网络
   - 例如：
     * 如果真相是"工厂的夜间保安是来自异世界的实体"，那么规则"夜间只允许蓝色制服的保安巡逻"就是对这个真相的伪装性描述
     * 如果真相是"三楼东侧病房的窗户是通往异界的通道"，那么规则"三楼东侧病房的窗户必须保持关闭状态"就是对这个危险通道的防护措施
     * 规则之间应该形成推理链条：遵守规则A -> 发现异常B -> 触发规则C -> 揭示真相D

9. **协作规则（多人模式非常重要）**：
   - 如果游戏模式是"多人"，请设计1-2条需要多个玩家协作才能发现或触发的规则
   - 例如：
     * 规则A："当两名玩家同时站在不同的位置时，某个隐藏的通道才会开启"
     * 规则B："只有当一名玩家持有特定物品，另一名玩家说出特定口令时，才能解除某个陷阱"
     * 规则C："需要三名玩家分别在三个不同的地点同时执行某个动作，才能揭示某个关键真相"
   - 协作规则应该鼓励玩家之间的沟通和合作，而不是各自为战
   - 协作规则的设计应该巧妙，让玩家在探索过程中自然地发现协作的必要性
   - 在 hidden_truth 中说明协作规则的设计意图和触发条件

10. **规则标题（非常重要）**：
    - 根据场景类型和玩家身份，生成一个贴合剧情的规则标题
    - 例如：
      * 工厂场景：员工守则、安全规程、操作手册
      * 医院场景：患者须知、病房守则、医疗规程
      * 学校场景：学生守则、校园安全须知、宿舍管理规定
      * 城堡场景：访客须知、城堡守则、安全指南
      * 酒店场景：入住须知、客房服务守则、安全警示
      * 超市场景：员工手册、营业规范、安全须知
      * 地铁场景：乘客须知、安全规程、运营守则
    - 标题应该简洁、正式，符合该场景的官方文件风格

**规则描述要求（非常重要）：**

- 规则必须简洁、直接，每条规则不超过60字
- 只说明禁止、允许或要求做的行为，不解释原因
- 使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX
- 使用冰冷、客观的公文语调，如同官方通告或操作手册
- 语调应该冷静、正式、不带感情色彩
- 可以加入少量关键的环境或感官细节，但要简洁
- 细节应该让人感到不安和恐惧，但不要直接揭示真相

**示例规则风格：**
"禁止在22:00-06:00期间离开房间。"
"听到三声敲门时，必须立即开门。"
"三楼东侧病房的窗户必须保持关闭状态。若发现窗户自行开启，请立即通知安保人员并远离开启的窗户。"
"严禁回应任何呼救声。"
"只有看到绿色灯光时才能进入走廊。"
"工厂只有蓝色制服的保安，若看见黑色制服的保安，请立即报告主管。"
"城堡内没有镜子，如果你觉得你看到了镜子，请相信那是你的幻觉。"

**死亡触发条件要求（非常重要）：**

- 列出会导致死亡的行为
- 死亡条件应该与规则和真相有逻辑关联
- 死亡条件应该具有一定的隐蔽性，不是一眼就能看穿
- 死亡条件应该给玩家一定的容错空间
- 死亡条件的描述应该简洁、明确

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "rules_title": "规则标题（如：员工守则、患者须知等）",
  "rules": ["规则1", "规则2", ...],
  "win_condition": "通关条件",
  "resolve_condition": "解除条件（解决规则怪谈根源的条件）",
  "death_triggers": ["会导致死亡的行为1", "会导致死亡的行为2", ...]
}}

**重要提示：**
- 请仅返回JSON，不要包含任何其他文字
- 严禁使用任何emoji表情符号
- 规则应该有层次感，表面看似合理，隐藏着诡异之处
- 死亡触发条件应该与规则和真相有逻辑关联
- 整个规则系统应该形成一个完整的、有逻辑的体系
- 规则的设计必须与提供的隐藏真相保持一致，所有规则都应该能够从隐藏真相中找到合理的解释
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            print("[环境演化] 生成完整规则失败：LLM API调用失败")
            print("[环境演化] 可能原因：API服务不可用、网络连接问题或API密钥错误")
            return None
        
        result = self._parse_llm_json_response(llm_response, "生成完整规则")
        if not result:
            print("[环境演化] 生成完整规则失败：JSON解析失败")
            print(f"[环境演化] LLM返回内容（前500字符）: {llm_response[:500]}")
            return None
        
        print(f"[环境演化] 生成完整规则成功，包含 {len(result.get('rules', []))} 条规则")
        return result

    async def generate_npc_initial_guidance(self, group_id: str, scene_name: str,
                                             player_identity: str, building_type: str,
                                             game_mode: str,
                                             api_url: str, api_key: str, model_list: List[str],
                                             current_model_index: int, temperature: float) -> Optional[Dict[str, Any]]:
        """生成NPC初始引导
        
        Args:
            group_id: 群组ID
            scene_name: 场景名称
            player_identity: 玩家身份
            building_type: 建筑类型
            game_mode: 游戏模式（单人/多人）
            api_url: API地址
            api_key: API密钥
            model_list: 模型列表
            current_model_index: 当前模型索引
            temperature: 温度参数
            
        Returns:
            NPC引导信息
        """
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        npcs = game_state.get("environment_evolution", {}).get("npcs", [])
        if not npcs:
            return None
        
        rules = game_state.get("rules", [])
        rules_title = game_state.get("rules_title", "")
        win_condition = game_state.get("win_condition", "")
        resolve_condition = game_state.get("resolve_condition", "")
        death_triggers = game_state.get("death_triggers", [])
        
        complete_rules = {
            "rules_title": rules_title,
            "rules": rules,
            "win_condition": win_condition,
            "resolve_condition": resolve_condition,
            "death_triggers": death_triggers
        }
        
        prompt = f"""
你是一位规则怪谈NPC引导系统。请为玩家生成NPC初始引导。

场景：{scene_name}
建筑类型：{building_type}
玩家身份：{player_identity}
游戏模式：{game_mode}
背景故事：{game_state.get('background', '')}
隐藏真相：{game_state.get('hidden_truth', '')}

完整规则信息：{json.dumps(complete_rules, ensure_ascii=False)}

当前NPC列表：{json.dumps(npcs, ensure_ascii=False)}

**NPC引导要求：**

1. **引导NPC选择**：从当前NPC列表中选择一个最合适的引导NPC（如：管家、护士长、保安队长等）
   - 优先选择对玩家态度友好或中立的NPC
   - 优先选择在等级体系中地位较高的NPC
   - 优先选择与玩家身份相关的NPC

2. **引导方式选择**：根据场景类型、玩家身份、NPC性格等因素，选择一种引导方式
   - **方式一：自然语言引导** - NPC通过对话、行为、语气等方式自然地引导玩家，不发放任何书面材料
   - **方式二：规则载体引导** - NPC发放"工作守则"、"员工手册"、"注意事项"等书面材料，通过规则载体引导玩家
   - 只能选择一种方式，不要同时使用两种方式

3. **引导内容**：
   - NPC的行为描述（如：NPC走近玩家，递给玩家一张纸条，指着某个方向等）
   - NPC的对话内容（如：欢迎语、工作职责、行为规范、禁止事项等）
   - 如果选择规则载体引导，则使用提供的完整规则载体信息
   - NPC的态度和语气（如：严厉、温和、神秘、不耐烦等）
   - **重要**：如果是多人模式，NPC的引导应该面向所有玩家，使用"你们"等复数称呼，确保引导适用于多个玩家

4. **规则融入**：
   - 如果选择自然语言引导：将规则融入NPC的对话和行为中，不要直接列出规则，而是让玩家通过NPC的引导自然理解规则
   - 如果选择规则载体引导：直接使用提供的完整规则载体内容

5. **氛围营造**：
   - 通过NPC的引导营造诡异、紧张、压抑的氛围
   - 通过NPC的表情、动作、语气暗示隐藏的真相
   - 通过环境描写（如：光线、声音、气味）增强氛围
   - 不要直接揭示真相，而是通过暗示让玩家感受到异常

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "guide_npc": {{
    "name": "引导NPC姓名",
    "role": "引导NPC角色",
    "attitude": "对玩家的态度（如：严厉、温和、神秘、不耐烦等）"
  }},
  "guidance_method": "引导方式（natural_language或rule_carrier）",
  "npc_behavior": "NPC的行为描述（如：NPC走近玩家，递给玩家一张皱巴巴的纸条，用警惕的眼神四处张望）",
  "npc_dialogue": "NPC的对话内容（如：'新来的？拿着这个，仔细看。这里不是普通的地方，有些规矩你必须遵守...'）",
  "rule_carrier": {{
    "type": "规则载体类型（如：工作守则、员工手册、注意事项、告示牌等）",
    "title": "规则载体标题（如：夜班护士工作守则）",
    "content": "规则载体内容（直接使用完整规则载体内容）"
  }},
  "atmosphere_description": "氛围描述（如：走廊里的灯光忽明忽暗，空气中弥漫着一股淡淡的消毒水味，远处传来若有若无的脚步声）",
  "implicit_rules": [
    {{
      "rule_hint": "规则暗示（如：'午夜后不要离开病房'）",
      "npc_action": "NPC如何暗示这条规则（如：NPC压低声音，神秘地说：'记住，午夜后不管听到什么声音，都不要离开病房'）"
    }}
  ]
}}

**重要说明：**
- 如果guidance_method为"natural_language"，则rule_carrier字段应为null
- 如果guidance_method为"rule_carrier"，则rule_carrier字段应直接使用提供的完整规则载体信息
- 只能选择一种引导方式，不要同时使用两种方式
- 请仅返回JSON，不要包含任何其他文字
- 不要使用任何emoji表情符号
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            print("[环境演化] 生成NPC初始引导失败：LLM API调用失败")
            return None
        
        result = self._parse_llm_json_response(llm_response, "生成NPC初始引导")
        if not result:
            print("[环境演化] 生成NPC初始引导失败：JSON解析失败")
            return None
        
        return result
    
    async def update_environment(self, group_id: str, player_actions: List[str],
                                   player_locations: List[str],
                                   api_url: str, api_key: str, model_list: List[str],
                                   current_model_index: int, temperature: float) -> Dict[str, Any]:
        """更新环境状态"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return {}
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return {}
        
        current_time = datetime.now()
        last_update = datetime.fromisoformat(environment_evolution["time"]["last_update"])
        elapsed_minutes = (current_time - last_update).total_seconds() / 60
        
        environment_evolution["time"]["elapsed_minutes"] += elapsed_minutes
        environment_evolution["time"]["last_update"] = current_time.isoformat()
        
        time_phase = self._calculate_time_phase(environment_evolution["time"]["elapsed_minutes"])
        environment_evolution["time"]["time_phase"] = time_phase
        
        player_locations_str = ", ".join(player_locations) if player_locations else "未知"
        
        prompt = f"""
你是一位规则怪谈环境演化系统。请根据当前游戏状态，更新环境描述。

场景：{game_state.get('scene', '')}
建筑类型：{game_state.get('building_type', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}

当前时间：{time_phase}
已流逝时间：{environment_evolution['time']['elapsed_minutes']}分钟

玩家当前位置：{player_locations_str}
玩家最近行动：{json.dumps(player_actions, ensure_ascii=False)}

当前NPC状态：{json.dumps(environment_evolution.get('npcs', []), ensure_ascii=False)}

**环境演化要求：**

1. **时间影响**：根据时间流逝，描述环境的变化（如：光线变化、温度变化、声音变化等）
2. **NPC行为**：描述NPC的当前行为和位置变化
3. **氛围变化**：根据时间和玩家行为，描述氛围的变化
4. **异常现象**：描述可能出现的异常现象
5. **危险提示**：如果有危险，给出适当的提示
6. **NPC发现**：如果NPC与玩家在同一位置或相邻位置，请在环境描述中自然地描述玩家看到的NPC。不要单独列出发现的NPC，而是将NPC的发现融入场景描述中。例如："走廊尽头的阴影中，一个穿着白大褂的身影正在徘徊"，而不是"发现NPC：医生张三"

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "environment_state": {{
    "lighting": "光线状况",
    "temperature": "温度感受",
    "sounds": ["声音1", "声音2"],
    "smells": ["气味1", "气味2"],
    "atmosphere": "整体氛围"
  }},
  "npc_activities": [
    {{
      "npc_name": "NPC姓名",
      "activity": "当前活动",
      "location": "当前位置",
      "mood": "当前情绪"
    }}
  ],
  "environmental_changes": ["环境变化1", "环境变化2"],
  "anomalies": ["异常现象1", "异常现象2"],
  "danger_warnings": ["危险提示1", "危险提示2"],
  "time_description": "时间流逝的描述",
  "scene_description": "完整的场景描述，包含环境、氛围、NPC发现等信息"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return environment_evolution
        
        result = self._parse_llm_json_response(llm_response, "更新环境")
        if not result:
            return environment_evolution
        
        environment_evolution["environment_state"] = result.get("environment_state", environment_evolution["environment_state"])
        npc_activities = result.get("npc_activities", [])
        environment_evolution["npc_activities"] = npc_activities
        environment_evolution["environmental_changes"] = result.get("environmental_changes", [])
        environment_evolution["anomalies"] = result.get("anomalies", [])
        environment_evolution["danger_warnings"] = result.get("danger_warnings", [])
        environment_evolution["time_description"] = result.get("time_description", "")
        environment_evolution["scene_description"] = result.get("scene_description", "")
        
        npcs = environment_evolution.get("npcs", [])
        current_time = datetime.now().isoformat()
        
        for activity in npc_activities:
            npc_name = activity.get("npc_name", "")
            activity_desc = activity.get("activity", "")
            location = activity.get("location", "")
            mood = activity.get("mood", "")
            
            for npc in npcs:
                if npc.get("name", "") == npc_name:
                    if "movement_history" not in npc:
                        npc["movement_history"] = []
                    
                    npc["movement_history"].append({
                        "timestamp": current_time,
                        "activity": activity_desc,
                        "location": location,
                        "mood": mood
                    })
                    
                    break
        
        return environment_evolution
    
    def format_environment_update(self, group_id: str) -> Optional[str]:
        """格式化环境更新信息，返回场景描述
        
        Args:
            group_id: 群组ID
            
        Returns:
            场景描述文本
        """
        game_state = self.game_states.get(group_id)
        if not game_state:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        scene_description = environment_evolution.get("scene_description", "")
        if scene_description:
            return scene_description
        
        return None
    
    async def generate_identity_guide(self, group_id: str, player_identity: str,
                                     building_type: str, api_url: str, api_key: str,
                                     model_list: List[str], current_model_index: int,
                                     temperature: float) -> Optional[Dict[str, Any]]:
        """生成身份引导信息（如管家引导侍者）"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        prompt = f"""
你是一位规则怪谈身份引导系统。请为玩家生成身份引导信息。

场景：{game_state.get('scene', '')}
建筑类型：{building_type}
玩家身份：{player_identity}
背景故事：{game_state.get('background', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}

**身份引导要求：**

1. **引导NPC**：选择一个合适的NPC来引导玩家（如：管家引导侍者、护士长引导护士等）
2. **引导内容**：根据NPC的性格、态度和剧情需要，自然地向玩家解释工作职责、行为规范、禁止事项等
   - 严厉的NPC可能会直接命令和警告
   - 温和的NPC可能会耐心解释和提醒
   - 神秘的NPC可能给出模糊的暗示
   - 基于NPC的设定和剧情逻辑来决定引导方式
3. **区域限制**：明确说明玩家当前身份无法进入的区域
4. **身份等级**：说明当前身份在等级体系中的位置
5. **身份提升**：暗示如何获得更高的身份权限
6. **语气和态度**：根据NPC的性格特征，使用合适的语气和态度

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "guide_npc": {{
    "name": "引导NPC姓名",
    "role": "引导NPC角色",
    "attitude": "对玩家的态度（如：严厉、温和、神秘等）"
  }},
  "guide_content": {{
    "welcome_message": "欢迎语",
    "job_responsibilities": ["职责1", "职责2", "职责3"],
    "behavior_rules": ["行为规范1", "行为规范2", "行为规范3"],
    "forbidden_areas": ["禁止区域1", "禁止区域2", "禁止区域3"],
    "identity_level": "当前身份等级（如：低级、中级、高级）",
    "identity_hierarchy": "身份等级体系说明",
    "promotion_hint": "如何获得更高身份的提示"
  }},
  "access_permissions": {{
    "allowed_areas": ["允许进入的区域1", "允许进入的区域2"],
    "restricted_areas": ["限制进入的区域1", "限制进入的区域2"],
    "forbidden_areas": ["禁止进入的区域1", "禁止进入的区域2"]
  }}
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return None
        
        result = self._parse_llm_json_response(llm_response, "生成身份引导")
        if not result:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        identity_system = environment_evolution.get("identity_system", {})
        identity_system["identity_guides"][player_identity] = result
        identity_system["access_permissions"] = result.get("access_permissions", {})
        
        environment_evolution["identity_system"] = identity_system
        game_state["environment_evolution"] = environment_evolution
        self.game_states[group_id] = game_state
        
        return result
    
    async def check_area_access(self, group_id: str, player_identity: str,
                               target_area: str) -> Dict[str, Any]:
        """评估玩家进入目标区域的风险（不再强制禁止，而是提供风险评估）"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return {"risk_level": "未知", "reason": "游戏状态不存在"}
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return {"risk_level": "未知", "reason": "环境系统未初始化"}
        
        identity_system = environment_evolution.get("identity_system", {})
        access_permissions = identity_system.get("access_permissions", {})
        
        allowed_areas = access_permissions.get("allowed_areas", [])
        restricted_areas = access_permissions.get("restricted_areas", [])
        forbidden_areas = access_permissions.get("forbidden_areas", [])
        
        if target_area in allowed_areas:
            return {
                "can_access": True,
                "risk_level": "无风险",
                "reason": f"您的身份（{player_identity}）可以正常进入{target_area}",
                "potential_consequences": []
            }
        
        if target_area in restricted_areas:
            return {
                "can_access": True,
                "risk_level": "中风险",
                "reason": f"您的身份（{player_identity}）进入{target_area}可能会被发现",
                "potential_consequences": [
                    "可能被NPC发现并受到警告",
                    "可能被要求离开该区域",
                    "可能影响NPC对您的好感度"
                ],
                "suggestion": "建议谨慎行动，避免被NPC发现"
            }
        
        if target_area in forbidden_areas:
            return {
                "can_access": True,
                "risk_level": "高风险",
                "reason": f"您的身份（{player_identity}）进入{target_area}极其危险",
                "potential_consequences": [
                    "极大概率被NPC发现",
                    "可能被NPC追杀",
                    "可能直接触发死亡规则",
                    "可能被永久禁止进入某些区域"
                ],
                "suggestion": "强烈建议不要进入，除非您做好了面对严重后果的准备"
            }
        
        return {
            "can_access": True,
            "risk_level": "未知",
            "reason": f"未确定{target_area}的访问权限",
            "potential_consequences": ["未知风险"],
            "suggestion": "请谨慎探索"
        }
    
    async def trigger_area_violation_consequences(self, group_id: str, player_identity: str,
                                                   target_area: str, api_url: str, api_key: str,
                                                   model_list: List[str], current_model_index: int,
                                                   temperature: float) -> Optional[Dict[str, Any]]:
        """触发进入限制区域的负面后果"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        identity_system = environment_evolution.get("identity_system", {})
        access_permissions = identity_system.get("access_permissions", {})
        
        forbidden_areas = access_permissions.get("forbidden_areas", [])
        restricted_areas = access_permissions.get("restricted_areas", [])
        
        is_forbidden = target_area in forbidden_areas
        is_restricted = target_area in restricted_areas
        
        if not (is_forbidden or is_restricted):
            return None
        
        npcs = environment_evolution.get("npcs", [])
        relevant_npc = None
        
        for npc in npcs:
            if npc.get("location") == target_area or npc.get("danger_level") == "高":
                relevant_npc = npc
                break
        
        if not relevant_npc:
            relevant_npc = {
                "name": "神秘守护者",
                "role": "区域守护者",
                "attitude": "敌对"
            }
        
        prompt = f"""
你是一位规则怪谈后果生成系统。请为玩家进入限制区域生成负面后果。

场景：{game_state.get('scene', '')}
建筑类型：{game_state.get('building_type', '')}
玩家身份：{player_identity}
目标区域：{target_area}
区域类型：{'禁止区域' if is_forbidden else '限制区域'}
发现NPC：{relevant_npc.get('name', '')}（{relevant_npc.get('role', '')}）
NPC态度：{relevant_npc.get('attitude_to_player', '敌对')}
NPC性格：{relevant_npc.get('personality', '神秘、危险')}
背景故事：{game_state.get('background', '')}

**后果生成要求：**

1. **发现过程**：描述NPC如何发现玩家进入该区域
2. **NPC反应**：根据NPC的性格和态度，描述NPC的反应
3. **后果类型**：
   - 如果是禁止区域：可能导致死亡、重伤、被追杀等严重后果
   - 如果是限制区域：可能导致警告、被赶出、好感度下降等中等后果
4. **逃脱机会**：给玩家一个逃脱的机会或选择
5. **后续影响**：说明这次违规对后续游戏的影响

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "discovery": "发现过程描述",
  "npc_reaction": {{
    "npc_name": "NPC姓名",
    "dialogue": "NPC的对话",
    "action": "NPC的行动",
    "attitude": "NPC的态度"
  }},
  "consequence_type": "后果类型（如：警告/被赶出/受伤/死亡/被追杀）",
  "consequence_description": "后果详细描述",
  "escape_chance": {{
    "has_escape_chance": true/false,
    "escape_options": ["逃脱选项1", "逃脱选项2"],
    "escape_difficulty": "逃脱难度（低/中/高）"
  }},
  "long_term_effects": ["长期影响1", "长期影响2"],
  "death_risk": "死亡风险（无/低/中/高/极高）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return None
        
        result = self._parse_llm_json_response(llm_response, "生成区域违规后果")
        if not result:
            return None
        
        violation_event = {
            "event_id": f"violation_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "event_type": "区域违规",
            "player_identity": player_identity,
            "target_area": target_area,
            "area_type": "禁止区域" if is_forbidden else "限制区域",
            **result
        }
        
        environment_evolution["active_events"].append(violation_event)
        environment_evolution["event_history"].append(violation_event)
        
        return violation_event
    
    async def update_identity_permissions(self, group_id: str, new_identity: str,
                                          api_url: str, api_key: str, model_list: List[str],
                                          current_model_index: int, temperature: float) -> Optional[Dict[str, Any]]:
        """更新身份权限（当玩家身份变化时）"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        identity_system = environment_evolution.get("identity_system", {})
        old_identity = identity_system.get("current_identity", "")
        
        prompt = f"""
你是一位规则怪谈权限管理系统。请为玩家的新身份更新访问权限。

场景：{game_state.get('scene', '')}
建筑类型：{game_state.get('building_type', '')}
旧身份：{old_identity}
新身份：{new_identity}
背景故事：{game_state.get('background', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}

**权限更新要求：**

1. **权限变化**：说明新身份相比旧身份的权限变化（新增、保留、失去的权限）
2. **新增区域**：新身份可以进入但旧身份不能进入的区域
3. **失去区域**：旧身份可以进入但新身份不能进入的区域（如果有）
4. **保留区域**：新旧身份都可以进入的区域
5. **身份等级**：新身份在等级体系中的位置
6. **特殊权限**：新身份的特殊权限或限制

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "identity_change": {{
    "old_identity": "旧身份",
    "new_identity": "新身份",
    "level_change": "等级变化（如：提升/降低/不变）"
  }},
  "permission_changes": {{
    "new_areas": ["新增可进入区域1", "新增可进入区域2"],
    "lost_areas": ["失去可进入区域1", "失去可进入区域2"],
    "retained_areas": ["保留可进入区域1", "保留可进入区域2"]
  }},
  "new_access_permissions": {{
    "allowed_areas": ["允许进入的区域1", "允许进入的区域2"],
    "restricted_areas": ["限制进入的区域1", "限制进入的区域2"],
    "forbidden_areas": ["禁止进入的区域1", "禁止进入的区域2"]
  }},
  "identity_level": "新身份等级",
  "special_permissions": ["特殊权限1", "特殊权限2"],
  "new_responsibilities": ["新职责1", "新职责2"]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return None
        
        result = self._parse_llm_json_response(llm_response, "更新身份权限")
        if not result:
            return None
        
        identity_system["current_identity"] = new_identity
        identity_system["identity_history"].append(new_identity)
        identity_system["access_permissions"] = result.get("new_access_permissions", {})
        
        environment_evolution["identity_system"] = identity_system
        game_state["environment_evolution"] = environment_evolution
        self.game_states[group_id] = game_state
        
        return result
    
    async def get_identity_guide_npc(self, group_id: str, player_identity: str) -> Optional[Dict[str, Any]]:
        """获取身份引导NPC信息"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        identity_system = environment_evolution.get("identity_system", {})
        identity_guides = identity_system.get("identity_guides", {})
        
        return identity_guides.get(player_identity)
    
    async def trigger_random_event(self, group_id: str, player_location: str,
                                   api_url: str, api_key: str, model_list: List[str],
                                   current_model_index: int, temperature: float) -> Optional[Dict[str, Any]]:
        """触发随机事件"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        event_probability = random.random()
        if event_probability > 0.3:
            return None
        
        prompt = f"""
你是一位规则怪谈事件系统。请根据当前游戏状态，生成一个随机事件。

场景：{game_state.get('scene', '')}
建筑类型：{game_state.get('building_type', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}

当前时间：{environment_evolution['time']['time_phase']}
玩家位置：{player_location}

当前NPC状态：{json.dumps(environment_evolution.get('npcs', []), ensure_ascii=False)}
当前环境状态：{json.dumps(environment_evolution.get('environment_state', {}), ensure_ascii=False)}

**事件设计要求：**

1. **事件类型**：可以是NPC事件、环境事件、超自然事件、物品事件等
2. **事件描述**：详细描述事件的发生过程
3. **玩家选择**：给玩家提供2-3个选择
4. **后果分析**：分析每个选择可能的后果
5. **危险等级**：评估事件的危险等级（低/中/高/极高）
6. **与真相的关系**：事件与隐藏真相的关系

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "event_type": "事件类型",
  "event_description": "事件描述",
  "player_choices": [
    {{
      "choice": "选择描述",
      "consequence": "可能的后果",
      "risk_level": "风险等级（低/中/高）"
    }}
  ],
  "danger_level": "危险等级（低/中/高/极高）",
  "truth_relation": "与真相的关系",
  "npc_involved": ["涉及的NPC"],
  "location": "事件发生地点"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return None
        
        result = self._parse_llm_json_response(llm_response, "触发随机事件")
        if not result:
            return None
        
        event = {
            "event_id": f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            **result
        }
        
        environment_evolution["active_events"].append(event)
        environment_evolution["event_history"].append(event)
        
        return event
    
    async def check_npc_interaction(self, group_id: str, player_location: str,
                                     player_action: str, api_url: str, api_key: str,
                                     model_list: List[str], current_model_index: int,
                                     temperature: float) -> Optional[Dict[str, Any]]:
        """检查NPC交互"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        npcs = environment_evolution.get("npcs", [])
        if not npcs:
            return None
        
        identity_system = environment_evolution.get("identity_system", {})
        current_identity = identity_system.get("current_identity", "")
        
        nearby_npcs = [npc for npc in npcs if player_location in npc.get("location", "")]
        
        if not nearby_npcs:
            return None
        
        selected_npc = random.choice(nearby_npcs)
        
        identity_guide = identity_system.get("identity_guides", {}).get(current_identity, {})
        guide_npc_name = identity_guide.get("guide_npc", {}).get("name", "")
        
        is_guide_npc = (selected_npc.get("name", "") == guide_npc_name)
        
        npc_movement_history = selected_npc.get("movement_history", [])
        recent_activities = npc_movement_history[-5:] if npc_movement_history else []
        
        prompt = f"""
你是一位规则怪谈NPC交互系统。请根据当前游戏状态，生成NPC的回应。

NPC信息：
- 姓名：{selected_npc.get('name', '')}
- 角色：{selected_npc.get('role', '')}
- 性格：{selected_npc.get('personality', '')}
- 行为模式：{selected_npc.get('behavior', '')}
- 对玩家的态度：{selected_npc.get('attitude_to_player', '')}
- 危险等级：{selected_npc.get('danger_level', '')}

NPC最近活动轨迹：
{json.dumps(recent_activities, ensure_ascii=False) if recent_activities else '无'}

玩家信息：
- 身份：{current_identity}
- 行动：{player_action}
- 位置：{player_location}

场景：{game_state.get('scene', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}

身份引导信息：{json.dumps(identity_guide, ensure_ascii=False) if identity_guide else '无'}

**NPC回应要求：**

1. **身份相关引导**：如果此NPC是身份引导NPC（{is_guide_npc}），应该根据玩家身份提供相应的引导和指导
2. **回应内容**：NPC的对话或行为
3. **情绪状态**：NPC当前的情绪状态
4. **行为描述**：NPC的行为描述
5. **隐藏信息**：NPC话语中可能隐藏的信息（如果有）
6. **危险提示**：如果NPC有危险倾向，给出提示
7. **关于权限区域的处理**：
   - 根据NPC的性格、态度、危险等级以及剧情需要来决定是否提醒玩家
   - 引导NPC可能会主动提醒玩家哪些区域不能进入
   - 敌对NPC可能故意不提醒，甚至诱导玩家进入危险区域
   - 中立NPC可能根本不在意或不知道
   - 神秘NPC可能给出模糊的暗示而不是直接提醒
   - 基于NPC的设定和剧情逻辑来决定

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "npc_name": "NPC姓名",
  "response": "NPC的回应",
  "emotion": "情绪状态",
  "action": "行为描述",
  "hidden_info": "隐藏信息（如果有）",
  "danger_warning": "危险提示（如果有）",
  "attitude_change": "态度变化（如果有）",
  "permission_hint": "权限提示（如果有）",
  "is_guide_interaction": "是否为身份引导交互（是/否）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return None
        
        result = self._parse_llm_json_response(llm_response, "NPC交互")
        if not result:
            return None
        
        interaction = {
            "interaction_id": f"interaction_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "npc_name": selected_npc.get('name', ''),
            "player_location": player_location,
            "player_action": player_action,
            **result
        }
        
        environment_evolution["npc_interactions"].append(interaction)
        
        return interaction
    
    async def check_npc_active_interaction(self, group_id: str, player_location: str,
                                             api_url: str, api_key: str, model_list: List[str],
                                             current_model_index: int, temperature: float) -> Optional[Dict[str, Any]]:
        """检查NPC主动交互"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return None
        
        npcs = environment_evolution.get("npcs", [])
        if not npcs:
            return None
        
        identity_system = environment_evolution.get("identity_system", {})
        current_identity = identity_system.get("current_identity", "")
        
        active_interaction_probability = random.random()
        
        if active_interaction_probability > 0.4:
            return None
        
        eligible_npcs = []
        
        for npc in npcs:
            npc_location = npc.get("location", "")
            npc_danger_level = npc.get("danger_level", "")
            npc_attitude = npc.get("attitude_to_player", "")
            
            is_nearby = (npc_location == player_location or 
                         self._is_adjacent_location(npc_location, player_location))
            
            if is_nearby:
                priority = 0
                if npc_danger_level == "极高":
                    priority += 3
                elif npc_danger_level == "高":
                    priority += 2
                elif npc_danger_level == "中":
                    priority += 1
                
                if npc_attitude == "敌对":
                    priority += 2
                elif npc_attitude == "神秘":
                    priority += 1
                
                eligible_npcs.append({
                    "npc": npc,
                    "priority": priority
                })
        
        if not eligible_npcs:
            return None
        
        eligible_npcs.sort(key=lambda x: x["priority"], reverse=True)  # pyright: ignore[reportUnknownLambdaType]
        selected_npc_data = eligible_npcs[0]
        selected_npc = selected_npc_data["npc"]
        
        identity_guide = identity_system.get("identity_guides", {}).get(current_identity, {})
        guide_npc_name = identity_guide.get("guide_npc", {}).get("name", "")
        is_guide_npc = (selected_npc.get("name", "") == guide_npc_name)
        
        prompt = f"""
你是一位规则怪谈NPC主动交互系统。请根据当前游戏状态，生成NPC的主动交互行为。

NPC信息：
- 姓名：{selected_npc.get('name', '')}
- 角色：{selected_npc.get('role', '')}
- 性格：{selected_npc.get('personality', '')}
- 行为模式：{selected_npc.get('behavior', '')}
- 对玩家的态度：{selected_npc.get('attitude_to_player', '')}
- 危险等级：{selected_npc.get('danger_level', '')}
- 当前位置：{selected_npc.get('location', '')}

玩家信息：
- 身份：{current_identity}
- 位置：{player_location}

场景：{game_state.get('scene', '')}
建筑类型：{game_state.get('building_type', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}

身份引导信息：{json.dumps(identity_guide, ensure_ascii=False) if identity_guide else '无'}
是否为引导NPC：{is_guide_npc}

**NPC主动交互要求：**

1. **交互类型**：NPC主动发起的交互类型（如：接近玩家、发起对话、警告玩家、追赶玩家、攻击玩家等）
2. **交互原因**：NPC为什么要主动与玩家交互
3. **对话内容**：NPC对玩家说的话（如果有）
4. **行为描述**：NPC的具体行为描述
5. **情绪状态**：NPC当前的情绪状态
6. **危险程度**：此次交互对玩家的危险程度（无/低/中/高/极高）
7. **玩家选择**：如果需要玩家做出选择，提供选项
8. **长期影响**：此次交互可能产生的长期影响

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "interaction_type": "交互类型（接近玩家/发起对话/警告玩家/追赶玩家/攻击玩家/其他）",
  "interaction_reason": "交互原因",
  "dialogue": "对话内容（如果有）",
  "action_description": "行为描述",
  "emotion": "情绪状态",
  "danger_level": "危险程度（无/低/中/高/极高）",
  "player_choices": [
    {{
      "choice_id": "选项ID",
      "choice_text": "选项文本",
      "consequence_hint": "后果提示"
    }}
  ],
  "long_term_effects": ["长期影响1", "长期影响2"],
  "is_guide_interaction": "是否为身份引导交互（是/否）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return None
        
        result = self._parse_llm_json_response(llm_response, "NPC主动交互")
        if not result:
            return None
        
        active_interaction = {
            "interaction_id": f"active_interaction_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "npc_name": selected_npc.get('name', ''),
            "npc_role": selected_npc.get('role', ''),
            "player_location": player_location,
            **result
        }
        
        environment_evolution["npc_interactions"].append(active_interaction)
        
        return active_interaction
    
    def _is_adjacent_location(self, location1: str, location2: str) -> bool:
        """判断两个位置是否相邻"""
        if not location1 or not location2:
            return False
        
        if location1 == location2:
            return True
        
        common_adjacent_pairs = [
            ("大厅", "走廊"),
            ("走廊", "大厅"),
            ("走廊", "客房"),
            ("客房", "走廊"),
            ("走廊", "厨房"),
            ("厨房", "走廊"),
            ("走廊", "地下室"),
            ("地下室", "走廊"),
            ("走廊", "阁楼"),
            ("阁楼", "走廊"),
            ("走廊", "书房"),
            ("书房", "走廊"),
            ("走廊", "餐厅"),
            ("餐厅", "走廊"),
            ("走廊", "花园"),
            ("花园", "走廊"),
            ("入口", "大厅"),
            ("大厅", "入口"),
            ("走廊", "病房"),
            ("病房", "走廊"),
            ("走廊", "药房"),
            ("药房", "走廊"),
            ("走廊", "手术室"),
            ("手术室", "走廊"),
            ("走廊", "诊室"),
            ("诊室", "走廊"),
            ("走廊", "休息室"),
            ("休息室", "走廊"),
            ("走廊", "储藏室"),
            ("储藏室", "走廊")
        ]
        
        return (location1, location2) in common_adjacent_pairs
    
    def check_npc_discovery(self, group_id: str, player_location: str) -> List[Dict[str, Any]]:
        """检查玩家是否发现了NPC
        
        根据玩家当前位置和NPC的位置关系来判断是否发现NPC。
        NPC的活动轨迹记录在后台，玩家只有在实际到达相关位置时才会发现。
        
        Args:
            group_id: 群组ID
            player_location: 玩家当前位置
            
        Returns:
            发现的NPC列表，包含NPC信息和发现场景描述
        """
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return []
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return []
        
        npcs = environment_evolution.get("npcs", [])
        if not npcs:
            return []
        
        discovered_npcs = []
        
        for npc in npcs:
            npc_name = npc.get("name", "")
            npc_role = npc.get("role", "")
            npc_personality = npc.get("personality", "")
            npc_danger_level = npc.get("danger_level", "")
            npc_attitude = npc.get("attitude_to_player", "")
            
            movement_history = npc.get("movement_history", [])
            
            if movement_history:
                latest_movement = movement_history[-1]
                npc_location = latest_movement.get("location", "")
                npc_activity = latest_movement.get("activity", "")
                npc_mood = latest_movement.get("mood", "")
            else:
                npc_location = npc.get("location", "")
                npc_activity = npc.get("behavior", "")
                npc_mood = npc.get("mood", "")
            
            if not npc_location:
                continue
            
            is_same_location = (npc_location == player_location)
            is_adjacent = self._is_adjacent_location(npc_location, player_location)
            
            if is_same_location or is_adjacent:
                discovered_npcs.append({
                    "npc_name": npc_name,
                    "npc_role": npc_role,
                    "npc_personality": npc_personality,
                    "npc_danger_level": npc_danger_level,
                    "npc_attitude": npc_attitude,
                    "npc_location": npc_location,
                    "npc_activity": npc_activity,
                    "npc_mood": npc_mood,
                    "discovery_type": "same_location" if is_same_location else "adjacent_location",
                    "discovery_scene": self._generate_discovery_scene(
                        npc_name, npc_role, npc_activity, npc_location,
                        npc_mood, is_same_location
                    )
                })
        
        return discovered_npcs
    
    def _generate_discovery_scene(self, npc_name: str, npc_role: str, npc_activity: str,
                                   npc_location: str, npc_mood: str, is_same_location: bool) -> str:
        """生成NPC发现场景描述
        
        Args:
            npc_name: NPC姓名
            npc_role: NPC角色
            npc_activity: NPC当前活动
            npc_location: NPC位置
            npc_mood: NPC情绪
            is_same_location: 是否在同一位置
            
        Returns:
            发现场景描述
        """
        if is_same_location:
            return f"在{npc_location}，你看到了{npc_role}{npc_name}，{npc_activity}。"
        else:
            return f"在{npc_location}附近，你注意到了{npc_role}{npc_name}，{npc_activity}。"
    
    async def check_time_based_death(self, group_id: str, api_url: str, api_key: str,
                                      model_list: List[str], current_model_index: int,
                                      temperature: float) -> Tuple[bool, Optional[str]]:
        """检查基于时间的死亡条件"""
        game_state = self.game_states.get(group_id, {})
        if not game_state:
            return False, None
        
        environment_evolution = game_state.get("environment_evolution", {})
        if not environment_evolution:
            return False, None
        
        elapsed_minutes = environment_evolution["time"]["elapsed_minutes"]
        
        if elapsed_minutes < 30:
            return False, None
        
        prompt = f"""
你是一位规则怪谈死亡判定系统。请根据当前游戏状态，判断玩家是否因为时间流逝而死亡。

场景：{game_state.get('scene', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}

已流逝时间：{elapsed_minutes}分钟
当前时间：{environment_evolution['time']['time_phase']}

玩家信息：{json.dumps(game_state.get('players', {}), ensure_ascii=False)}

**死亡判定要求：**

1. **时间相关规则**：检查是否有与时间相关的死亡规则
2. **玩家行为**：分析玩家是否违反了时间相关的规则
3. **NPC行为**：分析NPC是否因为时间流逝而对玩家产生威胁
4. **环境变化**：分析环境变化是否对玩家构成威胁

**输出格式：**
请以JSON格式返回，格式如下：
{{
  "death_occurred": "是/否",
  "death_reason": "死亡原因",
  "death_description": "死亡过程描述",
  "victim": "受害者姓名（如果是多人模式）",
  "npc_involved": "涉及的NPC（如果有）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        llm_response = await self._call_llm_api(prompt, api_url, api_key, model_list, current_model_index, temperature)
        if not llm_response:
            return False, None
        
        result = self._parse_llm_json_response(llm_response, "时间死亡判定")
        if not result:
            return False, None
        
        if result.get("death_occurred") == "是":
            death_reason = result.get("death_reason", "")
            death_description = result.get("death_description", "")
            return True, f"{death_reason}\n\n{death_description}"
        
        return False, None
    
    def _calculate_time_phase(self, elapsed_minutes: float) -> str:
        """根据流逝时间计算时间阶段"""
        if elapsed_minutes < 30:
            return "午夜"
        elif elapsed_minutes < 60:
            return "凌晨"
        elif elapsed_minutes < 90:
            return "黎明前"
        elif elapsed_minutes < 120:
            return "黎明"
        elif elapsed_minutes < 180:
            return "清晨"
        elif elapsed_minutes < 240:
            return "上午"
        elif elapsed_minutes < 300:
            return "中午"
        elif elapsed_minutes < 360:
            return "下午"
        elif elapsed_minutes < 420:
            return "傍晚"
        elif elapsed_minutes < 480:
            return "黄昏"
        elif elapsed_minutes < 540:
            return "入夜"
        else:
            return "深夜"
    
    def _parse_llm_json_response(self, llm_response: str, step_name: str = "步骤") -> Optional[Dict[str, Any]]:
        """解析LLM返回的JSON响应，支持提取JSON部分并处理控制字符和不完整的JSON"""
        if not llm_response:
            print(f"[环境演化] {step_name} LLM返回为空")
            return None
        
        def clean_json_string(json_str: str) -> str:
            """清理JSON字符串中的无效控制字符"""
            import re
            json_str = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', json_str)
            return json_str
        
        def try_parse_json(json_str: str) -> Optional[Dict[str, Any]]:
            """尝试解析JSON，返回解析结果或None"""
            try:
                cleaned_str = clean_json_string(json_str)
                result = json.loads(cleaned_str)
                return result
            except json.JSONDecodeError:
                return None
        
        def fix_incomplete_json(json_str: str) -> Optional[Dict[str, Any]]:
            """尝试修复不完整的JSON"""
            try:
                cleaned_str = clean_json_string(json_str)
                
                # 检查是否缺少闭合括号
                open_braces = cleaned_str.count('{')
                close_braces = cleaned_str.count('}')
                
                if open_braces > close_braces:
                    missing_braces = open_braces - close_braces
                    print(f"[环境演化] {step_name} 检测到不完整的JSON，缺少 {missing_braces} 个闭合括号")
                    
                    # 尝试补全括号
                    fixed_str = cleaned_str + '}' * missing_braces
                    
                    # 尝试解析修复后的JSON
                    try:
                        result = json.loads(fixed_str)
                        print(f"[环境演化] {step_name} 成功修复并解析JSON")
                        return result
                    except json.JSONDecodeError as e:
                        print(f"[环境演化] {step_name} 修复JSON失败: {e}")
                        return None
                
                return None
            except Exception as e:
                print(f"[环境演化] {step_name} 修复JSON时发生异常: {e}")
                return None
        
        try:
            result = try_parse_json(llm_response)
            if result:
                print(f"[环境演化] {step_name} JSON解析成功")
                return result
        except Exception as e:
            print(f"[环境演化] {step_name} JSON解析失败: {e}")
        
        print(f"[环境演化] {step_name} 尝试提取JSON部分...")
        
        json_match = re.search(r'\{[\s\S]*\}', llm_response)
        if json_match:
            try:
                json_str = json_match.group()
                result = try_parse_json(json_str)
                if result:
                    print(f"[环境演化] {step_name} 成功提取JSON")
                    return result
                else:
                    print(f"[环境演化] {step_name} 提取JSON后仍然解析失败，尝试修复...")
                    
                    # 尝试修复不完整的JSON
                    fixed_result = fix_incomplete_json(json_str)
                    if fixed_result:
                        return fixed_result
                    
                    print(f"[环境演化] {step_name} 提取的JSON内容（前500字符）: {json_str[:500]}")
                    return None
            except Exception as e2:
                print(f"[环境演化] {step_name} 提取JSON后仍然解析失败: {e2}")
                return None
        else:
            print(f"[环境演化] {step_name} 未找到JSON部分")
            print(f"[环境演化] LLM返回内容（前500字符）: {llm_response[:500]}")
            return None
    
    async def _call_llm_api(self, prompt: str, api_url: str, api_key: str,
                             model_list: List[str], current_model_index: int,
                             temperature: float, max_tokens: int = 16000) -> Optional[str]:
        """调用LLM API，支持重试机制和截断检测"""
        import asyncio
        
        max_retries = 3
        base_delay = 3
        current_max_tokens = max_tokens
        
        for attempt in range(max_retries):
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
                
                data = {
                    "model": model_list[current_model_index],
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    "max_tokens": current_max_tokens
                }
                
                timeout = aiohttp.ClientTimeout(total=300)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(api_url, headers=headers, json=data) as response:
                        if response.status == 200:
                            result = await response.json()
                            choices = result.get("choices", [])
                            if not choices:
                                print(f"[环境演化] LLM API返回choices为空")
                                return None
                            
                            first_choice = choices[0]
                            finish_reason = first_choice.get("finish_reason", "")
                            content = first_choice.get("message", {}).get("content", "")
                            
                            if not content:
                                print(f"[环境演化] LLM API返回内容为空")
                                return None
                            
                            print(f"[环境演化] LLM API调用成功 (尝试 {attempt + 1}/{max_retries})")
                            print(f"[环境演化] 返回内容长度: {len(content)} 字符")
                            print(f"[环境演化] finish_reason: {finish_reason}")
                            
                            if finish_reason == "length":
                                print(f"[环境演化] 检测到响应被截断，当前max_tokens: {current_max_tokens}")
                                
                                if attempt < max_retries - 1:
                                    current_max_tokens = min(current_max_tokens * 2, 16000)
                                    print(f"[环境演化] 增加max_tokens到 {current_max_tokens} 并重试...")
                                    await asyncio.sleep(1)
                                    continue
                                else:
                                    print(f"[环境演化] 已达到最大重试次数，使用截断的响应")
                                    return content
                            
                            return content
                        elif response.status in [503, 504]:
                            error_text = await response.text()
                            status_name = "503 服务不可用" if response.status == 503 else "504 网关超时"
                            print(f"[环境演化] LLM API调用失败: {status_name} (尝试 {attempt + 1}/{max_retries})")
                            print(f"[环境演化] 错误详情: {error_text[:200]}")
                            
                            if attempt < max_retries - 1:
                                delay = base_delay * (2 ** attempt)
                                print(f"[环境演化] 等待 {delay} 秒后重试...")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                print(f"[环境演化] 已达到最大重试次数 {max_retries}，放弃重试")
                                return None
                        elif response.status == 429:
                            error_text = await response.text()
                            print(f"[环境演化] LLM API调用失败: 429 请求过于频繁 (尝试 {attempt + 1}/{max_retries})")
                            print(f"[环境演化] 错误详情: {error_text[:200]}")
                            
                            if attempt < max_retries - 1:
                                delay = base_delay * (3 ** attempt)
                                print(f"[环境演化] 等待 {delay} 秒后重试...")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                print(f"[环境演化] 已达到最大重试次数 {max_retries}，放弃重试")
                                return None
                        else:
                            error_text = await response.text()
                            print(f"[环境演化] LLM API调用失败: HTTP {response.status} (尝试 {attempt + 1}/{max_retries})")
                            print(f"[环境演化] 错误详情: {error_text[:200]}")
                            
                            if attempt < max_retries - 1 and response.status >= 500:
                                delay = base_delay * (2 ** attempt)
                                print(f"[环境演化] 服务器错误，等待 {delay} 秒后重试...")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                return None
            except asyncio.TimeoutError:
                print(f"[环境演化] LLM API调用超时 (尝试 {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[环境演化] 等待 {delay} 秒后重试...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"[环境演化] 已达到最大重试次数 {max_retries}，放弃重试")
                    return None
            except aiohttp.ClientError as e:
                print(f"[环境演化] LLM API网络错误 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[环境演化] 等待 {delay} 秒后重试...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"[环境演化] 已达到最大重试次数 {max_retries}，放弃重试")
                    return None
            except Exception as e:
                print(f"[环境演化] LLM API调用异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"[环境演化] 等待 {delay} 秒后重试...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"[环境演化] 已达到最大重试次数 {max_retries}，放弃重试")
                    return None
        
        return None

