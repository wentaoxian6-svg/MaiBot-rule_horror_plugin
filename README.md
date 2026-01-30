# 规则怪谈插件 (Rule Horror Plugin)

一个为 MaiBot 设计的规则怪谈互动游戏插件，支持 LLM 生成内容、单人/多人游戏模式、提示系统和多种结局判定。

- 版本：`2.1.0`
- 指令前缀：`/rg`

## 功能演示

![游戏演示](data/temp_images/演示图片.png)

## 游玩建议

1. 在进入规则怪谈之前，将麦麦的思考频率调至0，并关闭提及必回复。
2. 插件配置的模型温度保持在0.7以上、1.0以下。
3. 推荐使用智商较高、使用成本较低的模型游玩（如deepseek-v3.2）。
4. 如果你不缺钱，只需要使用智商高的模型即可。
5. 如果启用环境演化系统（默认开启，不建议关闭，关闭可能导致其他问题），则需要使用支持结构化输出的模型（如官网的glm）。
6. 启用环境演化系统后，建议使用gemini-3(flash或者pro)等支持结构化输出的长上下文高智商模型。
7. 开始生成内容时耗时较长，可能需要等待几分钟才能生成完毕。

## 安装说明

### 环境要求

- **Python 3.10+**，推荐 Python 3.12+
- MaiBot 框架
- LLM API 服务，需要支持 OpenAI Chat Completions 风格

### 安装步骤

1. 将插件文件夹复制到 MaiBot 的 `plugins` 目录
2. 安装依赖项：
   ```
   pip install -r requirements.txt
   ```
3. 配置 LLM API，见下方配置说明
4. 重启 MaiBot 以加载插件

！！！一键包用户使用控制台"交互式安装pip模块"安装依赖！！！

## 配置说明

配置文件为插件目录下的 `config.toml`。

### LLM 配置

你至少需要配置以下几项：
- `llm.api_url`
- `llm.api_key`
- `llm.model_list`

示例：

```toml
[llm]
api_url = "https://example.com/v1/chat/completions"
api_key = "YOUR_API_KEY"
model_list = ["gemini-3-flash-preview"]
temperature = 0.8
max_concurrent = 10
max_tokens = 8000
```

### 插件配置

```toml
[plugin]
enabled = true
config_version = "2.1.0"
auto_save_interval = 30

# 是否允许直接发送自然语言触发行动
enable_natural_language_action = false

# 图片渲染字体文件路径
font_path = ""
```

### 存档配置

```toml
[save]
batch_save_interval = 30
```

### 环境变量

- `LLM_API_KEY`：当 `llm.api_key` 未配置或为 `YOUR_API_KEY` 时会尝试读取
- `RULE_HORROR_FONT`：覆盖字体路径，优先级高于 `plugin.font_path`

请不要把真实 `api_key` 提交到仓库或发到群里。

## 使用指南

### 两种行动方式

插件支持两种行动方式。

#### 方式1：命令格式

```
/rg 行动 检查房间的角落
/rg 行动 喝水
/rg 行动 休息30分钟
```

#### 方式2：自然语言输入

```
检查房间的角落
喝水
休息30分钟
```

自然语言输入默认关闭，需要在 `config.toml` 中设置 `plugin.enable_natural_language_action = true`。

沉浸式结束：在游戏进行中，你可以直接发送 `结束` 或 `结束游戏` 来结束并判定结局。

### 规则展示说明

- `/rg 规则` 展示的是玩家当前已获得的规则信息，可能为空或不完整
- 开局 NPC 引导可能是口述规矩，也可能是发放书面守则
- 通过探索与询问 NPC，可能逐步获得更多规则信息

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
/rg 提示 规则
/rg 提示 线索
/rg 推理 <推理内容>
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

## 故障排除

### LLM API 调用失败

- 检查 `llm.api_url` 和 `llm.api_key` 是否正确
- 确认模型与网关能稳定返回 JSON
- 查看 MaiBot 日志获取详细错误信息

### 图片中文字是方块或乱码

- Linux 下请安装中文字体
- 设置 `RULE_HORROR_FONT` 或在 `plugin.font_path` 填写字体文件路径

### 无法写入存档或图片

- Linux 或容器环境可能存在只读目录
- 插件会自动回退到用户数据目录，可在日志中看到回退提示

## 更多信息

- **快速参考**：查看 [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- **技术文档**：查看 [TECHNICAL.md](TECHNICAL.md)
- **测试指南**：查看 [TESTING_GUIDE.md](TESTING_GUIDE.md)

## 许可证

本插件遵循 MIT 许可证。

## 联系方式

作者：岚影鸿夜（QQ：1485272140）
