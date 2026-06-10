# 规则怪谈插件 (Rule Horror Plugin)

为 MaiBot 设计的规则怪谈互动游戏插件，支持单人/多人游玩、LLM 生成剧情与行动结果、规则笔记、提示系统、推理记录、存档管理和多结局判定。

- 版本：`2.3.0`
- 指令前缀：`/rg`

## 功能演示

![游戏演示](data/temp_images/演示图片.png)

## 游玩建议

1. 在进入规则怪谈之前，将麦麦的聊天频率调至0，并关闭提及必回复。
2. 插件配置的模型温度保持在0.7至1.5。
3. 推荐使用智商较高、支持结构化输出的模型游玩。（如果模型无法输出结构化内容，可能会玩到一半报错）
4. 开始生成内容时耗时较长，可能需要等待几分钟才能生成完毕。
5. 我主流程用deepseek-v4-pro玩，效果非常好，强推！NPC模拟的模型建议用长上下文、支持输出较长文本的模型。
6. **提醒**：多人模式中，每个参与玩家的身份任务卡和独有信息通过私聊发送，不再直接下发完整规则正文；规则主要通过场景探索、规则载体、NPC 行为和玩家推理逐步获得。需要确保每个参与玩家都有麦麦的好友，否则将接收不到自己的身份任务卡。使用 `/rg 身份` 指令也仍然通过私聊发送；如果私聊失败，群内只会提示补救方式，不会回退发送身份正文。`/rg 规则` 查看的是你自己整理的规则笔记。

## 安装说明

### 环境要求

- Python 3.12+
- MaiBot 框架
- `maibot_sdk` 新版插件运行环境
- LLM API 服务，需要支持 OpenAI Chat Completions 风格

### 安装步骤

1. 将插件文件夹复制到 MaiBot 的 `plugins` 目录
2. 安装依赖项：
   ```
   uv pip install -r requirements.txt
   ```
3. 配置 LLM API，见下方配置说明
4. 重启 MaiBot 以加载插件

### Linux 部署建议

- 建议提前安装中文字体，否则长图中的中文可能显示为方块
- 推荐字体包：
  - Debian/Ubuntu: `fonts-noto-cjk` 或 `fonts-wqy-microhei`
  - CentOS/RHEL: `google-noto-sans-cjk-ttc-fonts` 或文泉驿相关字体包
- 如果系统字体位置比较特殊，建议显式配置 `RULE_HORROR_FONT` 或 `plugin.font_path`
- 如果插件目录是只读挂载，插件会优先回退到 `XDG_DATA_HOME`、`~/.local/share/maibot/rule_horror/`，再不行会回退到系统临时目录

！！！一键包用户不要手动安装依赖！！！

## 配置说明

配置文件为插件目录下的 `config.toml`。

### 当前配置段

当前版本实际生效的配置结构为：

- `[plugin]`
- `[llm]`
- `[[llm.models]]`
- `[npc_sim]`
- `[[npc_sim.models]]`
- `[save]`

其中：

- 主流程剧情、行动判定等默认使用 `[llm]`
- NPC 行动与位置同步优先使用 `[npc_sim]`
- 如果 `[npc_sim]` 没有单独配置可用模型，会自动回退主 `[llm]`

### LLM 配置

当前版本支持两种 LLM 配置方式：

- 简化模式：直接配置 `llm.model_list`
- 完整模式：使用 `[[llm.models]]` 配置多个模型并按顺序故障转移

当两者同时存在时，优先使用 `[[llm.models]]`。

示例：

```toml
[llm]
api_url = "https://api.deepseek.com/chat/completions"
api_key = ""
model_list = []
temperature = 1.0
max_concurrent = 10
max_tokens = 8000
max_retries = 3
timeout = 180
default_headers = {}
default_body = {}

[[llm.models]]
name = "deepseek-v4-pro"
enabled = true
api_url = "https://api.deepseek.com/chat/completions"
api_key = ""
temperature = 1.0
max_tokens = 8000
timeout = 180
headers = {}
extra_body = { thinking = { type = "enabled" }, reasoning_effort = "high" }
```

### NPC 模拟配置

NPC 行动与位置同步使用独立的 `[npc_sim]` 配置段。未单独配置模型时，会回退主 `[llm]`。

```toml
[npc_sim]
enabled = true
trigger_on_every_action = true
room_hearing_radius = 1
max_event_history = 20
api_url = ""
api_key = ""
model_list = []
temperature = 0.7
max_concurrent = 10
max_tokens = 8000
max_retries = 3
timeout = 180
default_headers = {}
default_body = {}

[[npc_sim.models]]
name = "deepseek-v4-flash"
enabled = false
api_url = "https://api.deepseek.com/chat/completions"
api_key = ""
temperature = 0.7
max_tokens = 8000
timeout = 180
headers = {}
extra_body = {}
```

### 插件配置

```toml
[plugin]
enabled = true
config_version = "2.3.0"
auto_save_interval = 30

# 图片渲染字体文件路径
font_path = ""
```

### 存档配置

```toml
[save]
batch_save_interval = 30
max_auto_saves = 10
compress_saves = true
```

### 环境变量

- `LLM_API_KEY`：当 `llm.api_key` 或模型级 `api_key` 未配置时会尝试读取
- `RULE_HORROR_FONT`：覆盖字体路径，优先级高于 `plugin.font_path`

请不要把真实 `api_key` 提交到仓库或发到群里。

## 使用指南

### 行动方式

使用命令格式进行行动：

```
/rg 行动 检查房间的角落
/rg 行动 喝水
/rg 行动 休息30分钟
```

### 规则展示说明

- `/rg 规则` 展示的是玩家自己记录下来的规则笔记
- `规则载体` 形式的开局会把对应玩家可见的载体规则独立发送出来，并把展示出的载体规则同步写入规则笔记
- `自然语言` 形式的 NPC 引导不再自动提取规则，也不会触发开场规则发送；玩家需要自行推理并使用 `/rg 记录规则 <内容>` 记录
- 通过探索、询问 NPC、比对现场和整理线索，玩家可以逐步完善自己的规则笔记
- 多人模式下，不同身份、不同任务区域、不同可见组的玩家，看到的规则载体和初始观察可能不同
- 开场不保证直接给出规则；有时只能拿到身份任务、责任区域与少量观察，再通过探索和观察 NPC 行为自行推理

### NPC 与位置感知

- 单人和多人都使用同一套房间级 NPC 运行时
- 同房间 NPC 会被直接看见，相邻房间的动静可被听见，距离更远则默认无法感知
- 玩家每次行动后，NPC 会根据各自的行为逻辑推进下一步位置与动作
- 规则载体也会按房间、身份和可见组约束暴露，不会默认同步给所有人

### 规则类型与违规后果

**规则分类**：
- **即死规则**：触犯立即导致死亡（极少出现）
- **有害规则**：触犯会受到惩罚（NPC态度恶化、环境恶化、被追杀等）
- **双刃剑规则**：触犯有风险但能获得关键线索或NPC帮助
- **普通规则**：后果由剧情决定

**矛盾规则对**（部分场景）：
- 某些场景存在对抗势力（如A vs B）
- 两条规则直接矛盾，无法同时遵守
- 遵守/触犯不同规则会影响对应NPC的态度
- 有时需要"故意违规"来探索真相

**违规后果**：
- **即时后果**：理智/体力下降、NPC态度变化
- **延迟后果**：部分异常几分钟后才完全显现
- **追杀事件**：NPC敌意度足够高时概率触发
- **双刃剑收益**：获得线索、物品、NPC帮助

## 基本命令

### 开始游戏

```
/rg 开始 单人
/rg 开始 多人
/rg 开始 多人 开始
```

多人模式下，`/rg 开始 多人` 会创建大厅，其他玩家使用 `/rg 加入` 加入，房主使用 `/rg 开始 多人 开始` 生成开局并开始游戏。

### 强制开始

```
/rg 强制开始 单人
/rg 强制开始 多人
```

### 恢复存档

```
/rg 恢复
```

### 存档管理

```
/rg 保存 <存档名称>
/rg 读取 <存档名称>
/rg 存档列表
/rg 清理存档
```

### 加入与离开

```
/rg 加入
/rg 离开
```

### 查看信息

```
/rg 状态
/rg 剧情
/rg 规则
/rg 场景
/rg 道具
/rg 道具 <道具名称>
/rg 物品栏
/rg 背包
```

### 进行游戏

```
/rg 身份
/rg 线索
/rg 线索 <线索名称>
/rg 提示 规则
/rg 提示 线索
/rg 推理 <推理内容>
/rg 记录规则 <规则内容>
/rg 行动 <行动描述>
/rg 继续
/rg 结束
/rg 帮助
```

## 存档与数据目录

### 数据目录位置

- 默认使用插件目录下的 `data/`
- 如果插件目录不可写，会回退到用户数据目录
  - `${XDG_DATA_HOME}/maibot/rule_horror/`
  - `~/.local/share/maibot/rule_horror/`

### 存档文件

- 默认存档：`data/saves/save_<group_id>.json` 或 `data/saves/save_<group_id>.json.gz`
- 手动存档：`data/saves/named_<group_id>_<name>.json`

存档名与 group_id 会做安全过滤，只保留字母数字与 `- _`。

### 图片缓存

- 临时图片目录：`data/temp_images/`
- 缓存目录：`data/temp_images/cache/`
- `/rg 清理存档` 会清理已结束存档与过期图片缓存
- Linux/容器下如果插件目录只读，以上目录会一起回退到可写数据目录

## 当前实现结构

当前版本已经把命令与流程层做了分层，便于后续继续维护：

- `plugin.py`：只保留插件入口、生命周期、配置加载和 `/rg` 命令装配
- `plugin_config.py`：插件配置模型
- `flows/singleplayer_flow.py`：单人开局与单人流程编排
- `flows/multiplayer_flow.py`：多人大厅、加入、身份、多人开局流程编排
- `commands/handler.py`：薄命令装配层，负责开始/强制开始/身份等分发
- `commands/runtime_support.py`：规则载体、NPC 运行时、图片发送、规则笔记等运行时辅助能力
- `commands/shared_handlers.py`：状态、规则、提示、行动、存档等共享命令处理
- `commands/router.py`：命令名到处理方法的路由表

如果后续继续调整玩法或命令逻辑，优先在 `flows/` 或 `commands/` 下修改，而不是重新把大量逻辑塞回 `plugin.py`。

## 故障排除

### LLM API 调用失败

- 检查 `llm.api_url` 和 `llm.api_key` 是否正确
- 如果使用 `[[llm.models]]`，检查模型级 `api_url`、`api_key`、`enabled`
- 确认模型与网关能稳定返回 JSON
- 查看 MaiBot 日志获取详细错误信息

### 图片中文字是方块或乱码

- Linux 下请安装中文字体
- 优先推荐 `Noto Sans CJK` 或 `文泉驿微米黑`
- 设置 `RULE_HORROR_FONT` 或在 `plugin.font_path` 填写字体文件路径

### 无法写入存档或图片

- Linux 或容器环境可能存在只读目录
- 插件会自动回退到用户数据目录；若 `HOME` / `XDG_DATA_HOME` 也不可用，会继续回退到系统临时目录
- 可在日志中看到最终使用的数据目录与字体探测结果

## 游戏机制详解

### 状态系统

游戏中会实时显示以下状态：

**身体状态**：
- 体力值 (0-100)：受伤会减少，休息会恢复
- 疲劳等级：无/轻微/中度/严重/极度
- 受伤情况：无伤/轻伤/重伤/致命伤

**精神状态**：
- 理智值 (0-100)：违反规则会减少，使用关键物品会恢复
- 精神状态：正常/紧张/恐惧/崩溃/疯狂
- 情绪：平静/焦虑/绝望/愤怒等（根据游戏进程动态变化）

**心理状态**：
- 恐惧等级 (0-100)
- 焦虑等级 (0-100)
- 压力等级 (0-100)

### 理智值影响

理智值影响场景描述风格和视觉效果：
- **>70**：描述客观清晰
- **40-70**：开始出现混乱和恐惧
- **<40**：描述混乱、充满幻觉
- **=0**：理智崩坏模式

## 更多信息

- **快速参考**：查看 [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- **技术文档**：查看 [TECHNICAL.md](TECHNICAL.md)
- **测试指南**：查看 [TESTING_GUIDE.md](TESTING_GUIDE.md)

## 许可证

本插件遵循 MIT 许可证。

## 联系方式

作者：岚影鸿夜（QQ：1485272140）
