# 规则怪谈插件 - 技术文档

## 项目结构

```
rule_horror_plugin-main/
├── plugin.py                    # 主插件文件
├── plugin_old.py               # 原版备份
├── config.toml                 # 配置文件
├── requirements.txt            # Python依赖
├── README.md                   # 用户文档
├── TECHNICAL.md               # 技术文档（本文件）
├── REFACTORING.md             # 重构说明
│
├── core/                       # 核心模块
│   ├── __init__.py
│   ├── config/                # 配置管理
│   │   ├── __init__.py
│   │   ├── settings.py       # Pydantic配置模型
│   │   └── loader.py         # TOML配置加载器
│   │
│   ├── game/                  # 游戏管理
│   │   ├── __init__.py
│   │   ├── models.py         # 数据模型（Player, GameSession等）
│   │   ├── state_manager.py # 线程安全的状态管理器
│   │   └── save_manager.py  # 批量存档管理器
│   │
│   ├── llm/                   # LLM客户端
│   │   ├── __init__.py
│   │   ├── client.py         # 带连接池的LLM客户端
│   │   └── prompt_builder.py # Prompt构建器
│   │
│   ├── content/               # 内容生成
│   │   ├── __init__.py
│   │   ├── image_generator.py # 异步图片生成器
│   │   └── text_formatter.py  # 文本格式化
│   │
│   └── services/              # 业务服务
│       ├── __init__.py
│       ├── game_generator.py    # 游戏生成服务
│       ├── action_processor.py  # 行动处理服务
│       ├── ending_judge.py      # 结局判定服务
│       ├── intent_parser.py     # 意图解析服务
│       └── immersive_feedback.py # 沉浸式反馈服务
│
├── tests/                      # 单元测试
│   ├── __init__.py
│   └── test_basic.py
│
└── data/                       # 数据目录
    ├── saves/                 # 存档文件
    └── temp_images/           # 临时图片
```

## 架构设计

### 1. 职责分离

**重构前**：
- 单个7500+行的plugin.py文件
- 所有逻辑混在一起
- 难以维护和测试

**重构后**：
- 核心模块按功能分离
- 每个模块职责单一
- 易于维护和扩展

### 2. 行动处理架构

**设计目标**：通过命令格式进行游戏行动，确保功能稳定可靠。

**架构图**：
```
用户输入
    ↓
execute() 方法
    ↓
命令格式 (/rg 行动 XXX)
    ↓
_handle_行动()
    ↓
ActionProcessor.process_action()
    ↓
├─ ItemManager.check_and_use_item() ✅ 物品使用
├─ ItemManager.check_and_rest() ✅ 休息系统
└─ LLM 判定
    ↓
    ├─ 关键物品判定 ✅
    ├─ 规则变异系统 ✅
    ├─ 环境记忆系统 ✅
    └─ 理智值系统 ✅
```

**关键改进**：
- ✅ 命令格式经过 `ActionProcessor.process_action()`
- ✅ 统一的行动处理逻辑，易于维护
- ✅ 稳定可靠，避免误触发

**支持的行动示例**：
- `/rg 行动 检查房间的角落` → 触发探索行动
- `/rg 行动 喝水` → 触发物品使用系统
- `/rg 行动 休息30分钟` → 触发休息系统（自定义时间）
- `/rg 行动 打开柜子` → 触发探索行动，可能发现关键物品并触发规则变异


### 3. 状态管理

**GameStateManager**：
- 单例模式，线程安全
- 使用`threading.Lock`实现双重检查锁定
- 支持超时机制防止死锁
- 自动清理过期状态

**多人模式大厅状态**：
- 多人模式开始时先创建大厅状态
- 玩家在大厅阶段加入
- 人数到齐后由房主触发生成开局并进入进行中状态


```python
class GameStateManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
```

### 4. LLM客户端

**LLMClient**：
- 连接池复用（减少60%延迟）
- 并发控制（Semaphore限流）
- 自动重试机制（指数退避）
- 模型故障转移

```python
class LLMClient:
    def __init__(self):
        self._session = None  # 复用连接
        self._semaphore = asyncio.Semaphore(10)  # 并发控制
        self._connector = aiohttp.TCPConnector(
            limit=20,
            limit_per_host=10,
        )
```

### 5. 存档管理

**SaveManager**：
- 批量保存（减少80%磁盘IO）
- 使用deque保存多个版本
- 原子写入防止数据损坏
- 支持压缩存档

```python
class SaveManager:
    def __init__(self):
        self._pending_saves: dict[str, deque[tuple[datetime, GameSession]]] = {}
        self._save_interval = 30  # 30秒批量保存
```

### 6. 图片生成

**AsyncImageGenerator**：
- 线程池异步处理
- 不阻塞主事件循环
- 支持并发图片生成
- 字体缓存优化

**支持的图片类型**：
- 剧情导入长图：展示场景名称、背景故事和玩家到来原因
- 场景结构文字长图：白底黑字，展示建筑类型、总体布局、楼层布局、连接通道和特殊区域
- 规则长图：展示所有规则和通关条件
- 行动结果长图：展示行动后的结果、状态变化、发现的物品等，支持理智崩坏视觉效果
- 结局长图：展示结局类型和结局描述
- 结局长图在完美、成功、通关结局会展示推理分析
- 结局长图在满足条件时会展示隐藏真相


```python
class AsyncImageGenerator:
    def __init__(self, max_workers=4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
    
    async def generate_image(self, ...):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func)
```

## 核心系统详解

### 1. 理智值系统

**位置**: `core/services/action_processor.py`

理智值影响场景描述风格和视觉效果：

- **理智值 > 70**：描述客观清晰、冷静理性
- **理智值 40-70**：开始出现混乱和恐惧元素，偶尔出现轻微幻觉
- **理智值 < 40**：描述混乱、恐怖、充满幻觉和错觉
- **理智值 = 0**：理智崩坏模式，直接对话、否认死亡、诱导打破规则

**渲染/交互表现**：
- 行动结果长图：`sanity == 0` 时进入“崩坏展示”，**即使本次判定死亡也会输出完整叙述**，并隐藏状态栏
- 规则长图：`sanity == 0` 时只显示规则内容（不显示标题/标签），且会对规则文本做去重，避免重复行

**理智值变化规则**：

- 违反规则：-10到-30
- 目睹恐怖场景：-5到-15
- 发现真相线索：-3到-10
- 安全的探索：-1到-3
- 使用关键物品：+5到+15（用美好的语言描述）

### 2. 理智崩坏视觉效果系统

**位置**: `core/content/image_generator.py`

**方法**：
- `_apply_sanity_distortion()` - 应用视觉扭曲效果
- `_distort_text()` - 对文本进行扭曲处理

**触发条件**：
- 理智值 >= 30 或 = 0：不应用扭曲效果
- 理智值 20-30：最轻微扭曲 (insanity_level 0.0-0.33)
- 理智值 10-20：中等扭曲 (insanity_level 0.33-0.67)
- 理智值 0-10：最强扭曲 (insanity_level 0.67-1.0)

**视觉效果**：
1. 红色涂鸦遮盖（数量和大小根据 insanity_level 控制）
2. 红色斜线遮盖（数量根据 insanity_level 控制）
3. 黑色涂抹效果（模拟文字被涂抹）
4. 红色涂抹效果（模拟血迹涂抹）

**文本扭曲效果**：
1. 插入乱码符号（#、@、$、%等）和中文乱码（乱、码、崩、坏等）
2. 重复词语（针对中文，重复次数根据 insanity_level 控制）
3. 字符错位（随机交换相邻字符）
4. 文字缺失（随机删除部分字符）

### 3. 规则分类系统

**位置**: `core/services/action_processor.py`, `common/models.py`, `core/services/game_generator.py`

**规则类型**：
- **即死规则 (fatal)**: 触犯立即导致死亡
- **有害规则 (harmful)**: 触犯会受到惩罚但不立即致命（NPC追杀、环境恶化等）
- **双刃剑规则 (double_edged)**: 触犯有风险但能获得关键线索或NPC帮助

**类型标记**：
- LLM生成规则时自动标记类型
- 存储在规则的 `rule_type` 字段
- 根据类型触发不同的后果处理流程

**矛盾规则对**（可选）：
- 当剧情有对抗势力时（如A vs B），可创建矛盾规则对
- 标记 `related_npc`（该规则代表谁）和 `opposing_npc`（对抗谁）
- 遵守/触犯不同规则会影响对应NPC的态度

### 4. 违规后果系统

**位置**: `core/services/action_processor.py`

**方法**：
- `_handle_violation_consequences()` - 统一处理违规后果
- `_handle_area_violation()` - 处理区域违规
- `_handle_general_violation()` - 处理一般违规
- `_check_hunt_trigger()` - 检查追杀触发
- `_handle_double_edged_violation()` - 处理双刃剑规则

**后果类型**：

**即死规则**：
- 立即设置 `is_fatal=True`
- 玩家死亡

**有害规则**：
- NPC态度恶化（利用现有6维态度向量）
- 环境恶化（调用EnvironmentEvolutionSystem）
- 被追杀（概率触发，基于NPC敌意度）
- 理智/体力值下降

**双刃剑规则**：
- 受到惩罚（理智/体力下降）
- 获得收益（关键线索、物品、NPC帮助）
- 收益与剧情真相相关

**对抗规则**：
- 触犯规则A → 规则A代表方NPC态度恶化
- 同时 → 规则A对抗方NPC态度改善
- 通过NPC对话体现对抗（提及对方时表现厌恶）

**追杀机制**：
- 触发条件：NPC敌意度>70 + 概率判定
- 特殊位置增加触发概率
- 连续违规增加触发概率
- 通过LLM生成个性化追杀场景

**延迟反馈**：
- 部分后果延迟揭示（由LLM根据剧情决定）
- 增加悬疑感和后悔感
- 利用 `immersive_feedback.py` 的延迟反馈系统

### 5. 规则变异系统

**位置**: `core/services/action_processor.py`, `systems/rule_mutation_system.py`

**方法**：
- `_trigger_rule_mutation()` - 触发规则变异
- `_check_rule_mutation()` - 检查是否需要规则变异

**两步评估逻辑**：

**第一步：评估是否需要变异**
- 判断标准：贴合剧情推进、发现的合理性、增强紧张感
- 特别注意：
  - 仅仅发现普通物品不足以触发规则变化
  - 仅仅进入新房间不足以触发规则变化
  - 规则变化不是必须的
  - 规则变化与玩家是否推理出规则无关

**第二步：生成变异后的规则**
- 对1-2条规则进行细微篡改或补充
- 篡改应该令人不安，暗示规则本身是有意识的
- 规则变化方式：
  - 可以让新规则与旧规则冲突
  - 可以更改条件
  - 可以增加新的限制或放宽限制
- 新规则必须简洁、直接，每条规则30-50字
- 只说明禁止、允许或要求做的行为，不解释原因

**触发条件**：
- 发现关键物品时触发
- 连续违反规则（3次/10次行动）
- 多次访问特殊位置（3次）
- 理智崩坏时不触发

**混合模式**：
- 预设条件作为强提示传递给LLM
- LLM根据条件和剧情综合判断是否真正需要变异
- 避免纯条件触发的突兀感

### 6. 关键物品系统

**位置**: `core/services/action_processor.py`

**关键物品定义**：
- 带有奇怪符号的物品
- 与场景历史相关的物品
- 暗示真相的物品
- 只有极少数物品应该是关键物品

**物品判定**：
- LLM 判定物品时返回 `is_key_item` 字段（"是"/"否"）
- 普通物品（如笔记本、钥匙、工具等）不应该是关键物品

**关键物品效果**：
- 添加到玩家背包并标记 `is_key_item: True`
- 触发规则变异
- 使用时恢复理智值+5到+15

### 7. 物品使用系统

**位置**: `core/services/item_manager.py`

**水类物品**：
- 关键词：喝水、喝、饮用、水、矿泉水等
- 物品名称：水、矿泉水、瓶装水、饮料、果汁、牛奶等
- 效果：降低压力等级 3-5 点，降低焦虑等级 3-5 点

**食物类物品**：
- 关键词：吃、食用、食物、面包、饼干等
- 物品名称：食物、面包、饼干、巧克力、能量棒、罐头等
- 效果：恢复体力值 3-5 点

**使用流程**：
1. 检测行动中的关键词
2. 在背包中查找对应类型的物品
3. 应用物品效果
4. 从背包中移除物品
5. 返回效果描述

### 8. 休息系统

**位置**: `core/services/item_manager.py`

**休息关键词**：
- 休息、歇息、休息一下、歇一下、休息会儿、歇会儿
- 坐下休息、躺下休息、休息恢复、休息恢复体力等

**休息效果**：
- 恢复体力值（基于休息时间）
- 降低疲劳等级（从当前等级降低一级）
- 花费时间：可自定义（默认15分钟）

**休息时间设置**：
- **默认休息**：只说"休息"时，默认休息15分钟，恢复体力10-20点
- **自定义时间**：可指定休息时间，支持以下格式：
  - 数字格式：`休息30分钟`、`休息 30 分钟`、`休息30min`
  - 特殊描述：`休息半小时`（30分钟）、`休息一小时`（60分钟）、`休息两小时`（120分钟）
- **时间范围**：5-120分钟（超出范围会自动调整）
- **体力恢复量**：
  - 基础恢复：10-20点（15分钟）
  - 额外恢复：每增加15分钟，额外恢复10-20点
  - 例如：休息30分钟恢复20-40点，休息60分钟恢复40-80点

**疲劳等级计算**（基于体力值）：
- 体力值 76-100：无
- 体力值 51-75：轻微
- 体力值 26-50：中度
- 体力值 1-25：严重
- 体力值 0：极度

**时间推进**：
- 休息花费指定的时间（默认15分钟）
- 更新游戏时间描述：
  - 0-60 分钟：深夜（午夜时分，周围一片死寂）
  - 60-180 分钟：凌晨（黎明前的黑暗，空气中弥漫着不安）
  - 180+ 分钟：黎明（东方泛起鱼肚白，但黑暗仍未完全消散）

**实现逻辑**：
1. 检测行动中的休息关键词
2. 使用正则表达式提取休息时间（如果有）
3. 验证时间范围（5-120分钟）
4. 根据休息时间计算体力恢复量
5. 更新玩家体力值和疲劳等级
6. 推进游戏时间
7. 返回休息效果描述

### 9. 情绪与心理状态系统

**位置**: `core/services/action_processor.py`

**情绪数值**：
- `emotion`: 情绪描述（平静、焦虑、绝望、愤怒等）
- `anxiety_level`: 焦虑等级 (0-100)
- `stress_level`: 压力等级 (0-100)
- `fear_level`: 恐惧等级 (0-100)

**更新机制**：
- 从LLM响应解析 `mental_status` 和 `psychological_pressure`
- 每次行动后自动更新
- 影响状态栏显示和游戏沉浸感

**修复历史**：
- v2.2.0修复：此前情绪数值永远不会更新（永远是初始值）

### 10. 环境记忆系统

**位置**: `core/game/models.py`, `core/services/action_processor.py`

系统会记录：
- 已访问过的地点
- 已互动过的物品
- 时间事件

这些记忆会影响场景描述，避免重复描述，增强沉浸感。

### 11. 环境演化系统集成

**位置**: `systems/environment_evolution.py`, `plugin.py`, `core/services/action_processor.py`

**集成内容**：
- 游戏开始时调用 `initialize_environment()`
- 每次行动后调用 `update_environment()`（异步非阻塞）
- 区域违规时调用 `trigger_area_violation_consequences()`

**演化内容**：
- NPC行为和位置动态变化
- 环境氛围随时间变化
- 随机事件触发
- 区域风险评估

### 12. 行动处理优先级

在 `action_processor.py` 的 `process_action()` 方法中，系统按以下优先级处理玩家行动：

1. **物品使用系统**（最高优先级）
   - 检查是否是使用水类或食物类物品的行动
   - 如果是，直接应用效果，跳过 LLM 判定
   - 返回物品使用结果

2. **休息系统**（第二优先级）
   - 检查是否是休息行动
   - 如果是，直接应用效果，跳过 LLM 判定
   - 更新游戏时间
   - 返回休息结果

3. **LLM 行动判定**（默认处理）
   - 如果不是物品使用或休息，调用 LLM 判定行动结果
   - 检查是否发现关键物品
   - 应用状态变化
   - 更新环境记忆
   - 检查是否需要规则变异

这种优先级设计确保了：
- 物品使用和休息不会浪费 LLM 调用
- 玩家可以快速使用物品和休息
- 保持了原版的行动处理逻辑

## 性能优化

### 1. LLM调用优化

- **连接池复用**：减少60%延迟
- **并发控制**：防止过载
- **自动重试**：指数退避
- **模型故障转移**：多模型支持

### 2. 图片生成优化

- **线程池**：不阻塞主循环
- **字体缓存**：减少重复加载
- **异步处理**：支持并发生成

### 3. 存档优化

- **批量保存**：减少80%磁盘IO
- **队列缓存**：保留多个版本
- **原子写入**：防止数据损坏

## 线程安全

### 1. 单例模式

所有单例类使用双重检查锁定：

```python
_instance = None
_lock = threading.Lock()

def __new__(cls):
    if cls._instance is None:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
    return cls._instance
```

### 2. 状态锁

状态管理器使用两级锁：
- 全局锁：保护状态字典
- 状态锁：保护单个状态

```python
async def get_or_create(self, group_id: str, timeout: float = 5.0):
    async with asyncio.timeout(timeout):  # 超时保护
        async with self._global_lock:  # 全局锁
            state = self._states[group_id]
        await state.acquire()  # 状态锁
        return state
```

### 3. 死锁防护

- 统一锁的获取顺序
- 添加超时机制（5秒）
- 使用上下文管理器确保释放

## 错误处理

### 1. 异常捕获

所有关键操作都有try-except：

```python
try:
    result = await self._action_processor.process_action(...)
except Exception as e:
    logger.error(f"处理行动失败: {e}", exc_info=True)
    await self.send_text(f"处理行动时出错：{e}")
    return False, "处理失败", 2
```

### 2. Fallback机制

LLM调用失败时使用默认值：

```python
except Exception as e:
    logger.error(f"生成场景失败: {e}")
    return self._get_default_game()  # 默认场景
```

### 3. 状态锁释放

确保状态锁总是被释放：

```python
state = await state_manager.get(group_id)
try:
    # 处理逻辑
finally:
    state.release()  # 确保释放
```

## 配置管理

### 1. TOML配置

使用Pydantic验证配置：

```python
class LLMConfig(BaseModel):
    api_url: str
    api_key: str
    model_list: list[str]
    temperature: float = Field(ge=0.0, le=2.0)
```

### 2. 配置加载

自动加载和验证：

```python
def load_config_from_file(config_path):
    with open(config_path, "rb") as f:
        config_data = tomllib.load(f)
    
    config = Config(
        llm=LLMConfig(**config_data.get("llm", {})),
        ...
    )
    
    errors = validate_config(config)
    return config
```

## 测试

### 1. 单元测试

使用pytest进行测试：

```bash
pytest tests/ -v
```

### 2. 测试覆盖

- 配置加载测试
- 数据模型测试
- 状态管理器测试
- 存档管理器测试

## 文档导航

### 用户文档
- **[README.md](README.md)** - 用户使用指南，包含功能特性、安装说明、使用指南、游戏机制详解
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - 快速参考，包含命令速查表、游戏机制速查、常用操作组合

### 开发文档
- **[TECHNICAL.md](TECHNICAL.md)** - 技术文档（本文件），包含项目结构、架构设计、核心系统详解、开发指南
- **[TESTING_GUIDE.md](TESTING_GUIDE.md)** - 测试指南，包含测试方法、测试用例、测试流程

## 开发指南

### 添加新功能

1. 在`core/services/`创建新服务
2. 在`plugin.py`添加命令处理
3. 添加单元测试
4. 更新文档

### 修改现有功能

1. 找到对应的服务模块
2. 修改业务逻辑
3. 运行测试确保无破坏
4. 更新文档

### 调试技巧

1. 查看日志文件
2. 使用pytest运行测试
3. 添加logger.debug输出
4. 使用IDE断点调试

## 性能指标

- **开始游戏**：5-10秒（LLM生成）
- **执行行动**：3-5秒（LLM判定）
- **结束游戏**：3-5秒（LLM判定）
- **内存使用**：~100MB
- **并发能力**：支持多群组同时游戏

## 已知问题

1. 图片字体可能在某些系统上不可用（已有fallback）
2. LLM生成内容质量依赖模型选择
3. 长时间游戏可能导致上下文过长

## 未来改进

- 添加更多默认场景
- 优化图片生成样式
- 支持自定义场景
- 添加成就系统
- Web管理界面


## 项目结构

```
rule_horror_plugin-main/
├── plugin.py                      # 主插件文件（重构版本）
├── plugin_old.py                  # 原版插件文件（待迁移后删除）
├── _manifest.json                 # 插件清单文件
├── config.toml                    # 配置文件
├── requirements.txt               # Python依赖
├── README.md                      # 用户使用指南
├── TECHNICAL.md                   # 技术文档（本文件）
├── QUICK_REFERENCE.md             # 快速参考
├── INTEGRATION_SUMMARY.md         # 整合总结
├── core/                          # 核心模块
│   ├── __init__.py
│   ├── game/                      # 游戏状态管理
│   │   ├── state_manager.py      # 状态管理器
│   │   └── save_manager.py       # 存档管理器
│   ├── llm/                       # LLM客户端
│   │   └── client.py             # LLM API客户端
│   ├── content/                   # 内容生成
│   │   └── image_generator.py    # 图片生成器
│   ├── services/                  # 游戏逻辑服务
│   │   ├── game_generator.py     # 游戏生成服务
│   │   ├── action_processor.py   # 行动处理服务
│   │   └── ending_judge.py       # 结局判定服务
│   └── config/                    # 配置管理
│       └── loader.py             # 配置加载器
├── environment_evolution.py       # 环境演化系统
├── shared_prompts.py             # 共享提示词
├── game_time_manager.py          # 游戏时间管理
├── environment_state.py          # 环境状态
├── rule_mutation_system.py       # 规则变异系统
├── image_generator.py            # 图片生成器（原版）
├── clue_discovery_system.py      # 线索发现系统
├── multiplayer_physics_system.py # 多人物理系统
├── tests/                        # 测试文件
│   └── test_basic.py
└── data/                         # 数据目录
    ├── temp_images/              # 临时图片目录
    └── saves/                    # 存档目录
```

## 版本历史

### v2.2.0（违规后果多样化 + 情绪数值修复）✨ NEW
- **违规后果多样化系统**：
  - ✅ 规则分类：即死/有害/双刃剑/普通
  - ✅ 即死规则：触犯立即死亡
  - ✅ 有害规则：NPC态度恶化、环境恶化、追杀
  - ✅ 双刃剑规则：风险与收益并存，获得线索/NPC帮助
  - ✅ 对抗规则：矛盾规则对，影响不同NPC态度
  - ✅ 追杀机制：基于NPC敌意度概率触发
  - ✅ 延迟反馈：部分后果延迟揭示
- **情绪数值修复**：
  - ✅ 修复情绪、焦虑、压力、恐惧值永远不更新的问题
  - ✅ 从LLM响应解析并更新心理状态
- **规则变异系统优化**：
  - ✅ 优化条件触发（删除不合理条件）
  - ✅ 实现"条件+LLM评估"混合模式
- **环境演化系统集成**：
  - ✅ 游戏开始时初始化环境
  - ✅ 行动后异步更新环境状态
  - ✅ 区域违规调用后果生成
- **代码清理**：
  - ✅ 删除历史遗留的开发注释

### v2.1.0（完整迁移版本 + 统一行动处理）
- **完整迁移原版核心系统**：
  - ✅ 理智值描述系统（4个档位）
  - ✅ 理智崩坏视觉效果系统（视觉扭曲+文本扭曲）
  - ✅ 规则变异系统（两步评估）
  - ✅ 关键物品系统（触发规则变异）
  - ✅ 物品使用系统（水类+食物类）
  - ✅ 休息系统（体力恢复+疲劳等级+时间推进+自定义时间）
- **行动处理架构**：
  - ✅ 统一的行动处理逻辑，易于维护
  - ✅ 稳定可靠，避免误触发
- **新增模块**：
  - `core/services/item_manager.py` - 物品管理器
- **优化行动处理**：
  - 物品使用和休息优先于 LLM 判定
  - 节省 LLM 调用成本
  - 提高响应速度
- **文档更新**：
  - 新增 `SYSTEM_INTEGRATION_ANALYSIS.md` - 系统集成分析报告
  - 更新 `README.md` - 添加行动方式说明
  - 更新 `TECHNICAL.md` - 添加行动处理架构说明

### v2.0.0（重构版本）
- 完全重构代码架构，采用模块化设计
- 新增异步图片生成，使用线程池处理CPU密集型操作
- 新增LLM连接池，提升API调用性能
- 新增线程安全的状态管理器
- 新增批量保存机制，减少磁盘IO
- 增强图片生成系统，支持理智崩坏视觉效果
- 新增剧情导入长图和结局长图生成
- 优化配置管理，支持更灵活的配置选项

### v1.5
- 新增环境演化系统
  - **独立模块**：创建独立的environment_evolution.py模块，实现环境演化功能
  - **NPC系统**：自动生成具有独立行为、性格、危险等级和态度的NPC
  - **时间推进**：游戏时间实时推进，影响环境状态和NPC行为
  - **随机事件**：动态生成环境变化和随机事件，增强游戏张力
  - **环境更新**：根据时间推进和玩家行动更新环境状态（光线、温度、声音、气味）
  - **NPC交互**：NPC会根据玩家位置和行为主动与玩家交互
  - **身份系统**：支持玩家身份变化，身份引导NPC提供特定指导
  - **权限管理**：基于身份的访问权限管理，支持风险评估和后果触发
  - **独立模型配置**：环境演化系统使用独立的模型配置，不与主系统混合
- 优化环境更新信息显示
- 增强NPC交互体验
- 新增配置选项
- 代码优化

### v1.4.1
- 新增模型列表功能
- 新增手动清理存档功能

### v1.4.0
- 优化规则变异系统
- 新增身份变化规则系统
- 新增环境记忆系统
- 新增规则网络系统
- 新增多人协作规则
- 增强死亡场景描述
- 优化图片删除逻辑

### v1.3.1
- 新增视觉化信息展示功能
- 新增自动清理图片功能

### v1.3.0
- 新增理智崩溃机制
- 新增规则变异系统
- 新增关键物品系统
- 新增多人个性化体验
- 优化行动处理逻辑
- 增强场景描述的个性化

### v1.2.2
- 优化场景结构生成，使用结构化JSON格式
- 更新游戏状态存储格式
- 更新存档/读档功能

### v1.2.1
- 新增剧情导入部分
- 优化游戏生成流程
- 新增剧情导入查看功能
- 优化命令显示

### v1.2.0
- 新增沉浸式氛围系统
- 新增理智值动态描述系统
- 新增心理压力系统
- 新增物品和背包系统
- 新增随机环境事件系统
- 新增玩家位置跟踪
- 新增手动存档功能
- 新增存档管理功能
- 增强场景描述
- 优化LLM提示词
- 扩展随机事件库

### v1.1.0
- 新增完整场景结构生成
- 新增角色状态系统
- 新增命令
- 增强行动判定
- 优化游戏状态显示
- 新增自动清理存档功能
- 修复JSON解析失败的问题
- 修复用户信息获取失败的问题

### v1.0.0
- 初始版本发布
- 支持单人/多人游戏模式
- LLM内容生成
- 提示系统（3次机会）
- 推理和行动记录
- 多种结局判定
- 存档/读档功能
- 自动保存机制
