"""
常量定义 - 避免魔法字符串和硬编码值
"""
from enum import Enum


class GameCommands(str, Enum):
    """游戏命令常量"""
    START = "开始"
    FORCE_START = "强制开始"
    RESTORE = "恢复"
    SAVE = "保存"
    LOAD = "读取"
    SAVE_LIST = "存档列表"
    CLEAN_SAVES = "清理存档"
    JOIN = "加入"
    LEAVE = "离开"
    STATUS = "状态"
    PLOT = "剧情"
    RULES = "规则"
    SCENE = "场景"
    ITEMS = "道具"
    HINT = "提示"
    REASON = "推理"
    ACTION = "行动"
    CONTINUE = "继续"
    END = "结束"
    HELP = "帮助"
    IDENTITY = "身份"  # 查看自己的身份信息（多人模式）
    
    # 自然语言结束命令
    END_GAME = "结束游戏"


class GameModes(str, Enum):
    """游戏模式"""
    SINGLE = "单人"
    MULTI = "多人"


class TimePhases(str, Enum):
    """时间阶段"""
    MIDNIGHT = "深夜"
    DAWN = "凌晨"
    EARLY_MORNING = "黎明"
    MORNING = "清晨"
    DAYTIME = "白昼"


class TimeThresholds:
    """时间阈值（分钟）"""
    MIDNIGHT = 60
    DAWN = 180
    EARLY_MORNING = 300
    MORNING = 420


class FatigueLevel(str, Enum):
    """疲劳等级"""
    NONE = "无"
    SLIGHT = "轻微"
    MODERATE = "中度"
    SEVERE = "严重"
    EXTREME = "极度"


class FatigueMultipliers:
    """疲劳时间倍率"""
    NONE = 1.0
    SLIGHT = 1.2
    MODERATE = 1.5
    SEVERE = 2.0
    EXTREME = 3.0


class SanityThresholds:
    """理智值阈值"""
    HIGH = 70
    MEDIUM = 40
    LOW = 0
    MAX = 100


class HealthThresholds:
    """生命值阈值"""
    MAX = 100
    MIN = 0


class EntropyThresholds:
    """熵值阈值"""
    STABLE = 20
    ABNORMAL = 40
    DETERIORATING = 60
    DANGEROUS = 80
    COLLAPSING = 100


class PerceptionLevels(str, Enum):
    """感知层级（多人模式）"""
    ACTOR = "行动者"
    WITNESS = "目击者"
    DISTANT = "远处感知"


class ActionKeywords:
    """行动关键词列表"""
    KEYWORDS = [
        "拿", "取", "放", "扔", "用", "打开", "关闭", "检查", "询问",
        "进入", "离开", "触摸", "推", "拉", "按", "转", "看", "听",
        "等待", "躲藏", "逃跑", "攻击", "交谈", "观察", "搜索", "移动",
        "前往", "返回", "调查", "寻找", "翻找", "使用", "吃", "探索",
        "喝", "睡", "休息", "歇息", "坐", "站", "走", "跑", "爬",
        "先", "然后", "接着", "再", "去", "来", "到", "在", "找"
    ]


class ConfigDefaults:
    """配置默认值"""
    AUTO_SAVE_INTERVAL = 30
    BATCH_SAVE_INTERVAL = 30
    MAX_CONCURRENT_REQUESTS = 10
    TEMPERATURE = 0.8
    ENABLE_NATURAL_LANGUAGE = False


class DirectoryNames:
    """目录名称"""
    DATA = "data"
    TEMP_IMAGES = "temp_images"
    SAVES = "saves"
    LOGS = "logs"


class FileNames:
    """文件名称"""
    CONFIG = "config.toml"
    WRITE_TEST = ".write_test"


class ErrorMessages:
    """错误消息"""
    PLUGIN_DISABLED = "规则怪谈插件已被禁用。"
    UNKNOWN_COMMAND = "未知命令。请使用 `/rg 帮助` 查看可用命令。"
    NOT_IN_GAME = "你还没有加入游戏。"
    ALREADY_IN_GAME = "你已经在游戏中了。"
    GAME_NOT_STARTED = "游戏还未开始。"
    CONFIG_LOAD_FAILED = "配置文件加载失败"
    SAVE_FAILED = "保存失败"
    LOAD_FAILED = "读取失败"


class SuccessMessages:
    """成功消息"""
    CONFIG_LOADED = "配置文件加载成功"
    PLUGIN_LOADED = "规则怪谈插件已加载"
    PLUGIN_UNLOADED = "规则怪谈插件已卸载"
    GAME_STARTED = "游戏已开始"
    SAVE_SUCCESS = "保存成功"
    LOAD_SUCCESS = "读取成功"


class TimeDescriptions:
    """时间描述"""
    MIDNIGHT = "午夜时分，周围一片死寂"
    DAWN = "黎明前的黑暗，空气中弥漫着不安"
    EARLY_MORNING = "东方泛起鱼肚白，但黑暗仍未完全消散"
    MORNING = "晨光熹微，雾气缭绕"
    DAYTIME = "阳光透过窗户，但依然阴冷"


class EntropyDescriptions:
    """熵值描述"""
    STABLE = "环境相对稳定"
    ABNORMAL = "环境开始出现异常"
    DETERIORATING = "环境明显恶化"
    DANGEROUS = "环境极度危险"
    COLLAPSING = "环境即将崩溃"


class TimePressureLevels(str, Enum):
    """时间压力等级"""
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    CRITICAL = "极高"
