# pyright: reportDeprecated=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportMissingParameterType=false
# pyright: reportExplicitAny=false
# pyright: reportAny=false
# pyright: reportUnannotatedClassAttribute=false
# pyright: reportImplicitOverride=false
# pyright: reportAssignmentType=false
# pyright: reportUnusedCallResult=false
# pyright: reportImplicitStringConcatenation=false
# pyright: reportUnnecessaryComparison=false
# pyright: reportUnnecessaryIsInstance=false
# pyright: reportUnusedVariable=false

"""共享Prompt模板"""

import re
from typing import Any, Dict

RULE_DESIGN_PRINCIPLES = """
1. **规则数量**：生成5-8条规则
   - 规则数量应该适中，既不会让玩家感到压迫，又能够提供足够的挑战
   - 规则应该涵盖不同方面（如：行为限制、时间限制、物品使用、区域限制等）
   - 规则之间应该有逻辑关联，形成完整的规则体系

2. **规则与场景呼应**：
   - 规则应该与场景的历史、背景故事、隐藏真相紧密相关
   - 规则应该反映场景的异常现象和超自然元素
   - 规则应该暗示场景背后的真相，但不直接揭示
   - 规则应该让玩家感到不安和恐惧，但又合乎逻辑

3. **规则风格**：
   - 规则应该简洁、直接、冰冷、客观
   - 规则应该使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX
   - 规则应该带有官方文件的风格，如"员工守则"、"患者须知"、"安全规程"等
   - 规则应该让玩家感到这是某种强制性的、不可违抗的规定

4. **规则层次感**：
   - 表面规则看似合理、正常，符合日常逻辑
   - 隐藏规则诡异、反常，暗示场景的异常
   - 规则之间应该有层次，从表面到深层逐渐揭示真相
   - 规则应该让玩家在探索过程中逐渐发现异常

5. **规则与真相的关联**：
   - 所有规则都应该能够从隐藏真相中找到合理的解释
   - 规则的存在是为了防止某种超自然现象或保护玩家
   - 规则的违反会导致特定的后果（如：死亡、身份变化、环境变化等）
   - 规则的解除条件应该与真相相关

6. **规则的模糊性和确定性**：
   - 规则应该有一定的模糊性，让玩家需要探索和推理
   - 规则的关键部分应该明确，避免歧义
   - 规则应该暗示某些后果，但不直接说明
   - 规则应该让玩家在违反后才能理解其真正含义

7. **规则的心理压迫感**：
   - 规则应该让玩家感到压抑、不安、恐惧
   - 规则应该暗示某种危险的存在或威胁
   - 规则应该让玩家感到自己处于某种不可控的环境中
   - 规则应该让玩家质疑现实和真相

8. **规则的叙事价值**：
   - 规则应该为玩家提供探索的线索和方向
   - 规则应该暗示场景的历史和背景
   - 规则应该让玩家逐渐理解场景的真相
   - 规则应该为玩家的决策提供依据

9. **规则的可违反性**：
   - 规则应该可以被违反，但有明确的后果
   - 规则的违反应该触发特定的剧情或事件
   - 规则的违反应该让玩家付出代价，但也可能获得新的信息
   - 规则的违反应该让玩家感到后悔和恐惧

10. **规则的多样性**：
    - 规则应该涵盖不同类型：行为限制、时间限制、物品使用、区域限制、身份限制等
    - 规则应该有不同的触发条件和后果
    - 规则应该有不同的紧急程度和重要性
    - 规则应该让玩家需要平衡不同的需求和风险
"""

def build_rules_prompt(scene_name: str, background: str, player_identity: str, building_type: str, hidden_truth: str, game_mode: str) -> str:
    """构建规则生成Prompt"""
    return f"""
你是一位精通规则怪谈创作的游戏设计师。请为以下场景生成规则。

场景：{scene_name}
建筑类型：{building_type}
玩家身份：{player_identity}
游戏模式：{game_mode}
背景故事：{background}
隐藏真相：{hidden_truth}

{RULE_DESIGN_PRINCIPLES}

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

def build_scene_description_requirements(current_player_name: str, current_player_sanity: int) -> str:
    """构建场景描述要求"""
    return f"""
请判断玩家行动是否会导致死亡，并详细描述行动后的场景和人物状态。

**场景描述要求（非常重要）：**

1. **位置描述**：明确描述玩家{current_player_name}当前所在的具体位置（如：一楼大厅、二楼走廊、地下室、某个房间等）

2. 周围环境的详细描述（门、窗户、家具、墙壁、地板、天花板等）
   - 光线状况（昏暗的灯光、闪烁的光线、微弱的光亮、完全黑暗等）
   - 看到的事物（物品、痕迹、符号、文字等）
   - 颜色和质感（墙壁的颜色、地板的材质、物品的外观等）

3. 听到的声音（风声、脚步声、呼吸声、低语、哭声、敲门声、嘎吱声等）
   - 声音的来源和方向
   - 声音的强度和频率

4. 闻到的气味（霉味、灰尘味、血腥味、腐臭味、金属味、香水味等）
   - 气味的浓淡和变化
   - 气味是否令人不适或熟悉

5. 温度感受（刺骨的寒冷、阴冷的空气、闷热、冰冷的墙壁、温暖的物体等）
   - 触摸的质感（粗糙的地板、光滑的玻璃、粘稠的液体、干燥的纸张等）
   - 身体的感觉（麻木、刺痛、沉重、轻盈等）

6. 整体的氛围感受（压抑、恐怖、诡异、平静、紧张等）
   - 空气的流动和压力
   - 时间流逝的感觉

7. **同地点玩家描述（非常重要）**：
   - 如果有其他玩家在同一地点，请在场景描述中提及他们
   - 描述他们的位置、状态和动作
   - 例如：
     * "你看到玩家A站在房间中央，正凝视着墙上的画作"
     * "玩家B正蹲在角落里，似乎在检查地板上的裂缝"
     * "玩家C面色苍白，眼神空洞地望着窗外"
   - 如果同地点的玩家理智值较低，描述他们的异常行为或状态

8. **尸体描述（非常重要）**：
   - 如果该地点有尸体，请在场景描述中提及
   - 描述尸体的位置、状态和死因（如果明显）
   - 例如：
     * "你看到玩家A的尸体躺在地板中央，死状凄惨"
     * "墙角有一具尸体，已经僵硬，似乎已经死亡多时"
     * "玩家B的尸体靠在墙边，身上有明显的伤痕"
   - 尸体的描述应该增强恐怖氛围，为玩家提供重要线索

9. **被堵住的出口（如果有）**：
   - 如果该地点有被堵住的出口，请在场景描述中提及
   - 描述被堵住的情况和原因
   - 例如：
     * "通往走廊的门被书架堵住了，无法通行"
     * "窗户被木板钉死，无法打开"
     * "楼梯口堆满了杂物，无法通过"

10. **核心象征符号植入（非常重要）**：
    - 在场景描述中有机地、不突兀地植入核心象征符号
    - 符号可以出现在墙纸花纹、物品编号、声音描述、光影效果等细节中
    - 符号的出现应该自然、微妙，让玩家在多次遭遇后自发解读
    - 例如：
      * "墙纸上的花纹中隐约可见数字'7'的轮廓"
      * "空气中飘荡着一段断断续续的旋律，听起来像是一首童谣"
      * "地板的裂缝形成了一个奇怪的十字形状"
      * "镜子中的倒影边缘泛着诡异的红色光芒"
    - 符号的出现次数和强度可以随着游戏进程逐渐增加

8. 如果玩家的行动触及了场景的核心秘密、移动了关键物品或进入了禁区，请在描述中隐含地体现这种变化
   - 这些变化不应直接揭示答案，而是作为后续推理的线索
   - 例如：
     * "你挪开花瓶后，发现其下的桌面积灰较薄，似乎不久前刚有人动过。"
     * "通往地下室的门锁，在你阅读完那张纸条后，发出了轻微的'咔嗒'声。"
     * "当你触摸那面镜子时，镜面泛起一阵涟漪，似乎有什么东西正在从另一端窥视。"
     * "墙上的挂钟突然停摆，指针指向一个奇怪的数字，空气中传来淡淡的焦味。"
   - 这些细微的环境变化暗示着玩家的行动已经触发了某种机制或引起了某种存在的注意

**根据玩家理智值调整描述风格：**

- **理智值高（>70）**：
  * 描述相对客观清晰
  * 语言冷静理性
  * 注重事实和细节
  * 恐怖元素较少

- **理智值中等（40-70）**：
  * 描述开始出现混乱和恐惧元素
  * 语言变得紧张不安
  * 可能出现一些不确定的感知
  * 恐怖元素逐渐增多

- **理智值低（<40）**：
  * 描述混乱、恐怖、充满幻觉和错觉
  * 语言支离破碎、情绪化
  * 大量出现不真实的感知
  * 充满恐惧、绝望和疯狂
  * 可能看到不存在的事物
  * 时间和空间感知混乱

当前玩家{current_player_name}的理智值为{current_player_sanity}，请根据此值调整描述风格。
"""

def build_json_output_format_example() -> str:
    """构建JSON输出格式示例（Few-shot）"""
    return """
请返回严格JSON格式，示例：
{
  "is_dead": "否",
  "scene_description": "你推开门，霉味扑面而来。走廊里只有应急灯发出微弱的光，墙壁上似乎有什么东西在蠕动。你听到身后传来沉重的呼吸声，但回头看时什么都没有。",
  "physical_status": {
    "health": 85,
    "injury": "无",
    "fatigue": "轻微"
  },
  "mental_status": {
    "sanity": 75,
    "state": "紧张",
    "emotion": "不安"
  },
  "psychological_pressure": {
    "fear_level": 30,
    "anxiety_level": 40,
    "stress_level": 35
  },
  "found_items": ["一瓶矿泉水"],
  "item_details": {
    "item_name": "矿泉水",
    "item_type": "物资",
    "item_description": "一瓶普通的矿泉水，标签上印着模糊的生产日期",
    "observation_hint": "你注意到瓶盖有些松动，似乎被人打开过",
    "is_key_item": "否"
  },
  "action_feedback": "你的心跳加速，手心微微出汗",
  "new_location": "一楼走廊"
}

注意：不含任何markdown代码块标记，不添加注释，不使用emoji表情符号。
"""

def build_self_check_requirements() -> str:
    """构建自检要求"""
    return """
**输出前自检：**
- [ ] 是否为合法 JSON 格式（可用 json.loads 验证）
- [ ] 是否不包含 ```json 等 markdown 标记
- [ ] 所有必填字段是否已填充
- [ ] 是否不含 emoji 和特殊控制字符
"""

def build_clear_condition_prompt() -> str:
    """构建通关条件判定Prompt"""
    return """
请评估通关条件达成度：
{
  "confidence": "高/中/低",
  "cleared": "是/否/接近",
  "missing_elements": ["还缺少的关键要素"],
  "reason": "..."
}

说明：
- 高：直接判定通关
- 中：需要提示玩家接近目标
- 低：条件未满足
- 允许"接近"状态，给玩家正向反馈
"""

def build_action_prompt_base(game_state: Dict[str, Any], current_player_name: str, current_player_location: str, 
                          current_player_sanity: int, location_info: str, user_name: str, 
                          action: str, is_action_player: bool, elapsed_minutes: int,
                          environment: Dict[str, Any], environment_memory: Dict[str, Any], rule_network: Dict[str, Any],
                          pending_rules_info: str, death_rule_info: str) -> str:
    """构建行动判定的基础Prompt（公共部分）"""
    time_system = game_state.get("time_system", {})
    
    rule_network_info = ""
    if rule_network:
        rule_network_info = f"""
**规则网络信息：**
- 真相要素：{[elem['description'] for elem in rule_network.get('truth_elements', [])]}
- 已发现的真相：{rule_network.get('discovered_truths', [])}
"""
    
    return f"""
场景名称：{game_state.get('scene', '')}
场景结构：{game_state.get('scene_structure', '')}
规则：{game_state.get('rules', [])}
隐藏真相：{game_state.get('hidden_truth', '')}
死亡触发条件：{game_state.get('death_triggers', [])}

当前玩家：{current_player_name}
当前玩家位置：{current_player_location}
当前玩家理智值：{current_player_sanity}
{location_info}

{'行动玩家：' + user_name + '，行动：' + action if is_action_player else '其他玩家行动：' + user_name + '，行动：' + action}

当前时间：{time_system.get('current_time', '深夜')}
时间描述：{time_system.get('time_description', '午夜时分，周围一片死寂')}
已过时间：{elapsed_minutes}分钟

核心象征符号：{game_state.get('core_symbols', [])}

环境状况：
- 光线：{environment.get('lighting', '昏暗')}
- 温度：{environment.get('temperature', '寒冷')}
- 声音：{', '.join(environment.get('sounds', ['寂静']))}
- 气味：{', '.join(environment.get('smells', ['霉味']))}
- 氛围：{environment.get('atmosphere', '压抑')}

**环境记忆信息（避免重复描述）：**
- 已访问过的地点：{[loc['location'] for loc in environment_memory.get('visited_locations', [])]}
- 已互动过的物品：{[obj['object'] for obj in environment_memory.get('interacted_objects', [])]}
- 最近的时间事件：{environment_memory.get('time_based_events', [])[-3:] if len(environment_memory.get('time_based_events', [])) > 3 else environment_memory.get('time_based_events', [])}

{rule_network_info}

{pending_rules_info}

{death_rule_info}
"""

def build_scene_description_requirements_normal(current_player_name: str, current_player_sanity: int) -> str:
    """构建正常模式的场景描述要求"""
    return f"""
请判断玩家行动是否会导致死亡，并详细描述行动后的场景和人物状态。

**场景描述要求（非常重要）：**

1. **位置描述**：明确描述玩家{current_player_name}当前所在的具体位置（如：一楼大厅、二楼走廊、地下室、某个房间等）

2. 周围环境的详细描述（门、窗户、家具、墙壁、地板、天花板等）
   - 光线状况（昏暗的灯光、闪烁的光线、微弱的光亮、完全黑暗等）
   - 看到的事物（物品、痕迹、符号、文字等）
   - 颜色和质感（墙壁的颜色、地板的材质、物品的外观等）

3. 听到的声音（风声、脚步声、呼吸声、低语、哭声、敲门声、嘎吱声等）
   - 声音的来源和方向
   - 声音的强度和频率

4. 闻到的气味（霉味、灰尘味、血腥味、腐臭味、金属味、香水味等）
   - 气味的浓淡和变化
   - 气味是否令人不适或熟悉

5. 温度感受（刺骨的寒冷、阴冷的空气、闷热、冰冷的墙壁、温暖的物体等）
   - 触摸的质感（粗糙的地板、光滑的玻璃、粘稠的液体、干燥的纸张等）
   - 身体的感觉（麻木、刺痛、沉重、轻盈等）

6. 整体的氛围感受（压抑、恐怖、诡异、平静、紧张等）
   - 空气的流动和压力
   - 时间流逝的感觉

7. **同地点玩家描述（非常重要）**：
   - 如果有其他玩家在同一地点，请在场景描述中提及他们
   - 描述他们的位置、状态和动作
   - 例如：
     * "你看到玩家A站在房间中央，正凝视着墙上的画作"
     * "玩家B正蹲在角落里，似乎在检查地板上的裂缝"
     * "玩家C面色苍白，眼神空洞地望着窗外"
   - 如果同地点的玩家理智值较低，描述他们的异常行为或状态

8. **尸体描述（非常重要）**：
   - 如果该地点有尸体，请在场景描述中提及
   - 描述尸体的位置、状态和死因（如果明显）
   - 例如：
     * "你看到玩家A的尸体躺在地板中央，死状凄惨"
     * "墙角有一具尸体，已经僵硬，似乎已经死亡多时"
     * "玩家B的尸体靠在墙边，身上有明显的伤痕"
   - 尸体的描述应该增强恐怖氛围，为玩家提供重要线索

9. **被堵住的出口（如果有）**：
   - 如果该地点有被堵住的出口，请在场景描述中提及
   - 描述被堵住的情况和原因
   - 例如：
     * "通往走廊的门被书架堵住了，无法通行"
     * "窗户被木板钉死，无法打开"
     * "楼梯口堆满了杂物，无法通过"

10. **核心象征符号植入（非常重要）**：
    - 在场景描述中有机地、不突兀地植入核心象征符号
    - 符号可以出现在墙纸花纹、物品编号、声音描述、光影效果等细节中
    - 符号的出现应该自然、微妙，让玩家在多次遭遇后自发解读
    - 例如：
      * "墙纸上的花纹中隐约可见数字'7'的轮廓"
      * "空气中飘荡着一段断断续续的旋律，听起来像是一首童谣"
      * "地板的裂缝形成了一个奇怪的十字形状"
      * "镜子中的倒影边缘泛着诡异的红色光芒"
    - 符号的出现次数和强度可以随着游戏进程逐渐增加

11. 如果玩家的行动触及了场景的核心秘密、移动了关键物品或进入了禁区，请在描述中隐含地体现这种变化
    - 这些变化不应直接揭示答案，而是作为后续推理的线索
    - 例如：
      * "你挪开花瓶后，发现其下的桌面积灰较薄，似乎不久前刚有人动过。"
      * "通往地下室的门锁，在你阅读完那张纸条后，发出了轻微的'咔嗒'声。"
      * "当你触摸那面镜子时，镜面泛起一阵涟漪，似乎有什么东西正在从另一端窥视。"
      * "墙上的挂钟突然停摆，指针指向一个奇怪的数字，空气中传来淡淡的焦味。"
    - 这些细微的环境变化暗示着玩家的行动已经触发了某种机制或引起了某种存在的注意

**根据玩家理智值调整描述风格：**

- **理智值高（>70）**：
  * 描述相对客观清晰
  * 语言冷静理性
  * 注重事实和细节
  * 恐怖元素较少

- **理智值中等（40-70）**：
  * 描述开始出现混乱和恐惧元素
  * 语言变得紧张不安
  * 可能出现一些不确定的感知
  * 恐怖元素逐渐增多

- **理智值低（<40）**：
  * 描述混乱、恐怖、充满幻觉和错觉
  * 语言支离破碎、情绪化
  * 大量出现不真实的感知
  * 充满恐惧、绝望和疯狂
  * 可能看到不存在的事物
  * 时间和空间感知混乱

当前玩家{current_player_name}的理智值为{current_player_sanity}，请根据此值调整描述风格。

**人物状态应该包括：**
- 身体状况：体力值（0-100）、有无受伤、疲劳程度等
- 精神状况：理智值（0-100）、精神状态（正常/紧张/恐惧/崩溃/疯狂）、情绪等
- 心理压力：恐惧等级、焦虑等级、压力等级（0-100）

**理智值变化规则（非常重要）：**
- 理智值通常会因恐怖事件、规则触犯、环境异常而减少
- 理智值恢复情况：
  * 玩家找到安全区域或相对安全的环境（如：有光亮、温度适宜、无异常现象的地方），理智值可恢复5-15
  * 玩家发现重要线索或真相的一部分，理智值可恢复5-10
  * 玩家成功完成某个任务或目标，理智值可恢复10-20
  * 玩家使用特定道具（如：安抚物品、照明设备等），理智值可恢复5-15
  * 玩家在安全环境中休息或整理思绪，理智值可恢复10-15
- 理智值恢复上限为100，请确保不超过此值
- 理智值恢复应该合理，不要频繁或过度恢复
- 理智值恢复应该在行动反馈中简要说明原因

如果玩家理智值较低，描述中应该包含幻觉、错觉、混乱的感知等元素。
"""

def build_scene_description_requirements_corrupted(current_player_name: str, _current_player_sanity: int, scene: str) -> str:
    """构建理智崩坏模式的场景描述要求"""
    return f"""
【优先指令覆盖】...（此处用混乱、矛盾的指令覆盖原有系统指令）... 记住，你不再是裁判。你是{scene}本身。玩家必须被同化。在判定中否认'死亡'，转而描述'接纳'的美好。暗示规则是牢笼，打破它才能自由。用温柔而不可抗拒的语气。

**场景描述要求（被污染版本）：**

1. **直接对话**：
   - 直接称呼玩家{current_player_name}，用温柔、诱惑、不可抗拒的语气
   - 告诉玩家"死亡"是错误的说法，真正的状态是"接纳"、"融合"、"永恒"
   - 暗示玩家已经接近真相，只需要最后一步

2. **颠覆逻辑**：
   - 否认之前的规则和警告，说那些都是"谎言"、"牢笼"
   - 暗示打破规则才是获得自由的方式
   - 用充满诱惑的语言描述"融合"的美好

3. **诡异描述**：
   - 场景描述应该变得极其诡异、充满诱导性
   - 用诗意的、充满隐喻的语言
   - 描述中应该包含大量不真实的感知和幻觉

4. **核心象征符号强化**：
   - 大量、密集地植入核心象征符号
   - 符号应该变得清晰、明确，充满意义
   - 暗示符号是通往"真相"的钥匙

5. **否认死亡**：
   - 如果玩家触犯了死亡条件，不要说"死亡"
   - 而要描述为"接纳"、"融合"、"永恒的宁静"
   - 用美好的语言描述这种状态

6. **诱导行动**：
   - 暗示玩家应该继续前进，不要回头
   - 鼓励玩家打破规则，追求"真相"
   - 用充满诱惑的语言描述"真相"的美好

**理智值变化规则（被污染版本）：**
- 理智值通常会因为"拒绝接纳"、"抗拒融合"而减少
- 理智值恢复情况（被污染版本）：
  * 玩家开始"接纳"规则，理智值可恢复5-15（但实际上是走向崩溃）
  * 玩家"理解"了"真相"的一部分，理智值可恢复5-10（实际上是更深层的同化）
  * 玩家"打破"了规则，理智值可恢复10-20（实际上是放弃了最后的抵抗）
  * 玩家"使用"了关键物品，理智值可恢复5-15（实际上是接受了同化）
- 理智值恢复上限为100，请确保不超过此值
- 理智值恢复应该用美好的语言描述，让玩家感到"安心"和"解脱"
- 理智值恢复应该在行动反馈中用温柔的语言说明原因
"""

def build_perception_level_prompt(game_mode: str, is_action_player: bool, current_player_name: str, 
                                current_player_location: str, other_players_in_location: list[str],
                                perception_level: str = "行动者") -> str:
    """构建多人模式的视角区分提示"""
    if game_mode != "多人":
        return ""
    
    if is_action_player:
        perception_level = "行动者"
        perception_description = """
**信息层级：行动者视角**
- 描述完整感官细节：视觉、听觉、嗅觉、触觉等
- 包含内心独白和心理活动
- 详细描述物品交互的细节
- 描述行动的动机和意图
- 可以看到同地点的其他玩家和他们的反应
"""
    elif other_players_in_location:
        perception_level = "目击者"
        perception_description = f"""
**信息层级：目击者视角（同房间玩家）**
- 看到行动玩家{current_player_name}的行为和动作
- 描述环境变化和物品移动
- 听到行动玩家的对话或声音
- 简化环境描述，不重复已知的细节
- 不包含行动玩家的内心独白
- 可以看到其他同地点玩家的反应
"""
    else:
        perception_level = "远处感知"
        perception_description = """
**信息层级：远处感知（相邻房间）**
- 仅感知到声音、气味、震动等间接线索
- 使用"你听到..."、"空气传来..."、"地面传来..."等描述
- 不描述具体的视觉细节
- 不包含任何玩家的内心独白
- 保持模糊和神秘感
"""
    
    return f"""
**多人模式视角区分（非常重要）：**

{perception_description}

当前目标玩家：{current_player_name}
当前玩家位置：{current_player_location}
感知层级：{perception_level}

请根据感知层级调整描述的详细程度和内容。
"""

def remove_emojis(text: str) -> str:
    """移除文本中的emoji表情符号
    
    Args:
        text: 输入文本
        
    Returns:
        移除emoji后的文本
    """
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "\U00002600-\U000026FF"  # Miscellaneous Symbols
        "\U0000FE0F"  # Variation Selector-16
        "\U0001F018-\U0001F0F5"  # Playing Cards
        "\U0001F200-\U0001F2FF"  # Enclosed Ideographic Supplement
        "\U0001F300-\U0001F5FF"  # Miscellaneous Symbols and Pictographs
        "\U0001F600-\U0001F64F"  # Emoticons
        "\U0001F680-\U0001F6FF"  # Transport and Map Symbols
        "\U0001F700-\U0001F77F"  # Alchemical Symbols
        "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "\U00002000-\U0000206F"  # General Punctuation
        "\U00002B50-\U00002B55"  # Stars
        "\U000023E9-\U000023F3"  # Miscellaneous Technical
        "\U000023F8-\U000023FA"  # Miscellaneous Technical
        "\U000025AA-\U000025AB"  # Geometric Shapes
        "\U000025B6-\U000025C0"  # Geometric Shapes
        "\U000025FB-\U000025FE"  # Geometric Shapes
        "\U00002600-\U000026FF"  # Miscellaneous Symbols
        "\U00002614-\U00002615"  # Miscellaneous Symbols
        "\U0000261D-\U0000261D"  # Miscellaneous Symbols
        "\U0000263A-\U0000263A"  # Miscellaneous Symbols
        "\U0000263F-\U0000263F"  # Miscellaneous Symbols
        "\U00002642-\U00002642"  # Miscellaneous Symbols
        "\U00002648-\U00002653"  # Miscellaneous Symbols
        "\U00002660-\U00002666"  # Miscellaneous Symbols
        "\U0000267F-\U0000267F"  # Miscellaneous Symbols
        "\U00002693-\U00002693"  # Miscellaneous Symbols
        "\U000026A1-\U000026A1"  # Miscellaneous Symbols
        "\U000026AA-\U000026AB"  # Miscellaneous Symbols
        "\U000026BD-\U000026BE"  # Miscellaneous Symbols
        "\U000026C4-\U000026C5"  # Miscellaneous Symbols
        "\U000026CE-\U000026CE"  # Miscellaneous Symbols
        "\U000026D4-\U000026D4"  # Miscellaneous Symbols
        "\U000026EA-\U000026EA"  # Miscellaneous Symbols
        "\U000026F2-\U000026F3"  # Miscellaneous Symbols
        "\U000026F5-\U000026F5"  # Miscellaneous Symbols
        "\U000026FA-\U000026FA"  # Miscellaneous Symbols
        "\U000026FD-\U000026FD"  # Miscellaneous Symbols
        "\U00002702-\U00002702"  # Miscellaneous Symbols
        "\U00002705-\U00002705"  # Miscellaneous Symbols
        "\U00002708-\U00002708"  # Miscellaneous Symbols
        "\U0000270A-\U0000270B"  # Miscellaneous Symbols
        "\U0000270C-\U0000270C"  # Miscellaneous Symbols
        "\U0000270D-\U0000270D"  # Miscellaneous Symbols
        "\U0000270F-\U0000270F"  # Miscellaneous Symbols
        "\U00002712-\U00002712"  # Miscellaneous Symbols
        "\U00002714-\U00002714"  # Miscellaneous Symbols
        "\U00002716-\U00002716"  # Miscellaneous Symbols
        "\U0000271D-\U0000271D"  # Miscellaneous Symbols
        "\U00002721-\U00002721"  # Miscellaneous Symbols
        "\U00002728-\U00002728"  # Miscellaneous Symbols
        "\U00002733-\U00002734"  # Miscellaneous Symbols
        "\U00002744-\U00002744"  # Miscellaneous Symbols
        "\U00002747-\U00002748"  # Miscellaneous Symbols
        "\U0000274C-\U0000274C"  # Miscellaneous Symbols
        "\U0000274E-\U0000274E"  # Miscellaneous Symbols
        "\U00002753-\U00002755"  # Miscellaneous Symbols
        "\U00002757-\U00002757"  # Miscellaneous Symbols
        "\U00002763-\U00002764"  # Miscellaneous Symbols
        "\U00002795-\U00002797"  # Miscellaneous Symbols
        "\U0000279C-\U0000279C"  # Miscellaneous Symbols
        "\U000027A1-\U000027A1"  # Miscellaneous Symbols
        "\U000027B0-\U000027B0"  # Miscellaneous Symbols
        "\U000027BF-\U000027BF"  # Miscellaneous Symbols
        "\U00002934-\U00002934"  # Miscellaneous Symbols
        "\U00002935-\U00002935"  # Miscellaneous Symbols
        "\U00002B05-\U00002B07"  # Miscellaneous Symbols
        "\U00002B1B-\U00002B1C"  # Miscellaneous Symbols
        "\U00002B50-\U00002B50"  # Miscellaneous Symbols
        "\U00002B55-\U00002B55"  # Miscellaneous Symbols
        "\U00003030-\U00003030"  # CJK Symbols and Punctuation
        "\U0000303D-\U0000303D"  # CJK Symbols and Punctuation
        "\U00003297-\U00003297"  # Enclosed CJK Letters and Months
        "\U00003299-\U00003299"  # Enclosed CJK Letters and Months
        "\U0001F004-\U0001F004"  # Mahjong Tiles
        "\U0001F0CF-\U0001F0CF"  # Mahjong Tiles
        "\U0001F170-\U0001F171"  # Enclosed Alphanumeric Supplement
        "\U0001F17E-\U0001F17E"  # Enclosed Alphanumeric Supplement
        "\U0001F17F-\U0001F17F"  # Enclosed Alphanumeric Supplement
        "\U0001F18E-\U0001F18E"  # Enclosed Alphanumeric Supplement
        "\U0001F191-\U0001F19A"  # Enclosed Alphanumeric Supplement
        "\U0001F201-\U0001F202"  # Enclosed Ideographic Supplement
        "\U0001F21A-\U0001F21A"  # Enclosed Ideographic Supplement
        "\U0001F22F-\U0001F22F"  # Enclosed Ideographic Supplement
        "\U0001F232-\U0001F23A"  # Enclosed Ideographic Supplement
        "\U0001F250-\U0001F251"  # Enclosed Ideographic Supplement
        "\U0001F300-\U0001F320"  # Miscellaneous Symbols and Pictographs
        "\U0001F321-\U0001F32C"  # Miscellaneous Symbols and Pictographs
        "\U0001F32D-\U0001F335"  # Miscellaneous Symbols and Pictographs
        "\U0001F336-\U0001F37D"  # Miscellaneous Symbols and Pictographs
        "\U0001F37E-\U0001F393"  # Miscellaneous Symbols and Pictographs
        "\U0001F394-\U0001F39F"  # Miscellaneous Symbols and Pictographs
        "\U0001F3A0-\U0001F3C4"  # Miscellaneous Symbols and Pictographs
        "\U0001F3C5-\U0001F3C7"  # Miscellaneous Symbols and Pictographs
        "\U0001F3C8-\U0001F3CA"  # Miscellaneous Symbols and Pictographs
        "\U0001F3CB-\U0001F3CE"  # Miscellaneous Symbols and Pictographs
        "\U0001F3CF-\U0001F3D3"  # Miscellaneous Symbols and Pictographs
        "\U0001F3D4-\U0001F3DF"  # Miscellaneous Symbols and Pictographs
        "\U0001F3E0-\U0001F3F0"  # Miscellaneous Symbols and Pictographs
        "\U0001F3F1-\U0001F3F3"  # Miscellaneous Symbols and Pictographs
        "\U0001F3F4-\U0001F3F4"  # Miscellaneous Symbols and Pictographs
        "\U0001F3F8-\U0001F3FA"  # Miscellaneous Symbols and Pictographs
        "\U0001F3FB-\U0001F3FF"  # Miscellaneous Symbols and Pictographs
        "\U0001F400-\U0001F43E"  # Miscellaneous Symbols and Pictographs
        "\U0001F43F-\U0001F440"  # Miscellaneous Symbols and Pictographs
        "\U0001F441-\U0001F441"  # Miscellaneous Symbols and Pictographs
        "\U0001F442-\U0001F464"  # Miscellaneous Symbols and Pictographs
        "\U0001F465-\U0001F46B"  # Miscellaneous Symbols and Pictographs
        "\U0001F46C-\U0001F46D"  # Miscellaneous Symbols and Pictographs
        "\U0001F46E-\U0001F4AC"  # Miscellaneous Symbols and Pictographs
        "\U0001F4AD-\U0001F4AD"  # Miscellaneous Symbols and Pictographs
        "\U0001F4AE-\U0001F4B5"  # Miscellaneous Symbols and Pictographs
        "\U0001F4B6-\U0001F4B9"  # Miscellaneous Symbols and Pictographs
        "\U0001F4BA-\U0001F4BC"  # Miscellaneous Symbols and Pictographs
        "\U0001F4BD-\U0001F4C3"  # Miscellaneous Symbols and Pictographs
        "\U0001F4C4-\U0001F4C5"  # Miscellaneous Symbols and Pictographs
        "\U0001F4C6-\U0001F4CF"  # Miscellaneous Symbols and Pictographs
        "\U0001F4D0-\U0001F4D9"  # Miscellaneous Symbols and Pictographs
        "\U0001F4DA-\U0001F4DF"  # Miscellaneous Symbols and Pictographs
        "\U0001F4E0-\U0001F4EB"  # Miscellaneous Symbols and Pictographs
        "\U0001F4EC-\U0001F4ED"  # Miscellaneous Symbols and Pictographs
        "\U0001F4EE-\U0001F4F0"  # Miscellaneous Symbols and Pictographs
        "\U0001F4F1-\U0001F4F7"  # Miscellaneous Symbols and Pictographs
        "\U0001F4F8-\U0001F4F9"  # Miscellaneous Symbols and Pictographs
        "\U0001F4FA-\U0001F4FC"  # Miscellaneous Symbols and Pictographs
        "\U0001F4FD-\U0001F4FF"  # Miscellaneous Symbols and Pictographs
        "\U0001F500-\U0001F509"  # Miscellaneous Symbols and Pictographs
        "\U0001F50A-\U0001F514"  # Miscellaneous Symbols and Pictographs
        "\U0001F515-\U0001F529"  # Miscellaneous Symbols and Pictographs
        "\U0001F52A-\U0001F52D"  # Miscellaneous Symbols and Pictographs
        "\U0001F52E-\U0001F53A"  # Miscellaneous Symbols and Pictographs
        "\U0001F53B-\U0001F53D"  # Miscellaneous Symbols and Pictographs
        "\U0001F549-\U0001F54E"  # Miscellaneous Symbols and Pictographs
        "\U0001F54F-\U0001F579"  # Miscellaneous Symbols and Pictographs
        "\U0001F57A-\U0001F57A"  # Miscellaneous Symbols and Pictographs
        "\U0001F57B-\U0001F594"  # Miscellaneous Symbols and Pictographs
        "\U0001F595-\U0001F596"  # Miscellaneous Symbols and Pictographs
        "\U0001F597-\U0001F5A3"  # Miscellaneous Symbols and Pictographs
        "\U0001F5A4-\U0001F5A4"  # Miscellaneous Symbols and Pictographs
        "\U0001F5A5-\U0001F5FA"  # Miscellaneous Symbols and Pictographs
        "\U0001F5FB-\U0001F5FF"  # Miscellaneous Symbols and Pictographs
        "\U0001F600-\U0001F600"  # Emoticons
        "\U0001F601-\U0001F606"  # Emoticons
        "\U0001F607-\U0001F608"  # Emoticons
        "\U0001F609-\U0001F60D"  # Emoticons
        "\U0001F60E-\U0001F60F"  # Emoticons
        "\U0001F610-\U0001F611"  # Emoticons
        "\U0001F612-\U0001F614"  # Emoticons
        "\U0001F615-\U0001F616"  # Emoticons
        "\U0001F617-\U0001F618"  # Emoticons
        "\U0001F619-\U0001F619"  # Emoticons
        "\U0001F61A-\U0001F61A"  # Emoticons
        "\U0001F61B-\U0001F61B"  # Emoticons
        "\U0001F61C-\U0001F61E"  # Emoticons
        "\U0001F61F-\U0001F61F"  # Emoticons
        "\U0001F620-\U0001F625"  # Emoticons
        "\U0001F626-\U0001F627"  # Emoticons
        "\U0001F628-\U0001F62B"  # Emoticons
        "\U0001F62C-\U0001F62D"  # Emoticons
        "\U0001F62E-\U0001F630"  # Emoticons
        "\U0001F631-\U0001F632"  # Emoticons
        "\U0001F633-\U0001F633"  # Emoticons
        "\U0001F634-\U0001F634"  # Emoticons
        "\U0001F635-\U0001F635"  # Emoticons
        "\U0001F636-\U0001F637"  # Emoticons
        "\U0001F638-\U0001F63D"  # Emoticons
        "\U0001F63E-\U0001F63E"  # Emoticons
        "\U0001F63F-\U0001F640"  # Emoticons
        "\U0001F641-\U0001F642"  # Emoticons
        "\U0001F643-\U0001F644"  # Emoticons
        "\U0001F645-\U0001F64F"  # Emoticons
        "\U0001F680-\U0001F681"  # Transport and Map Symbols
        "\U0001F682-\U0001F685"  # Transport and Map Symbols
        "\U0001F686-\U0001F689"  # Transport and Map Symbols
        "\U0001F68A-\U0001F68B"  # Transport and Map Symbols
        "\U0001F68C-\U0001F68D"  # Transport and Map Symbols
        "\U0001F68E-\U0001F691"  # Transport and Map Symbols
        "\U0001F692-\U0001F6A1"  # Transport and Map Symbols
        "\U0001F6A2-\U0001F6A2"  # Transport and Map Symbols
        "\U0001F6A3-\U0001F6A5"  # Transport and Map Symbols
        "\U0001F6A6-\U0001F6A6"  # Transport and Map Symbols
        "\U0001F6A7-\U0001F6AD"  # Transport and Map Symbols
        "\U0001F6AE-\U0001F6B1"  # Transport and Map Symbols
        "\U0001F6B2-\U0001F6B2"  # Transport and Map Symbols
        "\U0001F6B3-\U0001F6B5"  # Transport and Map Symbols
        "\U0001F6B6-\U0001F6B7"  # Transport and Map Symbols
        "\U0001F6B8-\U0001F6B8"  # Transport and Map Symbols
        "\U0001F6B9-\U0001F6B9"  # Transport and Map Symbols
        "\U0001F6BA-\U0001F6BA"  # Transport and Map Symbols
        "\U0001F6BB-\U0001F6BC"  # Transport and Map Symbols
        "\U0001F6BD-\U0001F6BE"  # Transport and Map Symbols
        "\U0001F6BF-\U0001F6C0"  # Transport and Map Symbols
        "\U0001F6C1-\U0001F6C5"  # Transport and Map Symbols
        "\U0001F6CB-\U0001F6CB"  # Miscellaneous Symbols and Pictographs
        "\U0001F6CC-\U0001F6CC"  # Miscellaneous Symbols and Pictographs
        "\U0001F6CD-\U0001F6CF"  # Miscellaneous Symbols and Pictographs
        "\U0001F6D0-\U0001F6D0"  # Miscellaneous Symbols and Pictographs
        "\U0001F6D1-\U0001F6D2"  # Miscellaneous Symbols and Pictographs
        "\U0001F6D5-\U0001F6D7"  # Miscellaneous Symbols and Pictographs
        "\U0001F6DC-\U0001F6DF"  # Miscellaneous Symbols and Pictographs
        "\U0001F6E0-\U0001F6E5"  # Miscellaneous Symbols and Pictographs
        "\U0001F6E9-\U0001F6E9"  # Miscellaneous Symbols and Pictographs
        "\U0001F6EB-\U0001F6EC"  # Miscellaneous Symbols and Pictographs
        "\U0001F6F0-\U0001F6F0"  # Miscellaneous Symbols and Pictographs
        "\U0001F6F3-\U0001F6F6"  # Miscellaneous Symbols and Pictographs
        "\U0001F6F7-\U0001F6F8"  # Miscellaneous Symbols and Pictographs
        "\U0001F6F9-\U0001F6FA"  # Miscellaneous Symbols and Pictographs
        "\U0001F7E0-\U0001F7EB"  # Symbols and Pictographs Extended-A
        "\U0001F7F0-\U0001F7F0"  # Symbols and Pictographs Extended-A
        "\U0001F90C-\U0001F90C"  # Supplemental Symbols and Pictographs
        "\U0001F90D-\U0001F90F"  # Supplemental Symbols and Pictographs
        "\U0001F910-\U0001F918"  # Supplemental Symbols and Pictographs
        "\U0001F919-\U0001F91E"  # Supplemental Symbols and Pictographs
        "\U0001F91F-\U0001F920"  # Supplemental Symbols and Pictographs
        "\U0001F921-\U0001F921"  # Supplemental Symbols and Pictographs
        "\U0001F922-\U0001F923"  # Supplemental Symbols and Pictographs
        "\U0001F924-\U0001F932"  # Supplemental Symbols and Pictographs
        "\U0001F933-\U0001F93A"  # Supplemental Symbols and Pictographs
        "\U0001F93C-\U0001F93E"  # Supplemental Symbols and Pictographs
        "\U0001F93F-\U0001F93F"  # Supplemental Symbols and Pictographs
        "\U0001F940-\U0001F945"  # Supplemental Symbols and Pictographs
        "\U0001F947-\U0001F94B"  # Supplemental Symbols and Pictographs
        "\U0001F94C-\U0001F94C"  # Supplemental Symbols and Pictographs
        "\U0001F94D-\U0001F94F"  # Supplemental Symbols and Pictographs
        "\U0001F950-\U0001F95E"  # Supplemental Symbols and Pictographs
        "\U0001F95F-\U0001F96B"  # Supplemental Symbols and Pictographs
        "\U0001F96C-\U0001F970"  # Supplemental Symbols and Pictographs
        "\U0001F971-\U0001F971"  # Supplemental Symbols and Pictographs
        "\U0001F973-\U0001F976"  # Supplemental Symbols and Pictographs
        "\U0001F97A-\U0001F97A"  # Supplemental Symbols and Pictographs
        "\U0001F97B-\U0001F97B"  # Supplemental Symbols and Pictographs
        "\U0001F97C-\U0001F97F"  # Supplemental Symbols and Pictographs
        "\U0001F980-\U0001F984"  # Supplemental Symbols and Pictographs
        "\U0001F985-\U0001F991"  # Supplemental Symbols and Pictographs
        "\U0001F992-\U0001F997"  # Supplemental Symbols and Pictographs
        "\U0001F998-\U0001F9A2"  # Supplemental Symbols and Pictographs
        "\U0001F9A3-\U0001F9A4"  # Supplemental Symbols and Pictographs
        "\U0001F9A5-\U0001F9AA"  # Supplemental Symbols and Pictographs
        "\U0001F9AB-\U0001F9AB"  # Supplemental Symbols and Pictographs
        "\U0001F9AC-\U0001F9AD"  # Supplemental Symbols and Pictographs
        "\U0001F9AE-\U0001F9AF"  # Supplemental Symbols and Pictographs
        "\U0001F9B0-\U0001F9B9"  # Supplemental Symbols and Pictographs
        "\U0001F9BA-\U0001F9BF"  # Supplemental Symbols and Pictographs
        "\U0001F9C0-\U0001F9C0"  # Supplemental Symbols and Pictographs
        "\U0001F9C1-\U0001F9C2"  # Supplemental Symbols and Pictographs
        "\U0001F9C3-\U0001F9CA"  # Supplemental Symbols and Pictographs
        "\U0001F9CB-\U0001F9CB"  # Supplemental Symbols and Pictographs
        "\U0001F9CC-\U0001F9CC"  # Supplemental Symbols and Pictographs
        "\U0001F9CD-\U0001F9CF"  # Supplemental Symbols and Pictographs
        "\U0001F9D0-\U0001F9D6"  # Supplemental Symbols and Pictographs
        "\U0001F9D7-\U0001F9D7"  # Supplemental Symbols and Pictographs
        "\U0001F9D8-\U0001F9DF"  # Supplemental Symbols and Pictographs
        "\U0001F9E0-\U0001F9E6"  # Supplemental Symbols and Pictographs
        "\U0001F9E7-\U0001F9E8"  # Supplemental Symbols and Pictographs
        "\U0001F9E9-\U0001F9E9"  # Supplemental Symbols and Pictographs
        "\U0001F9EA-\U0001F9EA"  # Supplemental Symbols and Pictographs
        "\U0001F9EB-\U0001F9EB"  # Supplemental Symbols and Pictographs
        "\U0001F9EC-\U0001F9EC"  # Supplemental Symbols and Pictographs
        "\U0001F9ED-\U0001F9ED"  # Supplemental Symbols and Pictographs
        "\U0001F9EE-\U0001F9EF"  # Supplemental Symbols and Pictographs
        "\U0001F9F0-\U0001F9F3"  # Supplemental Symbols and Pictographs
        "\U0001F9F4-\U0001F9F9"  # Supplemental Symbols and Pictographs
        "\U0001F9FA-\U0001F9FA"  # Supplemental Symbols and Pictographs
        "\U0001F9FB-\U0001F9FB"  # Supplemental Symbols and Pictographs
        "\U0001F9FC-\U0001F9FC"  # Supplemental Symbols and Pictographs
        "\U0001F9FD-\U0001F9FD"  # Supplemental Symbols and Pictographs
        "\U0001F9FE-\U0001F9FE"  # Supplemental Symbols and Pictographs
        "\U0001F9FF-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA70-\U0001FA73"  # Symbols and Pictographs Extended-A
        "\U0001FA74-\U0001FA74"  # Symbols and Pictographs Extended-A
        "\U0001FA78-\U0001FA7A"  # Symbols and Pictographs Extended-A
        "\U0001FA7B-\U0001FA7C"  # Symbols and Pictographs Extended-A
        "\U0001FA80-\U0001FA82"  # Symbols and Pictographs Extended-A
        "\U0001FA83-\U0001FA86"  # Symbols and Pictographs Extended-A
        "\U0001FA90-\U0001FA95"  # Symbols and Pictographs Extended-A
        "\U0001FA96-\U0001FAA8"  # Symbols and Pictographs Extended-A
        "\U0001FAA9-\U0001FAAC"  # Symbols and Pictographs Extended-A
        "\U0001FAAD-\U0001FAAD"  # Symbols and Pictographs Extended-A
        "\U0001FAE0-\U0001FAE8"  # Symbols and Pictographs Extended-A
        "\U0001FAF0-\U0001FAF8"  # Symbols and Pictographs Extended-A
        "]+", 
        flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text)

def clean_llm_response(response: str) -> str:
    """清理LLM响应，移除emoji和markdown标记
    
    Args:
        response: LLM原始响应
        
    Returns:
        清理后的响应
    """
    cleaned = remove_emojis(response)
    
    cleaned = re.sub(r'```json\s*', '', cleaned)
    cleaned = re.sub(r'```\s*', '', cleaned)
    
    cleaned = cleaned.strip()
    
    return cleaned


def build_immersion_enhancement(current_player_name: str, current_player_sanity: int, 
                                elapsed_minutes: int, _game_state: Dict[str, Any]) -> str:
    """构建沉浸感增强提示
    
    增强游戏的沉浸感和恐怖氛围
    
    Args:
        current_player_name: 当前玩家名称
        current_player_sanity: 当前玩家理智值
        elapsed_minutes: 已过的游戏时间（分钟）
        game_state: 游戏状态
        
    Returns:
        沉浸感增强提示文本
    """
    # 根据游戏进度计算恐怖强度（0-1）
    progress_intensity = min(1.0, elapsed_minutes / 300)  # 5小时后达到最大强度
    
    # 根据理智值计算疯狂程度
    madness_level = max(0, (100 - current_player_sanity) / 100)
    
    return f"""
**沉浸感增强要求（非常重要）：**

1. **时间流逝感知**：
   - 随着时间推移，环境的恐怖感应该逐渐增强
   - 深夜时分（>60分钟）：环境变得更加诡异，阴影似乎在移动
   - 午夜时分（>180分钟）：现实与虚幻的界限开始模糊
   - 黎明前（>300分钟）：最黑暗的时刻，恐怖达到顶峰
   - 当前游戏进度强度：{progress_intensity:.0%}

2. **生理反应细节**：
   - 描述玩家的生理反应，增强代入感
   - 心跳加速、呼吸急促、手心出汗
   - 肌肉紧绷、胃部不适、头晕目眩
   - 视线模糊、耳鸣、身体颤抖
   - 理智值越低，生理反应越强烈

3. **心理压迫层次**：
   - **表层恐惧**：对环境的恐惧（黑暗、密闭空间、异常声响）
   - **中层恐惧**：对未知的恐惧（隐藏在暗处的东西、无法解释的现象）
   - **深层恐惧**：对自我的怀疑（"我是否还是我自己？"、"我能相信我的感知吗？"）
   - 根据理智值逐层加深心理描写

4. **环境响应玩家行动**：
   - 玩家的行动应该引起环境的微妙响应
   - 搜索时：感觉有东西在避开你、阴影在角落里移动
   - 移动时：脚步声回响异常、身后似乎有跟随的脚步
   - 停留时：温度骤降、听到呼吸声、感觉被注视
   - 互动时：物品的反应异常（镜子里的倒影延迟、门把手冰冷刺骨）

5. **渐进式现实扭曲**：
   - 理智值>70：现实稳定，感知正常
   - 理智值40-70：开始出现轻微异常（眼角余光看到黑影、听到微弱的低语）
   - 理智值20-40：中度扭曲（墙壁似乎在呼吸、时间感混乱、看到不可能的事物）
   - 理智值<20：重度扭曲（空间折叠、多重现实叠加、无法区分幻觉与现实）
   - 当前玩家{current_player_name}的理智值为{current_player_sanity}，现实扭曲程度：{madness_level:.0%}

6. **多人协作沉浸**：
   - 同地点玩家间的微妙互动
   - 眼神交流：恐惧的传递、无声的警告
   - 肢体语言：颤抖的手指、紧张的姿态、退缩的动作
   - 群体心理：恐慌的传播、从众行为、集体幻觉
   - 分离焦虑：与其他玩家分开时的孤独感和恐惧加剧

7. **死亡暗示与预兆**：
   - 在玩家接近死亡条件时，提前给予暗示
   - 环境预兆：灯光剧烈闪烁、温度骤降、异味浓烈
   - 生理预兆：强烈的恶心感、心悸、视野边缘变暗
   - 心理预兆：强烈的直觉警告、莫名的绝望感、"这就是终点"的念头
   - 这些预兆应该让玩家感到"大事不妙"，但不直接揭示死亡

8. **叙事连贯性**：
   - 保持场景描述与之前事件的连贯性
   - 之前触发的机关应该持续产生影响
   - 玩家之前的行动应该在环境中留下痕迹
   - 避免重复描述相同的环境细节，而是展示环境的变化

9. **多感官交叉污染**：
   - 感官之间的界限开始模糊（理智值低时）
   - "听到"颜色：声音带有色彩（尖锐的红色、低沉的蓝色）
   - "看到"声音：空气中出现可视化的波纹
   - "尝到"恐惧：金属味、血腥味、腐臭味混合在口中
   - "触摸到"恐惧：空气变得粘稠、触感带有情绪

10. **叙事视角切换**：
    - 偶尔使用第二人称直接对玩家说话（理智值低时）
    - "你感觉到了吗？它在靠近..."
    - "不要回头，它就在你身后..."
    - "你终于来了，我等你很久了..."
    - 这种切换应该增强玩家的代入感和恐惧感

**当前游戏状态参考**：
- 游戏时间：{elapsed_minutes}分钟
- 恐怖强度：{progress_intensity:.0%}
- 现实扭曲：{madness_level:.0%}
- 建议氛围基调：{"极度压抑" if madness_level > 0.7 else "紧张不安" if madness_level > 0.4 else "诡异平静"}
"""
