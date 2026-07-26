# 规则怪谈插件 - 技术文档

本文只描述仓库当前可验证的实现状态，适用版本为 `2.4.0`。

## 目录结构

插件目录当前实际存在的主要文件和目录如下：

```text
rule_horror_plugin-main/
├── _manifest.json
├── plugin.py
├── plugin_config.py
├── config.toml
├── README.md
├── QUICK_REFERENCE.md
├── TECHNICAL.md
├── TESTING_GUIDE.md
├── requirements.txt
├── common/
├── core/
│   ├── config/
│   ├── content/
│   ├── game/
│   ├── llm/
│   └── services/
├── helpers/
├── prompts/
├── systems/
├── typings/
└── commands/
```

## 模块职责

### `plugin.py`

入口插件，负责：

- 新版 `MaiBotPlugin` 生命周期与 `create_plugin()` 导出
- 插件生命周期初始化
- 配置覆盖合并
- 数据目录解析
- `/rg` 命令装配，并把执行委托给 `commands/handler.py`

当前 `plugin.py` 已经是薄入口，不再直接承载单人/多人开局、状态展示、提示、行动等大段业务逻辑。

### `plugin_config.py`

- `RuleHorrorPluginConfig` 及各配置子段模型
- 把原先堆在入口文件中的插件配置定义独立出来

### `commands/`

- `handler.py`: 薄命令装配层，只保留开始、强制开始、身份等少量分发逻辑
- `runtime_support.py`: 规则载体、NPC 运行时、图片发送、规则笔记、运行时辅助方法
- `shared_handlers.py`: 状态、规则、线索、提示、行动、存档、剧情等共享命令处理
- `session_runtime.py`: 会话运行时恢复与绑定（如 `_bind_environment_runtime`、`rehydrate_session_runtime`）
- `router.py`: 命令名到处理方法的路由表

### `flows/`

- `singleplayer_flow.py`: 单人开局与单人流程编排
- `multiplayer_flow.py`: 多人大厅、加入、身份、多人开局编排

### `core/config/`

- `settings.py`: Pydantic 配置模型
- `loader.py`: TOML 加载、覆盖合并、基础校验

### `core/game/`

- `models.py`: 主要数据模型，如 `GameSession`、`Player`
- `state_manager.py`: 运行时状态管理与锁控制
- `save_manager.py`: 默认存档、命名存档、清理逻辑

### `core/llm/`

- `client.py`: 通用 LLM 调用与 JSON 解析

### `core/content/`

- `image_generator.py`: 长图渲染、字体解析、缓存相关逻辑

### `core/services/`

包含主要业务服务，例如：

- `game_generator.py`: 开局内容生成
- `action_processor.py`: 行动处理主逻辑
- `ending_judge.py`: 结局判定
- `item_manager.py`: 道具与休息处理
- `immersive_feedback.py`: 沉浸式反馈
- `npc_simulator.py`: 使用独立模型推进 NPC 位置、动作和房间级感知事件
- `event_bus.py`: 事件总线
- `factories.py`: 工厂协议与实现，解耦 core 反向依赖
- `psychological_state.py`: 心理状态服务
- `pvp_combat.py`: PvP 战斗服务

### `systems/`

包含独立系统实现，例如：

- `environment_evolution.py`: 开局环境状态生成与保存
- `npc_system.py`: NPC 相关
- `room_topology.py`: 房间级拓扑、可见与可听范围判断（含门状态/声源强度/墙材质/障碍物）
- `rule_mutation_system.py`: 规则变异

## 配置事实

### 实际加载的配置段

当前 `core/config/loader.py` 实际加载：

- `[plugin]`
- `[llm]`
- `[[llm.models]]`
- `[npc_sim]`
- `[[npc_sim.models]]`
- `[save]`

- 不应把 `[environment]` 视为当前生效配置
- NPC 模拟系统优先使用 `[npc_sim]`，未单独配置时回退主 `[llm]`
- 环境演化系统使用主 LLM 配置参与运行

### 版本口径

当前版本相关文件的目标口径统一为 `2.4.0`：

- `_manifest.json`
- `config.toml`
- `core/config/settings.py`
- 用户文档

注意：`core/game/save_manager.py` 中存档数据结构的 `version` 字段仍为 `2.2.0`（属存档格式版本，与插件版本独立），不在上述统一口径范围内。

当前代码中的配置默认值、文档和清单已经统一到 `2.4.0` 口径。

## 命令与流程

### 命令入口

命令由 `plugin.py` 统一装配，随后委托给 `commands/handler.py` 和 `commands/shared_handlers.py`。常见命令包括：

- 开局类：`开始`、`强制开始`、`加入`、`离开`
- 查询类：`状态`、`剧情`、`规则`、`场景`、`区域`、`身份`、`道具`、`物品栏`、`背包`、`线索`
- 行为类：`行动`、`推理`、`记录规则`、`提示`、`继续`、`结束`
- 存档类：`保存`、`读取`、`恢复`、`存档列表`、`清理存档`
- 辅助类：`帮助`

`/rg 场景` 只组合 `scene_impression` 与玩家当前位置；场景结构中全部楼层区域和特殊区域由 `/rg 区域` 单独以去重列表展示，避免把区域清单混入场景叙述。

开局环境由 `EnvironmentEvolutionSystem.initialize_environment()` 使用主 LLM 生成并写入 `session.environment_state["environment_evolution"]`。该系统不再承担 NPC、规则、随机事件、身份权限或区域违规后果等已废弃职责。

### 规则笔记机制

当前实现中，规则信息分为两类：

- 客观暴露：规则载体、NPC 对话、探索结果等在世界中出现
- 玩家笔记：玩家主动整理后保存在 `Player.recorded_rules`

实际行为规则如下：

- `/rg 规则` 查看的是 `Player.recorded_rules`
- `/rg 记录规则 <内容>` 会把玩家确认或怀疑的规则写入自己的规则笔记
- 规则载体模式会把对应玩家可见的载体内容独立发送出来，并把展示出的载体规则同步写入玩家规则笔记
- 自然语言引导模式不再把 NPC 口述内容自动提取为规则，也不会触发开场规则发送；玩家需要自行推理和记录
- 为兼容旧存档，`GameSession.from_dict()` 会在加载时把旧的 `environment_state.known_rule_indices` / `known_rule_texts_extra` 一次性迁移进 `Player.recorded_rules`，随后清理旧键

### 规则载体与多人差异化可见性

当前实现里，规则载体运行时保存在 `environment_state["rule_carriers"]`，并具有这些稳定字段：

- `carrier_id`
- `title`
- `location`
- `visible_to`
- `revealed_rules`
- `description`
- `initially_visible`
- `requires_action`
- `discovered_by`

多人模式下，`game_generator.py` 会优先把模型生成的 `rule_carriers`、`identity_groups`、`shared_visibility_groups` 写入 `session.rule_network["multi_identity"]`。`plugin.py` 初始化运行时时会优先消费这些结构，只有模型缺字段时才会回退到本地默认载体拼装。

`/rg 规则` 的唯一展示来源仍然是 `Player.recorded_rules`。无论单人还是多人，玩家只能看到自己已经记录下来的规则，不会因为其他玩家发现了某个载体而自动同步笔记。

### 智能提示进度推断

`/rg 提示` 当前已经从"按 kind 硬切换"改造为"先推断进度，再选向引导"的流程，核心行为如下：

- 玩家输入参数（"规则"/"线索"）降级为**软偏好**，仅作次要参考；无参数调用时 LLM 完全自主决定引导方向
- LLM 内部完成"对比玩家规则笔记 ↔ 后台完整规则 ↔ 隐藏真相 → 识别（误解/遗漏/误信/未触及/接近/偏离）→ 选向"三步推理
- 输出 `guidance_target`（`rule` / `truth`）和 `progress_assessment`（10 字内进度标签），二者**仅写入日志**，不进入玩家可见消息
- 玩家收到的消息只包含 `hint` + `next_action`，格式为 `**提示（你还剩N次）**\n\n{hint}\n\n下一步建议：{next_action}`
- 多人模式下，`commands/runtime_support.py` 的 `CarrierService.collect_team_rules_for_hint(session, requester_id)` 会收集所有 `PlayerStatus.ALIVE` 玩家的规则笔记，调用者本人单独标注，其他玩家归入"队友笔记"，LLM 可见全队笔记用于进度推断
- 单人模式下该收集方法返回与原 `_get_player_rules_for_display` 等价的结构，行为不退化

剧透检测与异常处理遵循 AGENTS.md（不兜底、不静默吞异常）：

- 模块级 `_detect_spoiler(hint_text, next_action, hidden_truth, guidance_target) -> tuple[bool, list[str]]` 同时检测 hidden_truth 前 20 字片段泄露与 truth 方向真相关键词泄露
- 首次命中剧透检测时丢弃结果并重新调用 LLM 一次（temperature 提升至 0.7，user prompt 末尾追加"上一次生成疑似泄露真相，请严格避免"）
- 二次仍命中时直接 `raise RuntimeError("提示生成失败：检测到剧透风险")`，记录 error 日志（包含泄露关键词列表），由 `_handle_提示` 外层 except 向玩家发送错误信息
- LLM 调用本身的异常（网络/非 JSON/解析失败）不在提示模块内部静默吞掉，统一向上抛出
- 原 `if not hint_text:` 兜底文案分支已彻底删除

### 行动处理链

常规流程如下：

```text
用户输入
  -> plugin.py 装配命令对象
  -> commands/handler.py 路由到对应处理器
  -> ActionProcessor.process_action()
  -> ItemManager 优先处理道具/休息
  -> 其它行动再进入 LLM 判定
  -> runtime_support/shared_handlers 补充规则载体发现、NPC 感知、存档
  -> 生成行动结果图片或文本
```

当前实现中，道具和休息属于高优先级分支，命中后不会再走同一轮的常规 LLM 行动判定。

当前行动上下文会把这些信息提供给主流程判定：

- 玩家当前规则笔记
- 身份、任务、责任区域、独有信息
- 当前房间可见的规则载体
- 同房间可见 NPC
- 相邻房间可听 NPC 动静和房间事件

旧的 `Player.unique_rules` 仍保留在数据模型中用于兼容旧存档和后台信息，但不再作为玩家默认已知规则喂给行动判定 prompt。

## 多人身份机制

当前实现的多人身份发送规则如下：

- 开局后，插件会为每位玩家构造一份私聊简报
- 简报内容包含身份、身份简介、任务、责任区域、开场观察和独有信息
- 简报不再直接包含个人规则或共同规则正文
- 系统优先调用私聊发送接口
- 如果私聊失败，群内只提示送达失败与补救方式，不发送身份正文
- 玩家执行 `/rg 身份` 时，也遵循相同逻辑：优先私聊，失败时只提示补救

因此，当前代码已经符合“身份正文只通过私聊送达，失败时不在群聊泄露”的约束。

## NPC 运行时机制

当前实现使用两层协作：

- `action_processor.py`: 负责主流程行动结果、叙事和直接后果
- `npc_simulator.py`: 负责 NPC 的下一步位置、动作、房间事件与玩家可感知提示

NPC 运行时主要保存在：

- `environment_state["npcs"]`
- `environment_state["npc_runtime"]`
- `environment_state["room_graph"]`

房间级感知规则为：

- 同房间：可直接看到 NPC
- 相邻房间：可根据 `room_hearing_radius` 听到动静
- 更远房间：默认不感知

`npc_simulator.py` 当前会接收完整规则、隐藏真相、规则载体、房间图、玩家位置和 NPC roster，并输出结构化结果写回运行时，包括：

- `npc_updates`
- `room_events`
- `visible_events`
- `audible_events`
- `carrier_state_updates`
- `player_perception_hints`

## LLM 能力事实

### `core/llm/client.py`

主 `LLMClient` 当前已经实现：

- 兼容 OpenAI Chat Completions 风格接口
- 优先使用启用的 `[[llm.models]]`，未配置时回退到 `model_list`
- 对多种 `message.content` 格式进行文本提取
- 对 JSON 响应做清洗、Markdown 去壳、片段提取和有限修复
- 合并 `default_body` 与模型级 `extra_body`
- 读取模型级或全局 `timeout` 设置请求超时
- 通过 `asyncio.Semaphore(8)` 做全局并发限流（在 `call` 入口获取信号量，避免多玩家同时行动打爆 API）

主 `LLMClient` 当前没有实现为通用能力的内容：

- 连接池复用
- 读取 `max_retries` 执行统一自动重试

当前每次调用仍会创建新的 `aiohttp.ClientSession`，尚未做连接池复用。

### `systems/environment_evolution.py`

环境演化系统直接复用主 `LLMClient` 进行 LLM 调用，不包含独立的 HTTP/重试逻辑。这意味着：

- 全插件的 LLM 调用统一走 `LLMClient`，受其 `asyncio.Semaphore(8)` 限流与 `timeout` 配置约束
- 环境演化使用主 LLM 配置参数，但不是通过独立 `[environment]` 段来完成配置注入

## 图像与数据

### 图像

`core/content/image_generator.py` 负责生成：

- 剧情导入图
- 入场长图
- 规则图
- 行动结果图
- 结局图
- 道具图、线索图等附加信息图

字体路径优先级为：

1. `RULE_HORROR_FONT`
2. `plugin.font_path`
3. 自动探测或默认字体

Linux 下额外行为：

- 会优先探测常见的 `Noto Sans CJK`、`文泉驿`、`DejaVu`、`Liberation Sans` 路径
- 若所有候选字体都不可用，会记录告警并回退到 Pillow 默认字体
- 图片缓存索引会在缓存目录内通过临时文件原子写入，降低异常中断导致索引损坏的概率

### 数据目录

插件会使用 `resolve_data_dir()` 解析可写数据目录，常见用途包括：

- `data/saves/`: 默认存档和命名存档
- `data/temp_images/`: 运行期图片与缓存

当插件目录不可写时，路径可能回退到其它可写目录，具体由运行环境决定。

Linux/容器下的实际回退顺序为：

1. 插件目录下的 `data/`
2. `${XDG_DATA_HOME}/maibot/rule_horror/`
3. `~/.local/share/maibot/rule_horror/`
4. 系统临时目录下的 `maibot/rule_horror/`

## 存档事实

`core/game/save_manager.py` 当前具备这些特征：

- 默认存档支持普通 JSON 与 `.json.gz`
- 命名存档使用单独文件
- 批量保存使用队列保留最近若干版本
- 写入时使用临时文件再原子替换
- 支持清理已结束存档与旧图片缓存
- 临时文件与目标文件保持在同一目录内，避免 Linux 跨文件系统移动时破坏原子替换语义

## 状态与线程安全

### `GameStateManager`

运行时状态管理采用：

- 单例实例
- 全局状态字典
- 异步锁与超时保护
- 获取状态后必须显式释放

### `SaveManager`

存档管理采用：

- 单例实例
- `threading.Lock` 保护实例创建
- `asyncio.Lock` 保护待保存队列

## 测试事实

详见 `TESTING_GUIDE.md`。

## 文档导航

- [README.md](README.md): 用户说明与配置入口
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md): 命令与配置速查
- [TESTING_GUIDE.md](TESTING_GUIDE.md): 当前版本测试指南
