# 变更记录

本文件记录规则怪谈插件的核心仓库重构变更，按"用户感知功能侧"与"开发侧"两部分组织，每部分按模块分组，每功能一行。不记录版本号提升与项目依赖升级。

## 用户感知功能侧

### 命令

- 新增 `/rg 对比规则 @某人` 命令，可对比两名玩家的规则笔记差异，便于多人协作推理

### NPC 与对话

- NPC 对话改为通过 LLM 生成，口吻与措辞更贴合角色设定
- NPC 引入说谎一致性约束，相同规则在多轮对话中口述保持一致，避免穿帮
- NPC INTERACT 加入冷却机制（默认 180 秒），冷却期内不会反复贴脸互动
- NPC 决策（逃跑/攻击）改读六维态度向量，行为更细腻、不再被单维好感度误判
- 引入追杀状态机，NPC 锁定追击目标后会持续 ATTACK，避免追一半突然转身

### 心理与理智

- 心理压力叙事每档扩到 6 条变体，第 3 次命中后 30% 概率改为静默反馈，避免审美疲劳
- 新增理智回复途径，在符合条件时玩家可恢复理智

### 场景与氛围

- 场景专属环境音在时段切换时不再被整体替换，改为按时段降权叠加保留
- 温度演化改为向"基底+目标"逼近的收敛映射，不再无限漂移

### 行动响应

- 感官事件被合并进判定 prompt 而非单独发消息，主流程行动响应更快
- NPC 后台模拟不再阻塞玩家行动反馈

### 规则与变异

- 规则条件改为结构化表达 + 确定性违规匹配，"连续违反规则""多次访问特殊位置"等条件可真正触发
- 规则变异冷却修复，变异不再被绕过连续触发

### 结局判定

- 引入硬性完成条件门槛，LLM 不能在硬门槛未达成时强行收尾

### 物理感知

- 房间级距离衰减与双人协作机关上线，相邻房间声音按距离/墙材质/门状态乘性衰减
- 房间间听力质量返回浮点值，禁止半径向下取整归零导致"听不见"

## 开发侧

### LLM 客户端

- 引入 tenacity 指数退避重试，对 429/5xx 及网络错误按 `max_retries` 重试后再故障转移
- 通过配置段 `max_concurrent` 创建 `asyncio.Semaphore` 做并发限流，不再硬编码 `Semaphore(8)`
- 复用 `aiohttp.ClientSession` 连接池，惰性创建并在跨调用间复用，`close()` 时释放

### 配置

- 配置 schema 与默认值统一到 `2.7.0` 口径，`plugin_config.py` 与 `core/config/settings.py` 同步
- `[llm]`/`[npc_sim]` 段补齐 `max_retries`/`max_concurrent`/`temperature`/`timeout` 字段及默认值
- `[npc_sim]` 新增 `default_wall_material`、`room_hearing_radius`、`tick_idle_threshold_seconds`、`tick_idle_scale_factor`、`tick_max_minutes_per_tick`、`npc_interact_cooldown_seconds` 字段

### 行动处理

- `action_processor.py` 由近 2900 行拆分为四个子服务：`npc_interaction`/`player_interaction`/`violation_consequence`/`rule_mutation`，主文件回落到约 1700 行
- 给物交互改用双方背包名词匹配，替代 `给...` 正则截取
- `_is_attack_action` 剔除单字"打"，避免"打听/打开/打电话"被误判为攻击
- 交互目标选择排除死亡玩家
- 延迟反馈按 `target_player_id` 匹配消费，不匹配的反馈留回 `pending_feedbacks` 队列
- `_apply_result` 落地世界状态加世界锁，保证 NPC 后台模拟与主流程行动结果并发安全

### 公共模块

- 抽取 `common/sound_utils.py`（声源强度推断）与 `common/door_utils.py`（门状态查询），消除 `room_topology` 与 `action_processor` 重复实现
- `room_topology.py` 新增统一 `_normalize_area` 入口，`normalize_rooms`/`build_room_graph`/`_infer_new_location` 共用

### 存档与序列化

- `wall_materials` 由 tuple-keyed dict 改为 `list[list[str, str]]` 格式，可被 JSON 直接序列化
- `save_manager` 在保存/加载路径做 `normalize_wall_materials_format` 规范化，兼容旧存档

### 死代码清理

- 删除 `NPC.update_attitude`（语义错误且零调用）
- 删除 `multiplayer_physics_system.py`/`horror_atmosphere.py`/`intent_parser.py` 等零引用死代码文件，价值能力已移植到 `room_topology.py`

### 规则变异

- 修复 `action_processor._trigger_rule_mutation` 绕过 `trigger_mutation` 导致 `mutation_cooldown` 失效的 bug
- 变异 metadata 同步修复

### 游戏时间

- `GameSession` 新增 `last_action_real_time` 字段记录最近一次有效行动的现实时间戳
- NPC tick 改为空闲补时模型，仅在玩家长时间无行动时按"无行动时长 × 倍率"折算补时

### 数据模型

- `GameSession` 新增 `completion_conditions`（硬性完成条件）与 `hunt_state`（追杀状态）字段
- `Player` 新增 `psych_tracking`（心理压力追踪）字段
- `NPCMemory` 新增 `rule_versions` 字段，记录 NPC 已知规则版本用于说谎一致性约束
