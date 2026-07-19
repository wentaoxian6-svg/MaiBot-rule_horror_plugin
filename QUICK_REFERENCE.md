# 规则怪谈插件 - 快速参考

适用版本：`2.3.0`

## 命令速查

### 开局与大厅

| 命令 | 说明 |
|------|------|
| `/rg 开始 单人` | 开始单人游戏 |
| `/rg 开始 多人` | 创建多人大厅 |
| `/rg 开始 多人 开始` | 由房主在大厅人数到齐后正式开局 |
| `/rg 强制开始 单人` | 覆盖现有进度并强制开始单人局 |
| `/rg 强制开始 多人` | 覆盖现有进度并强制开始多人局 |
| `/rg 加入` | 加入当前多人大厅 |
| `/rg 离开` | 离开当前多人游戏 |

### 游戏信息

| 命令 | 说明 |
|------|------|
| `/rg 状态` | 查看当前状态 |
| `/rg 剧情` | 重新发送剧情导入内容 |
| `/rg 规则` | 查看自己记录下来的规则笔记 |
| `/rg 场景` | 查看场景整体印象与当前所在地 |
| `/rg 区域` | 以列表查看场景所有可确认区域 |
| `/rg 身份` | 查看多人模式身份信息 |
| `/rg 道具` | 查看当前道具列表 |
| `/rg 道具 <名称>` | 查看指定道具详情 |
| `/rg 线索` | 查看当前已知线索 |
| `/rg 线索 <名称>` | 查看指定线索详情 |

当前状态页会额外显示：

- 玩家当前位置
- 疲劳等级
- 每位玩家当前记录的规则笔记数量

### 游戏操作

| 命令 | 说明 |
|------|------|
| `/rg 行动 <内容>` | 推进行动，推荐主入口 |
| `/rg 推理 <内容>` | 记录推理内容 |
| `/rg 记录规则 <内容>` | 记录推理出的规则 |
| `/rg 提示 规则` | 获取规则提示 |
| `/rg 提示 线索` | 获取线索提示 |
| `/rg 继续` | 达成目标后继续探索，冲击完美结局 |
| `/rg 结束` | 结束游戏并判定结局 |
| `/rg 帮助` | 查看帮助信息 |

### 存档管理

| 命令 | 说明 |
|------|------|
| `/rg 保存 <名称>` | 手动保存 |
| `/rg 读取 <名称>` | 读取命名存档 |
| `/rg 恢复` | 恢复默认存档 |
| `/rg 存档列表` | 查看当前群组/用户可用存档 |
| `/rg 清理存档` | 清理已结束存档与过期图片缓存 |

## 常见行动

```text
/rg 行动 检查房间角落
/rg 行动 打开柜子
/rg 行动 去走廊
/rg 行动 喝水
/rg 行动 吃面包
/rg 行动 休息
/rg 行动 休息30分钟
```

## 多人身份说明

当前实现以代码行为为准：

- 开局后会优先私聊发送身份、任务、责任区域、开场观察和独有信息
- 只有 `rule_carrier` 形式的开局才会继续发送规则内容
- `natural_language` 形式的开局不会自动发送规则，只保留剧情导入与后续玩家自行推理
- 不再直接私聊发送个人规则或共同规则正文
- `/rg 规则` 只查看玩家自己的规则笔记；规则主要通过探索、规则载体和 NPC 行为获得
- 不同身份、不同任务区域或共享可见组的玩家，开场能看到的载体和线索可能不同
- 执行 `/rg 身份` 时也会优先私聊
- 私聊失败时，群内只会提示补救方式，不会发送该玩家的身份正文

如果你在意身份保密性，建议在开局前确认机器人与所有玩家的私聊链路可用；若有人未收到私聊，可在修复私聊权限后重新使用 `/rg 身份` 获取。

## 配置速查

### 生效配置段

当前版本实际生效的配置段与结构为：

- `[plugin]`
- `[llm]`
- `[[llm.models]]`
- `[npc_sim]`
- `[[npc_sim.models]]`
- `[save]`

不要再添加独立的 `[environment]` 配置块。当前版本没有单独加载它。

补充说明：

- 主流程默认走 `[llm]`
- NPC 行动与位置同步优先走 `[npc_sim]`
- `[npc_sim]` 未单独配模型时会回退到主 `[llm]`

### 最小示例

```toml
[plugin]
enabled = true
config_version = "2.3.0"
auto_save_interval = 30
font_path = ""

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
max_tokens = 4000
max_retries = 3
timeout = 180
default_headers = {}
default_body = {}

[save]
batch_save_interval = 30
max_auto_saves = 10
compress_saves = true
```

### 环境变量

| 变量名 | 用途 |
|------|------|
| `LLM_API_KEY` | 当 `llm.api_key` 或模型级 `api_key` 为空时尝试读取 |
| `RULE_HORROR_FONT` | 覆盖 `plugin.font_path` 指定的字体路径 |

### Linux 速查

- 推荐安装 `Noto Sans CJK` 或 `文泉驿微米黑`
- 插件目录只读时，数据目录会依次尝试：
  - `${XDG_DATA_HOME}/maibot/rule_horror/`
  - `~/.local/share/maibot/rule_horror/`
  - 系统临时目录下的 `maibot/rule_horror/`
- 如果长图中文显示异常，优先检查 `RULE_HORROR_FONT` 或 `plugin.font_path`

## LLM 能力速查

主 `LLMClient` 当前已经实现：

- Chat Completions 风格接口调用
- 优先使用 `[[llm.models]]`，未配置时回退到 `model_list`
- 支持按配置段调用主流程模型与 `npc_sim` 模型
- 多种 `message.content` 返回格式兼容
- JSON 内容清洗与解析修复
- 合并 `default_body + extra_body` 并兼容 DeepSeek 思考参数
- 按模型级或全局 `timeout` 设置请求超时

主 `LLMClient` 当前没有落地为通用能力的部分：

- 连接池复用
- `Semaphore` 并发限流
- 按 `max_retries` 做统一自动重试

NPC 模拟系统会优先读取 `[npc_sim]`，未单独配置模型时回退主 `[llm]`。环境演化系统仍复用主 LLM 配置参与运行，但不是通过独立的 `[environment]` 段配置。

## 机制速查

### 理智与展示

| 理智值 | 表现 |
|------|------|
| `> 70` | 描述较清晰、偏理性 |
| `40-70` | 开始出现不安和混乱感 |
| `< 40` | 描述明显变得混乱、恐怖 |
| `= 0` | 进入理智崩坏展示 |

### 疲劳值与疲劳等级

| 疲劳值 | 疲劳等级 |
|------|------|
| `0-19` | 无 |
| `20-39` | 轻微 |
| `40-59` | 中度 |
| `60-79` | 严重 |
| `80-100` | 极度 |

状态页当前统一展示疲劳等级；新存档优先使用独立 `fatigue` 疲劳值，旧存档缺失该字段时才会临时按体力兜底推导。

### 休息

| 行动 | 效果 |
|------|------|
| `/rg 行动 休息` | 默认休息 15 分钟，恢复体力并降低疲劳 |
| `/rg 行动 休息30分钟` | 恢复更多体力 |
| `/rg 行动 休息一小时` | 恢复更多体力并推进更多时间 |

### 道具

| 行动 | 常见效果 |
|------|------|
| `/rg 行动 喝水` | 降低压力、焦虑 |
| `/rg 行动 吃面包` | 恢复体力 |
| 发现关键物品 | 可能触发规则变异或理智变化 |

## 流程速查

### 单人模式

1. `/rg 开始 单人`
2. 阅读剧情导入、身份引导和场景信息
3. 用 `/rg 行动 <内容>` 推进
4. 用 `/rg 记录规则 <内容>` 整理规则笔记，再结合 `/rg 规则`、`/rg 场景`、`/rg 区域`、`/rg 线索` 复盘信息
5. 达成目标后 `/rg 继续` 或 `/rg 结束`

### 多人模式

1. 房主执行 `/rg 开始 多人`
2. 其他玩家执行 `/rg 加入`
3. 房主执行 `/rg 开始 多人 开始`
4. 系统优先私聊下发身份任务卡
5. 各玩家分别探索、观察 NPC 行为、记录规则并交换信息
6. 达成目标后 `/rg 继续` 或 `/rg 结束`

## 架构速查

当前命令层已经按职责拆开：

- `plugin.py`：插件入口与命令装配
- `flows/singleplayer_flow.py`：单人流程
- `flows/multiplayer_flow.py`：多人流程
- `commands/handler.py`：薄命令分发层
- `commands/runtime_support.py`：共享运行时辅助
- `commands/shared_handlers.py`：共享命令处理

## 故障排除

| 问题 | 快速处理 |
|------|---------|
| 游戏无法开始 | 检查 `plugin.enabled` 和 LLM 配置 |
| LLM 调用失败 | 检查 `api_url`、`api_key`、模型是否可用 |
| 图片乱码 | 设置 `RULE_HORROR_FONT` 或 `plugin.font_path` |
| 私聊身份没收到 | 检查私聊链路并修复私聊权限；当前实现不会回退到群聊，可重新使用 `/rg 身份` 获取 |
| 手动存档失败 | 检查存档名是否包含非法字符 |

## 文档链接

- [README.md](README.md): 用户说明
- [TECHNICAL.md](TECHNICAL.md): 技术结构与实现事实
- [TESTING_GUIDE.md](TESTING_GUIDE.md): 测试与验收指南
