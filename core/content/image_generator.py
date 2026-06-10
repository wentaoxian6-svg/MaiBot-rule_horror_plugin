"""异步图片生成器 - 使用线程池处理 CPU 密集型操作，支持图片缓存"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import random
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import TypeAlias, cast

from PIL import Image, ImageDraw, ImageFont

from ...common.models import JsonValue, RuleDict

logger = logging.getLogger(__name__)

Font: TypeAlias = ImageFont.FreeTypeFont | ImageFont.ImageFont
CoreSymbol: TypeAlias = str | Mapping[str, str]


class AsyncImageGenerator:
    """异步图片生成器（带缓存功能）"""

    # 全局字体路径缓存：避免每次创建生成器都做一轮“探测字体”
    _global_font_path: str | None = None

    def __init__(self, output_dir: str, max_workers: int = 4):
        self.output_dir: Path = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=max_workers)
        self._font_cache: dict[tuple[str, int], Font] = {}

        # 图片缓存目录
        self.cache_dir: Path = self.output_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 缓存索引文件
        self.cache_index_file: Path = self.cache_dir / "cache_index.json"
        self._cache_index: dict[str, str] = self._load_cache_index()

        # 选择一个跨平台可用的字体（优先配置/环境变量，其次自动探测）
        if AsyncImageGenerator._global_font_path is None:
            AsyncImageGenerator._global_font_path = self._resolve_font_path()
        self._font_path: str = AsyncImageGenerator._global_font_path or "msyh.ttc"

    def _load_cache_index(self) -> dict[str, str]:
        """加载缓存索引"""
        if self.cache_index_file.exists():
            try:
                with open(self.cache_index_file, "r", encoding="utf-8") as f:
                    raw = cast(object, json.load(f))
                if not isinstance(raw, dict):
                    return {}
                raw_dict = cast(dict[object, object], raw)
                out: dict[str, str] = {}
                for k, v in raw_dict.items():
                    if isinstance(k, str) and isinstance(v, str):
                        out[k] = v
                return out
            except Exception as e:
                logger.warning(f"加载缓存索引失败: {e}")
        return {}

    def _save_cache_index(self) -> None:
        """保存缓存索引"""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=".cache_index_",
                suffix=".tmp",
                dir=str(self.cache_dir),
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._cache_index, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                temp_path.replace(self.cache_index_file)
            except Exception:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
        except Exception as e:
            logger.error(f"保存缓存索引失败: {e}")

    def _resolve_font_path(self) -> str:
        """解析一个可用字体路径（跨平台）。

        优先级：
        1) 环境变量 `RULE_HORROR_FONT`
        2) 配置 `plugin.font_path`
        3) 常见 Windows/Linux 字体路径

        返回空字符串表示未找到（后续会回退到 Pillow 默认字体）。
        """
        candidates: list[str] = []

        # 1) 环境变量覆盖
        env_font = (os.getenv("RULE_HORROR_FONT") or "").strip()
        if env_font:
            candidates.append(env_font)

        # 2) 配置覆盖（尽量不引入硬依赖：失败就跳过）
        try:
            from ..config import get_config  # 延迟导入，避免循环依赖

            cfg = get_config()
            cfg_font = (getattr(getattr(cfg, "plugin", None), "font_path", "") or "").strip()
            if cfg_font:
                candidates.append(cfg_font)
        except Exception:
            pass

        # 3) 插件内可能的字体位置（若未来你决定随仓库附带字体文件，可以放这里）
        plugin_root = Path(__file__).resolve().parents[2]
        candidates.extend([
            str(plugin_root / "data" / "fonts" / "NotoSansCJK-Regular.ttc"),
            str(plugin_root / "data" / "fonts" / "NotoSansCJKsc-Regular.otf"),
            str(plugin_root / "data" / "fonts" / "wqy-microhei.ttc"),
        ])

        # 4) Windows 常见字体
        candidates.extend([
            r"C:\\Windows\\Fonts\\msyh.ttc",
            r"C:\\Windows\\Fonts\\msyh.ttf",
            r"C:\\Windows\\Fonts\\simhei.ttf",
            r"C:\\Windows\\Fonts\\simsun.ttc",
        ])

        # 5) Linux 常见中文字体（发行版差异较大，尽量覆盖主流路径）
        candidates.extend([
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/arphic/ukai.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ])

        # 6) 兜底：相对文件名（Windows 上通常可用；Linux 可能不可用但尝试无害）
        candidates.extend([
            "NotoSansCJK-Regular.ttc",
            "NotoSansCJKsc-Regular.otf",
            "wqy-microhei.ttc",
            "wqy-zenhei.ttc",
            "msyh.ttc",
            "simhei.ttf",
            "simsun.ttc",
            "arial.ttf",
        ])

        # 去重且保持顺序
        seen: set[str] = set()
        uniq: list[str] = []
        for c in candidates:
            c = (c or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            uniq.append(c)

        # 尝试加载（以能否被 Pillow/FreeType 打开为准）
        for c in uniq:
            p = os.path.expanduser(c)
            # 绝对路径/相对路径都可尝试；不存在的路径直接跳过可以减少噪音
            if ("/" in p or "\\" in p) and not Path(p).exists():
                continue
            try:
                _ = ImageFont.truetype(p, 20)
                if sys.platform.startswith("linux"):
                    logger.info(f"[rule_horror] Linux 字体已选择: {p}")
                return p
            except Exception:
                continue

        logger.warning("[rule_horror] 未找到可用中文字体，将回退到 Pillow 默认字体（可能导致中文显示为方块）")
        return ""

    def _get_cache_key(self, **kwargs: object) -> str:
        """生成缓存键"""
        # 将参数转换为JSON字符串，然后计算MD5
        cache_data = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(cache_data.encode("utf-8")).hexdigest()

    def _get_cached_image(self, cache_key: str) -> str | None:
        """获取缓存的图片路径"""
        if cache_key in self._cache_index:
            cached_path = self._cache_index[cache_key]
            if Path(cached_path).exists():
                logger.info(f"使用缓存图片: {cached_path}")
                return cached_path
            else:
                # 缓存文件不存在，删除索引
                del self._cache_index[cache_key]
                self._save_cache_index()
        return None

    def _cache_image(self, cache_key: str, image_path: str) -> None:
        """缓存图片"""
        self._cache_index[cache_key] = image_path
        self._save_cache_index()

    async def close(self) -> None:
        """关闭线程池"""
        self._executor.shutdown(wait=True)

    def _get_font(self, font_name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """获取字体（带缓存）"""
        key = (font_name, size)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(font_name, size)
            except Exception:
                # 尝试备用字体
                for fallback in [
                    "NotoSansCJK-Regular.ttc",
                    "NotoSansCJKsc-Regular.otf",
                    "wqy-microhei.ttc",
                    "wqy-zenhei.ttc",
                    "simhei.ttf",
                    "simsun.ttc",
                    "arial.ttf",
                ]:
                    try:
                        self._font_cache[key] = ImageFont.truetype(fallback, size)
                        break
                    except Exception:
                        continue
                else:
                    self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

    def _distort_text(self, text: str, sanity: int) -> str:
        """对文本应用理智崩坏效果"""
        if sanity >= 30:
            return text
        
        # 理智值越低，文字扭曲越严重
        distortion_level = (30 - sanity) / 30.0
        
        # 随机替换字符
        if random.random() < distortion_level * 0.3:
            distorted = list(text)
            num_replacements = int(len(text) * distortion_level * 0.2)
            for _ in range(num_replacements):
                if distorted:
                    idx = random.randint(0, len(distorted) - 1)
                    distorted[idx] = random.choice(['█', '▓', '▒', '░', '?', '!', '#'])
            return ''.join(distorted)
        
        return text

    def _wrap_text(self, text: str, max_chars_per_line: int) -> list[str]:
        """将文本按指定字符数换行（向后兼容）
        
        Args:
            text: 要换行的文本
            max_chars_per_line: 每行最大字符数
        
        Returns:
            换行后的文本列表
        """
        if not text:
            return []
        
        lines: list[str] = []
        current_pos = 0
        text_len = len(text)
        
        while current_pos < text_len:
            # 取出一行的文本
            line_end = min(current_pos + max_chars_per_line, text_len)
            line = text[current_pos:line_end]
            
            # 如果不是最后一行，尝试在标点符号处断行
            if line_end < text_len:
                # 查找最后一个标点符号
                punctuation = ['。', '！', '？', '，', '、', '；', '：', '"', "'", '）', '】', '》']
                last_punct_pos = -1
                for i in range(len(line) - 1, max(0, len(line) - 10), -1):
                    if line[i] in punctuation:
                        last_punct_pos = i
                        break
                
                # 如果找到标点符号，在标点符号后断行
                if last_punct_pos > len(line) // 2:  # 只有在后半部分找到标点才断行
                    line = line[:last_punct_pos + 1]
                    line_end = current_pos + last_punct_pos + 1
            
            lines.append(line)
            current_pos = line_end
        
        return lines

    def _wrap_text_by_width(
        self,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        """将文本按像素宽度动态换行（尽量贴近分割线宽度，不按标点“提前断行”）

        设计目标：
        - 以最大宽度为主进行贪婪折行，避免因逗号/顿号等标点导致行尾提前截断、右侧留大块空白。
        - 尽量避免“行首标点”。
        """
        if not text:
            return []

        # 创建临时图片用于计算文本宽度
        temp_img = Image.new("RGB", (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)

        def text_width(s: str) -> int:
            bbox = temp_draw.textbbox((0, 0), s, font=font)
            return int(bbox[2] - bbox[0])

        # 常见中文标点（尽量不要出现在行首）
        leading_punct = set("，。！？、；：）》】）,.!?;:)\"' ")
        # 空白类：行首直接跳过
        leading_space = set([" ", "\t", "\n", "\r", "　"])  # 含全角空格

        lines: list[str] = []
        current_line = ""

        for ch in text:
            # 折行时如果遇到显式换行符，直接断行
            if ch in {"\n", "\r"}:
                if current_line:
                    lines.append(current_line)
                    current_line = ""
                continue

            # 行首不保留多余空白
            if not current_line and ch in leading_space:
                continue

            test_line = current_line + ch
            if current_line and text_width(test_line) > max_width:
                # 发生溢出：默认以“到达最大宽度”为准断行
                # 若溢出字符是标点，尝试把标点塞进上一行（避免行首标点）
                if ch in leading_punct and text_width(current_line + ch) <= max_width:
                    lines.append(current_line + ch)
                    current_line = ""
                else:
                    lines.append(current_line)
                    current_line = "" if ch in leading_space else ch
            else:
                current_line = test_line

        if current_line:
            lines.append(current_line)

        # 兜底：如果仍出现行首标点，尽量把标点移到上一行尾（不强行超宽）
        fixed: list[str] = []
        for line in lines:
            if fixed and line and line[0] in leading_punct and text_width(fixed[-1] + line[0]) <= max_width:
                fixed[-1] = fixed[-1] + line[0]
                line = line[1:]
            fixed.append(line)

        return [ln for ln in fixed if ln != ""]

    def _apply_sanity_distortion(
        self,
        img: Image.Image,
        draw: ImageDraw.ImageDraw,
        sanity: int,
        font_normal: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        """应用理智崩坏时的视觉扭曲效果"""
        _ = font_normal
        if sanity >= 30 or sanity == 0:
            return img, draw
        
        width, height = img.size
        
        # 计算理智崩坏程度
        if sanity > 20:
            insanity_level = (30 - sanity) / 30.0 * 0.33
        elif sanity > 10:
            insanity_level = (20 - sanity) / 10.0 * 0.33 + 0.33
        else:
            insanity_level = (10 - sanity) / 10.0 * 0.33 + 0.67
        
        # 效果1：红色涂鸦遮盖
        num_scribbles = int(1 + 3 * insanity_level)
        for _ in range(num_scribbles):
            x1 = random.randint(50, width - 50)
            y1 = random.randint(100, height - 100)
            scribble_width = int(50 + 100 * insanity_level)
            scribble_height = int(10 + 20 * insanity_level)
            x2 = x1 + scribble_width
            y2 = y1 + scribble_height
            alpha = int(50 + 100 * insanity_level)
            draw.rectangle([x1, y1, x2, y2], fill=(255, 0, 0, alpha))
        
        # 效果2：红色斜线遮盖
        num_lines = int(1 + 2 * insanity_level)
        for _ in range(num_lines):
            y = random.randint(150, height - 150)
            line_width = int(2 + 3 * insanity_level)
            draw.line([(50, y), (width - 50, y)], fill=(255, 0, 0), width=line_width)
        
        # 效果3：黑色涂抹效果
        num_black_scribbles = int(2 + 4 * insanity_level)
        for _ in range(num_black_scribbles):
            x1 = random.randint(50, width - 50)
            y1 = random.randint(100, height - 100)
            scribble_width = int(30 + 80 * insanity_level)
            scribble_height = int(5 + 15 * insanity_level)
            x2 = x1 + scribble_width
            y2 = y1 + scribble_height
            draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0))
        
        return img, draw


    # ==================== 剧情导入长图 ====================
    
    def _generate_scene_image_sync(
        self,
        scene_name: str,
        background: str,
        arrival_reason: str,
        core_symbols: Sequence[CoreSymbol] | None = None,
        output_path: str | None = None,
    ) -> str:
        """同步方法：生成剧情导入长图（纯黑背景+鲜红字体）"""
        _ = core_symbols  # 不显示核心象征符号
        
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"plot_{timestamp}.png")

        # 加载字体
        font_title = self._get_font(self._font_path, 36)
        font_subtitle = self._get_font(self._font_path, 28)
        font_normal = self._get_font(self._font_path, 20)

        # 预估图片高度
        margin = 60
        title_height = 80
        section_height = 50
        line_height = 30
        char_per_line = 38
        
        # 计算背景故事需要的行数
        bg_lines: list[str] = []
        for i in range(0, len(background), char_per_line):
            bg_lines.append(background[i:i+char_per_line])
        
        # 计算玩家身份需要的行数
        identity_lines: list[str] = []
        for i in range(0, len(arrival_reason), char_per_line):
            identity_lines.append(arrival_reason[i:i+char_per_line])
        
        # 计算总高度
        total_height = (margin * 2 + title_height + section_height + 
                       len(bg_lines) * line_height + section_height + 
                       len(identity_lines) * line_height + 50)
        
        # 创建图片（纯黑背景）
        width = 900
        img = Image.new('RGB', (width, total_height), color='#000000')
        draw = ImageDraw.Draw(img)
        
        # 绘制标题（动态居中）
        title_text = "规则怪谈"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title_text, fill='#8B0000', font=font_title)
        
        # 绘制场景名称（动态居中）
        scene_bbox = draw.textbbox((0, 0), scene_name, font=font_subtitle)
        scene_width = scene_bbox[2] - scene_bbox[0]
        scene_x = (width - scene_width) // 2
        draw.text((scene_x, margin + 50), scene_name, fill='#DC143C', font=font_subtitle)
        
        # 绘制分隔线
        draw.line([(margin, margin + 100), (width - margin, margin + 100)], fill='#8B0000', width=2)
        
        # 绘制背景故事
        current_y = margin + 130
        draw.text((margin, current_y), "剧情导入", fill='#DC143C', font=font_subtitle)
        current_y += section_height
        for line in bg_lines:
            draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        # 绘制玩家身份
        current_y += 20
        draw.text((margin, current_y), "你的身份", fill='#DC143C', font=font_subtitle)
        current_y += section_height
        for line in identity_lines:
            draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        # 保存图片
        img.save(output_path, 'PNG')
        logger.info(f"剧情导入长图已生成：{output_path}")
        
        return output_path

    async def generate_scene_image(
        self,
        scene_name: str,
        background: str,
        arrival_reason: str,
        core_symbols: Sequence[CoreSymbol] | None = None,
        output_path: str | None = None,
        use_cache: bool = True,
    ) -> str:
        """异步生成剧情导入长图（支持缓存）"""
        # 生成缓存键
        cache_key = self._get_cache_key(
            type="scene",
            scene_name=scene_name,
            background=background,
            arrival_reason=arrival_reason,
        )
        
        # 检查缓存
        if use_cache:
            cached_path = self._get_cached_image(cache_key)
            if cached_path:
                return cached_path
        
        # 生成新图片
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_scene_image_sync,
            scene_name=scene_name,
            background=background,
            arrival_reason=arrival_reason,
            core_symbols=core_symbols,
            output_path=output_path,
        )
        result_path = await loop.run_in_executor(self._executor, func)
        
        # 缓存图片
        if use_cache:
            self._cache_image(cache_key, result_path)
        
        return result_path

    # ==================== 规则长图 ====================
    
    def _generate_rules_image_sync(
        self,
        rules_title: str,
        rules: Sequence[RuleDict | Mapping[str, JsonValue] | str],
        win_condition: str,
        game_mode: str = "单人",
        output_path: str | None = None,
        sanity: int = 100,
    ) -> str:
        """同步方法：生成规则长图（纯黑背景+鲜红字体）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"rules_{timestamp}.png")

        # 加载字体
        font_title = self._get_font(self._font_path, 36)
        font_subtitle = self._get_font(self._font_path, 28)
        font_normal = self._get_font(self._font_path, 20)

        margin = 60
        title_height = 80
        section_height = 50
        line_height = 30
        width = 900
        
        # 计算分割线之间的可用宽度（与分割线等长）
        line_available_width = width - margin * 2

        # 理智崩坏模式（sanity=0）：只显示规则内容，不显示标题和标签
        is_insane_mode = (sanity == 0)

        # 渲染前去重：避免“同一句规则”因提取/合并/规则表重复而展示多次
        def _norm_rule_text(t: str) -> str:
            t = str(t or "")
            t = re.sub(r"\s+", "", t)
            # 去掉常见标点，便于判定“同一句话”
            t = re.sub(r"[，,。.!！？?；;:“”\"'‘’《》【】\[\]（）()\-—…·]", "", t)
            return t

        dedup_rules: list[RuleDict] = []
        seen: dict[str, int] = {}
        for r in rules or []:
            rr: RuleDict
            if isinstance(r, Mapping):
                rule_text_raw = str(r.get("text", r.get("content", "")) or "").strip()
                if not rule_text_raw:
                    rule_text_raw = str(r or "").strip()

                cur_idx_raw = r.get("original_index")
                cur_idx = cur_idx_raw if isinstance(cur_idx_raw, int) else None

                source_raw = r.get("source")
                source = source_raw if isinstance(source_raw, str) else ""

                rule_type_raw = r.get("rule_type")
                rule_type = str(rule_type_raw) if isinstance(rule_type_raw, str) else None

                related_npc_raw = r.get("related_npc")
                related_npc = str(related_npc_raw) if isinstance(related_npc_raw, str) else None

                opposing_npc_raw = r.get("opposing_npc")
                opposing_npc = str(opposing_npc_raw) if isinstance(opposing_npc_raw, str) else None

                rr = {
                    "text": rule_text_raw,
                    "original_index": cur_idx,
                    "source": source,
                    "rule_type": rule_type,
                    "related_npc": related_npc,
                    "opposing_npc": opposing_npc,
                }
            else:
                rule_text_raw = str(r or "").strip()
                rr = {
                    "text": rule_text_raw,
                    "original_index": None,
                    "source": "",
                    "rule_type": None,
                    "related_npc": None,
                    "opposing_npc": None,
                }

            if not rule_text_raw:
                continue

            key = _norm_rule_text(rule_text_raw)
            if not key:
                continue

            if key in seen:
                # 同文案：优先保留带 original_index 的那条（更利于排序/对齐）
                prev_i = seen[key]
                prev = dedup_rules[prev_i]
                prev_idx = prev.get("original_index")
                cur_idx = rr.get("original_index")
                if not isinstance(prev_idx, int) and isinstance(cur_idx, int):
                    dedup_rules[prev_i] = rr
                continue

            seen[key] = len(dedup_rules)
            dedup_rules.append(rr)

        rules = dedup_rules

        # 计算规则需要的行数（使用动态宽度换行）
        rule_lines: list[str] = []
        for i, rule in enumerate(rules, 1):
            rule_text = str(rule.get("text", rule.get("content", str(rule))) or "")
            rule_line = f"{i}. {rule_text}"
            # 使用动态宽度换行，与分割线等长
            wrapped_lines = self._wrap_text_by_width(rule_line, font_normal, line_available_width)
            rule_lines.extend(wrapped_lines)

        # 计算通关条件需要的行数（使用动态宽度换行）
        show_goal = bool(str(win_condition or "").strip())
        goal_lines: list[str] = []
        if show_goal:
            goal_prefix = "你的目标是：" if game_mode == "单人" else "你们的目标是："
            goal_lines = [goal_prefix]
            condition_lines = self._wrap_text_by_width(win_condition, font_subtitle, line_available_width)
            goal_lines.extend(condition_lines)
        
        # 计算总高度
        if is_insane_mode:
            visible_lines = rule_lines or goal_lines
            total_height = margin * 2 + len(visible_lines) * (line_height + 5) + 50
        else:
            total_height = margin * 2 + title_height + section_height + 50
            if rule_lines:
                total_height += len(rule_lines) * line_height
            if show_goal:
                if rule_lines:
                    total_height += section_height
                total_height += len(goal_lines) * line_height
        
        # 创建图片（纯黑背景）
        width = 900
        img = Image.new('RGB', (width, total_height), color='#000000')
        draw = ImageDraw.Draw(img)
        
        current_y = margin
        
        if is_insane_mode:
            # 理智崩坏模式：优先显示规则；无规则时显示目标文本
            visible_lines = rule_lines or goal_lines
            for line in visible_lines:
                # 移除序号前缀
                display_line = line
                for num in range(1, 11):
                    if line.startswith(f"{num}. "):
                        display_line = line.split(". ", 1)[1] if ". " in line else line
                        break
                draw.text((margin, current_y), display_line, fill='#8B0000', font=font_subtitle)
                current_y += line_height + 5
        else:
            # 正常模式
            # 绘制标题（动态居中）
            title_bbox = draw.textbbox((0, 0), rules_title, font=font_title)
            title_width = title_bbox[2] - title_bbox[0]
            title_x = (width - title_width) // 2
            
            # 对标题应用理智崩坏效果
            if sanity < 30:
                rules_title = self._distort_text(rules_title, sanity)
                offset_x = random.randint(-5, 5)
                offset_y = random.randint(-3, 3)
                draw.text((title_x + offset_x, margin + offset_y), rules_title, fill='#8B0000', font=font_title)
            else:
                draw.text((title_x, margin), rules_title, fill='#8B0000', font=font_title)
            
            # 绘制分隔线
            draw.line([(margin, margin + 80), (width - margin, margin + 80)], fill='#8B0000', width=2)
            
            current_y = margin + 110 + 15
            
            # 绘制规则列表
            for line in rule_lines:
                distorted_line = self._distort_text(line, sanity)
                
                if sanity < 30 and random.random() < 0.3:
                    offset_x = random.randint(-3, 3)
                    offset_y = random.randint(-2, 2)
                    draw.text((margin + offset_x, current_y + offset_y), distorted_line, fill='#FF0000', font=font_normal)
                else:
                    draw.text((margin, current_y), distorted_line, fill='#FF0000', font=font_normal)
                current_y += line_height
            
            # 绘制通关条件
            if show_goal:
                if rule_lines:
                    current_y += 30
                    draw.line([(margin, current_y), (width - margin, current_y)], fill='#8B0000', width=2)
                    current_y += section_height

                for line in goal_lines:
                    distorted_line = self._distort_text(line, sanity)
                    
                    if sanity < 30 and random.random() < 0.3:
                        offset_x = random.randint(-3, 3)
                        offset_y = random.randint(-2, 2)
                        draw.text((margin + offset_x, current_y + offset_y), distorted_line, fill='#DC143C', font=font_subtitle)
                    else:
                        draw.text((margin, current_y), distorted_line, fill='#DC143C', font=font_subtitle)
                    current_y += line_height
        
        # 应用理智崩坏的视觉扭曲效果
        if not is_insane_mode:
            img, draw = self._apply_sanity_distortion(img, draw, sanity, font_normal)
        
        # 保存图片
        img.save(output_path, 'PNG')
        logger.info(f"规则长图已生成：{output_path}")
        
        return output_path

    async def generate_rules_image(
        self,
        rules_title: str,
        rules: Sequence[RuleDict | Mapping[str, JsonValue] | str],
        win_condition: str,
        game_mode: str = "单人",
        output_path: str | None = None,
        sanity: int = 100,
        use_cache: bool = True,
    ) -> str:
        """异步生成规则长图（支持缓存）"""
        # 生成缓存键（不包含sanity，因为理智值变化不应该使用缓存）
        cache_key: str | None = None
        if sanity == 100:
            cache_key = self._get_cache_key(
                type="rules",
                # 变更渲染算法时用于自动失效旧缓存
                render_version="rules_wrap_v3",
                rules_title=rules_title,
                rules=[str(r.get("text", str(r))) if isinstance(r, Mapping) else str(r) for r in rules],
                win_condition=win_condition,
                game_mode=game_mode,
            )
            
            # 检查缓存
            if use_cache:
                cached_path = self._get_cached_image(cache_key)
                if cached_path:
                    return cached_path
        
        # 生成新图片
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_rules_image_sync,
            rules_title=rules_title,
            rules=rules,
            win_condition=win_condition,
            game_mode=game_mode,
            output_path=output_path,
            sanity=sanity,
        )
        result_path = await loop.run_in_executor(self._executor, func)
        
        # 缓存图片（仅当理智值为100时）
        if use_cache and sanity == 100 and cache_key is not None:
            self._cache_image(cache_key, result_path)
        
        return result_path

    # ==================== 行动结果长图 ====================
    
    def _generate_action_result_image_sync(
        self,
        user_name: str,
        action: str,
        is_dead: bool,
        scene_description: str,
        action_feedback: str,
        health: int,
        injury: str,
        fatigue: str,
        sanity: int,
        state: str,
        emotion: str,
        fear_level: int,
        anxiety_level: int,
        stress_level: int,
        found_items: list[str],
        found_clues: list[str],
        new_location: str | None,
        random_event: str | None,
        output_path: str | None = None,
    ) -> str:
        """同步方法：生成行动结果长图（纯黑背景+鲜红字体）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"action_{timestamp}_{random.randint(1000, 9999)}.png")

        # 加载字体
        font_title = self._get_font(self._font_path, 36)
        font_subtitle = self._get_font(self._font_path, 24)
        font_normal = self._get_font(self._font_path, 18)

        margin = 50
        title_height = 80
        line_height = 34  # 增大行高，避免长文 + 扰动时出现视觉重叠

        width = 900
        line_available_width = width - margin * 2

        _ = font_title

        # 理智崩坏模式（sanity=0）：只显示“对话/感受”，隐藏所有状态栏
        # TECHNICAL.md 设计：理智=0 时会出现“直接对话、否认死亡、诱导打破规则”的叙述风格。
        # 因此：即使本次行动判定死亡，也要优先进入理智崩坏展示，而不是只显示“你已死亡”。
        is_insane_mode = (sanity == 0)

        # 注意：保留 action 参数是为了兼容调用方；行动长图不再复述玩家行动
        _ = action
        _ = health
        _ = injury
        _ = fatigue
        _ = state
        _ = emotion
        _ = fear_level
        _ = anxiety_level
        _ = stress_level

        def _truncate_text(text: str, max_chars: int) -> str:
            t = str(text or "")
            if len(t) <= max_chars:
                return t
            t = t[:max_chars].rstrip()
            return t + "…"

        # 限制长度：避免极端超长导致图片过高不可读
        if scene_description:
            scene_description = _truncate_text(scene_description, 320 if is_insane_mode else 520)
        if action_feedback:
            action_feedback = _truncate_text(action_feedback, 220 if is_insane_mode else 360)
        if new_location:
            new_location = _truncate_text(new_location, 80)
        if random_event:
            random_event = _truncate_text(random_event, 220)

        def _wrap(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> list[str]:
            return self._wrap_text_by_width(str(text or ""), font, line_available_width)

        content_lines: list[str] = []

        if is_dead and not is_insane_mode:
            content_lines.append(f"行动结果 - {user_name}")
            content_lines.append("你已死亡！")

            if scene_description:
                content_lines.append("")
                content_lines.append("场景描述：")
                content_lines.extend(_wrap(scene_description, font_normal))

            if action_feedback:
                content_lines.append("")
                content_lines.append("行动反馈：")
                content_lines.extend(_wrap(action_feedback, font_normal))

            content_lines.append("")
            content_lines.append("你变成了怪谈的一部分。")

        elif is_insane_mode:
            # 理智崩坏模式：只显示场景描述和行动反馈（无标签/无状态栏/弱化死亡呈现）
            if scene_description:
                content_lines.extend(_wrap(scene_description, font_subtitle))
                content_lines.append("")

            if action_feedback:
                content_lines.extend(_wrap(action_feedback, font_subtitle))
                content_lines.append("")

            if not content_lines:
                content_lines.append("……")

        else:
            content_lines.append(f"行动结果 - {user_name}")
            content_lines.append("")
            content_lines.append("场景描述：")
            content_lines.extend(_wrap(scene_description, font_normal))

            if action_feedback:
                content_lines.append("")
                content_lines.append("行动反馈：")
                content_lines.extend(_wrap(action_feedback, font_normal))

            if not is_dead:
                if found_items:
                    content_lines.append("")
                    content_lines.append("获得物品：")
                    content_lines.extend(_wrap(', '.join(found_items), font_normal))

                if found_clues:
                    content_lines.append("")
                    content_lines.append("获得线索：")
                    content_lines.extend(_wrap(', '.join(found_clues), font_normal))

                if new_location:
                    content_lines.append("")
                    content_lines.append("当前位置：")
                    content_lines.extend(_wrap(new_location, font_normal))

                if random_event:
                    content_lines.append("")
                    content_lines.append("环境事件：")
                    content_lines.extend(_wrap(random_event, font_normal))

                content_lines.append("")
                content_lines.append("你存活了下来！继续探索吧。")
            else:
                content_lines.append("")
                content_lines.append("你变成了怪谈的一部分。")

        # 按实际绘制逻辑计算高度：空行只算半行，避免过高；也避免“预估不足导致挤压”
        total_height = margin * 2 + title_height + 50
        for line in content_lines:
            total_height += (line_height // 2) if line == "" else line_height

        img = Image.new('RGB', (width, total_height), color='#000000')
        draw = ImageDraw.Draw(img)

        current_y = margin
        for line in content_lines:
            if line == "":
                current_y += line_height // 2
                continue

            distorted_line = self._distort_text(line, sanity)

            # 文字错位效果：只做横向轻微扰动，避免纵向扰动造成重叠
            if sanity < 30 and sanity > 0 and random.random() < 0.3:
                offset_x = random.randint(-3, 3)
                base_x = margin + offset_x
            else:
                base_x = margin
            base_y = current_y

            if is_insane_mode:
                draw.text((base_x, base_y), distorted_line, fill='#8B0000', font=font_subtitle)
            elif line.startswith("行动："):
                draw.text((base_x, base_y), distorted_line, fill='#DC143C', font=font_subtitle)
            elif line.startswith("你已死亡！"):
                draw.text((base_x, base_y), distorted_line, fill='#FF0000', font=font_subtitle)
            elif line.startswith(("场景描述：",)):
                draw.text((base_x, base_y), distorted_line, fill='#DC143C', font=font_subtitle)
            elif line.startswith(("行动反馈：", "获得物品：", "获得线索：", "当前位置：", "环境事件：")):
                draw.text((base_x, base_y), distorted_line, fill='#DC143C', font=font_normal)
            elif line.startswith("你存活了下来！"):
                draw.text((base_x, base_y), distorted_line, fill='#DC143C', font=font_normal)
            else:
                draw.text((base_x, base_y), distorted_line, fill='#FF0000', font=font_normal)

            current_y += line_height
        
        # 应用理智崩坏的视觉扭曲效果
        img, draw = self._apply_sanity_distortion(img, draw, sanity, font_normal)
        
        img.save(output_path, 'PNG')
        logger.info(f"行动结果长图已生成：{output_path}")
        
        return output_path

    async def generate_action_result_image(
        self,
        user_name: str,
        action: str,
        is_dead: bool,
        scene_description: str,
        action_feedback: str,
        health: int = 100,
        injury: str = "无",
        fatigue: str = "正常",
        sanity: int = 100,
        state: str = "正常",
        emotion: str = "平静",
        fear_level: int = 0,
        anxiety_level: int = 0,
        stress_level: int = 0,
        found_items: list[str] | None = None,
        found_clues: list[str] | None = None,
        new_location: str | None = None,
        random_event: str | None = None,
        output_path: str | None = None,
    ) -> str:
        """异步生成行动结果长图（不使用缓存，因为每次都不同）"""
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_action_result_image_sync,
            user_name=user_name,
            action=action,
            is_dead=is_dead,
            scene_description=scene_description,
            action_feedback=action_feedback,
            health=health,
            injury=injury,
            fatigue=fatigue,
            sanity=sanity,
            state=state,
            emotion=emotion,
            fear_level=fear_level,
            anxiety_level=anxiety_level,
            stress_level=stress_level,
            found_items=found_items or [],
            found_clues=found_clues or [],
            new_location=new_location,
            random_event=random_event,
            output_path=output_path,
        )
        return await loop.run_in_executor(self._executor, func)

    # ==================== 结局长图 ====================
    
    def _generate_ending_image_sync(
        self,
        ending_title: str,
        ending_description: str,
        reasoning_analysis: str,
        truth_revealed: bool,
        hidden_truth: str | None = None,
        ending_type: str = "失败",
        output_path: str | None = None,
    ) -> str:
        """同步方法：生成结局长图（纯黑背景+鲜红字体）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"ending_{timestamp}.png")

        # 加载字体
        font_title = self._get_font(self._font_path, 40)
        font_subtitle = self._get_font(self._font_path, 28)
        font_normal = self._get_font(self._font_path, 20)

        margin = 60
        title_height = 100
        section_height = 50
        line_height = 30
        char_per_line = 38

        _ = ending_type
        _ = font_subtitle
        _ = section_height

        content_lines: list[str] = []
        
        # 结局描述
        for i in range(0, len(ending_description), char_per_line):
            content_lines.append(ending_description[i:i+char_per_line])
        
        # 推理分析（可选）：死亡/失败/强制结束等情况可传空字符串以隐藏解释
        if reasoning_analysis and str(reasoning_analysis).strip():
            content_lines.append("")
            for i in range(0, len(reasoning_analysis), char_per_line):
                content_lines.append(reasoning_analysis[i:i+char_per_line])
        
        # 隐藏真相
        if truth_revealed and hidden_truth:
            content_lines.append("")
            for i in range(0, len(hidden_truth), char_per_line):
                content_lines.append(hidden_truth[i:i+char_per_line])
        
        total_height = margin * 2 + title_height + len(content_lines) * line_height + 50
        
        width = 900
        img = Image.new('RGB', (width, total_height), color='#000000')
        draw = ImageDraw.Draw(img)
        
        # 绘制标题（动态居中）
        title_text = f"结局：{ending_title}"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title_text, fill='#8B0000', font=font_title)
        
        # 绘制分隔线
        draw.line([(margin, margin + title_height), (width - margin, margin + title_height)], fill='#8B0000', width=2)
        
        current_y = margin + title_height + 30
        for line in content_lines:
            if line.startswith("游戏结束。"):
                draw.text((margin, current_y), line, fill='#DC143C', font=font_normal)
            else:
                draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        img.save(output_path, 'PNG')
        logger.info(f"结局长图已生成：{output_path}")
        
        return output_path

    async def generate_ending_image(
        self,
        ending_title: str,
        ending_description: str,
        reasoning_analysis: str,
        truth_revealed: bool,
        hidden_truth: str | None = None,
        ending_type: str = "失败",
        output_path: str | None = None,
    ) -> str:
        """异步生成结局长图（不使用缓存）"""
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_ending_image_sync,
            ending_title=ending_title,
            ending_description=ending_description,
            reasoning_analysis=reasoning_analysis,
            truth_revealed=truth_revealed,
            hidden_truth=hidden_truth,
            ending_type=ending_type,
            output_path=output_path,
        )
        return await loop.run_in_executor(self._executor, func)

    # ==================== 物品栏长图 ====================
    
    def _generate_inventory_image_sync(
        self,
        inventory_data: Sequence[Mapping[str, JsonValue]],
        player_name: str = "玩家",
        title: str = "物品栏",
        output_path: str | None = None,
    ) -> str:
        """同步方法：生成物品栏图片（纯黑背景+鲜红字体）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"inventory_{timestamp}.png")

        font_title = self._get_font(self._font_path, 28)
        font_item = self._get_font(self._font_path, 20)
        font_desc = self._get_font(self._font_path, 16)

        margin = 40
        line_height = 28

        header_line = f"{player_name} 的{title}"
        content_lines = [header_line, ""]

        if not inventory_data:
            content_lines.append("（空）")
        else:
            for item in inventory_data:
                name = item.get("name", "未知物品")
                desc = item.get("description", "")
                content_lines.append(f"• {name}")
                if desc:
                    content_lines.append(f"  {desc}")
                content_lines.append("")

        img_width = 800
        img_height = margin * 2 + len(content_lines) * line_height + 30

        img = Image.new("RGB", (img_width, img_height), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)

        y = margin
        for line in content_lines:
            if line == header_line:
                draw.text((margin, y), line, font=font_title, fill=(200, 0, 0))
                y += 40
            elif line.startswith("•"):
                draw.text((margin, y), line, font=font_item, fill=(200, 0, 0))
                y += line_height
            elif line.startswith("  "):
                draw.text((margin + 20, y), line.strip(), font=font_desc, fill=(180, 0, 0))
                y += line_height - 5
            else:
                y += line_height

        img.save(output_path, "PNG")
        logger.info(f"物品栏图片已生成：{output_path}")
        
        return output_path

    async def generate_inventory_image(
        self,
        inventory_data: Sequence[Mapping[str, JsonValue]],
        player_name: str = "玩家",
        title: str = "物品栏",
        output_path: str | None = None,
        use_cache: bool = True,
    ) -> str:
        """异步生成物品栏图片（支持缓存）"""
        # 生成缓存键
        cache_key = self._get_cache_key(
            type="inventory",
            player_name=player_name,
            title=title,
            inventory=[item.get("name", "") for item in inventory_data],
        )
        
        # 检查缓存
        if use_cache:
            cached_path = self._get_cached_image(cache_key)
            if cached_path:
                return cached_path
        
        # 生成新图片
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_inventory_image_sync,
            inventory_data=inventory_data,
            player_name=player_name,
            title=title,
            output_path=output_path,
        )
        result_path = await loop.run_in_executor(self._executor, func)
        
        # 缓存图片
        if use_cache:
            self._cache_image(cache_key, result_path)
        
        return result_path

    # ==================== 场景结构文字长图（保持白底黑字）====================
    
    def _generate_scene_structure_text_image_sync(
        self,
        building_type: str,
        overall_layout: str,
        floors: Sequence[Mapping[str, JsonValue]],
        connections: Sequence[str],
        special_areas: Sequence[str],
        output_path: str | None = None,
    ) -> str:
        """同步方法：生成场景结构文字长图（白底黑字，保持原样）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"scene_structure_text_{timestamp}.png")
        
        # 加载字体
        font_title = self._get_font(self._font_path, 32)
        font_subtitle = self._get_font(self._font_path, 24)
        font_normal = self._get_font(self._font_path, 18)
        
        # 预估图片高度
        margin = 30
        title_height = 70
        _ = title_height
        section_height = 45
        line_height = 28
        line_length = 900 - 2 * margin
        
        # 统一复用引擎的按宽度换行（避免重复逻辑）
        # 计算总体布局需要的行数
        layout_lines = self._wrap_text_by_width(overall_layout, font_normal, line_length)

        # 计算楼层布局需要的行数
        floor_lines: list[str] = []
        for floor in floors:
            # 兼容两种字段名: floor/name 和 areas/rooms
            floor_name_raw = floor.get("floor") or floor.get("name", "")
            floor_name = str(floor_name_raw or "")

            areas_raw = floor.get("areas") or floor.get("rooms") or []
            areas_list: list[str] = []
            if isinstance(areas_raw, list):
                areas_list = [str(x) for x in areas_raw]
            elif isinstance(areas_raw, str):
                areas_list = [areas_raw]
            elif areas_raw:
                areas_list = [str(areas_raw)]

            floor_text = f"  - {floor_name}: {', '.join(areas_list)}"
            floor_lines.extend(self._wrap_text_by_width(floor_text, font_normal, line_length))

        # 计算连接通道需要的行数
        conn_text = f"连接通道：{', '.join(connections)}"
        conn_lines = self._wrap_text_by_width(conn_text, font_normal, line_length)

        # 计算特殊区域需要的行数
        special_text = f"特殊区域：{', '.join(special_areas)}"
        special_lines = self._wrap_text_by_width(special_text, font_normal, line_length)

        # 计算建筑类型需要的行数（修复：添加换行处理）
        building_type_text = f"建筑类型：{building_type}"
        building_type_lines = self._wrap_text_by_width(building_type_text, font_subtitle, line_length)

        # 计算总高度：按"实际绘制时的 y 递增逻辑"推导，避免漏算段落间距导致贴底
        bottom_padding = 60
        y = margin + 100  # 与绘制一致：分隔线之后开始

        # 建筑类型（根据实际行数计算高度）
        y += section_height
        y += (len(building_type_lines) - 1) * line_height

        # 总体布局：标题一行 + 标题后间距（合计一个 section_height 推进到内容起始）
        y += section_height
        y += len(layout_lines) * line_height

        # 楼层布局
        y += 20
        y += section_height
        y += len(floor_lines) * line_height

        # 连接通道
        y += 20
        y += section_height
        y += len(conn_lines) * line_height

        # 特殊区域
        y += 20
        y += section_height
        y += len(special_lines) * line_height

        total_height = y + bottom_padding
        
        # 创建图片（白底黑字）
        width = 900
        img = Image.new('RGB', (width, total_height), color='#FFFFFF')
        draw = ImageDraw.Draw(img)
        
        # 绘制标题（居中）
        title_text = "场景结构"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title_text, fill='#000000', font=font_title)
        
        # 绘制分隔线
        draw.line([(margin, margin + 80), (width - margin, margin + 80)], fill='#000000', width=2)
        
        # 绘制建筑类型（修复：支持多行显示）
        current_y = margin + 100
        for i, line in enumerate(building_type_lines):
            draw.text((margin, current_y), line, fill='#000000', font=font_subtitle)
            current_y += line_height if i < len(building_type_lines) - 1 else 0

        # 绘制总体布局
        current_y += section_height
        draw.text((margin, current_y), "总体布局", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in layout_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 绘制楼层布局
        current_y += 20
        draw.text((margin, current_y), "楼层布局", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in floor_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 绘制连接通道
        current_y += 20
        draw.text((margin, current_y), "连接通道", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in conn_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 绘制特殊区域
        current_y += 20
        draw.text((margin, current_y), "特殊区域", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in special_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 保存图片
        img.save(output_path, 'PNG')
        logger.info(f"场景结构文字长图已生成：{output_path}")
        
        return output_path

    async def generate_scene_structure_text_image(
        self,
        building_type: str,
        overall_layout: str,
        floors: Sequence[Mapping[str, JsonValue]],
        connections: Sequence[str],
        special_areas: Sequence[str],
        output_path: str | None = None,
        use_cache: bool = True,
    ) -> str:
        """异步生成场景结构文字长图（支持缓存）"""
        # 生成缓存键
        cache_key = self._get_cache_key(
            type="scene_structure",
            # 变更高度/排版计算时用于自动失效旧缓存
            render_version="scene_structure_v2",
            building_type=building_type,
            overall_layout=overall_layout,
            floors=floors,
            connections=connections,
            special_areas=special_areas,
        )
        
        # 检查缓存
        if use_cache:
            cached_path = self._get_cached_image(cache_key)
            if cached_path:
                return cached_path
        
        # 生成新图片
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_scene_structure_text_image_sync,
            building_type=building_type,
            overall_layout=overall_layout,
            floors=floors,
            connections=connections,
            special_areas=special_areas,
            output_path=output_path,
        )
        result_path = await loop.run_in_executor(self._executor, func)
        
        # 缓存图片
        if use_cache:
            self._cache_image(cache_key, result_path)
        
        return result_path

    # ==================== 入场长图（入场描述 + NPC引导）====================
    
    def _generate_entrance_long_image_sync(
        self,
        scene_name: str,
        entrance_description: str,
        npc_guidance: Mapping[str, JsonValue],
        output_path: str | None = None,
    ) -> str:
        """同步方法：生成入场长图（入场描述 + NPC引导，合二为一）"""
        _ = scene_name
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"entrance_{timestamp}.png")

        # 加载字体
        font_title = self._get_font(self._font_path, 36)
        font_subtitle = self._get_font(self._font_path, 28)
        font_normal = self._get_font(self._font_path, 20)

        margin = 60
        title_height = 80
        section_height = 50
        line_height = 35  # 增加行高，避免字体重叠
        char_per_line = 36  # 减少每行字符数，避免过于拥挤

        _ = font_subtitle
        _ = section_height
        _ = char_per_line

        # 获取NPC引导信息
        guidance_method = str(npc_guidance.get("guidance_method", "natural_language") or "natural_language")
        npc_name = str(npc_guidance.get("npc_name", "NPC") or "NPC")
        npc_role = str(npc_guidance.get("npc_role", "") or "")
        npc_attitude = str(npc_guidance.get("npc_attitude", "") or "")
        npc_behavior = str(npc_guidance.get("npc_behavior", "") or "")
        npc_dialogue = str(npc_guidance.get("npc_dialogue", "") or "")
        rule_carrier_title = str(npc_guidance.get("rule_carrier_title", "") or "")
        rule_carrier_description = str(npc_guidance.get("rule_carrier_description", "") or "")

        # 根据guidance_method决定标题和内容
        if guidance_method == "natural_language":
            # 避免“某某的警告/指示”这类系统感过强的标题，优先显示人物本身
            title = npc_name or "来者"
            if npc_role and npc_role not in title:
                title = f"{title} · {npc_role}"
        else:
            # 规则载体方式优先使用载体本身名称，而不是“某某的指示”
            title = rule_carrier_title or (f"{npc_name}留下的东西" if npc_name else "留下的东西")

        # 创建图片（纯黑背景）
        width = 900
        img = Image.new('RGB', (width, 1), color='#000000')  # 临时高度，后面会重新创建
        draw = ImageDraw.Draw(img)
        
        # 计算分割线之间的可用宽度（与分割线等长）
        line_available_width = width - margin * 2
        
        # 构建内容
        content_lines: list[str] = []

        # 第一部分：入场描述（使用动态宽度换行）
        entrance_lines = self._wrap_text_by_width(entrance_description, font_normal, line_available_width)
        content_lines.extend(entrance_lines)
        
        content_lines.append("")  # 空行分隔
        
        # 第二部分：NPC行为描述（使用动态宽度换行）
        if npc_behavior:
            behavior_lines = self._wrap_text_by_width(npc_behavior, font_normal, line_available_width)
            content_lines.extend(behavior_lines)
            content_lines.append("")  # 空行分隔
        
        # 第三部分：NPC对话或规则载体描述
        if guidance_method == "natural_language":
            # 自然语言引导允许展示 NPC 原话，但这些内容只是剧情信息，
            # 不应被系统自动视为“已确认规则”。
            if npc_dialogue:
                max_dialogue_chars = 800
                dialogue_text = str(npc_dialogue)
                if len(dialogue_text) > max_dialogue_chars:
                    dialogue_text = dialogue_text[:max_dialogue_chars].rstrip() + "…"

                dialogue_lines = self._wrap_text_by_width(dialogue_text, font_normal, line_available_width)
                content_lines.extend(dialogue_lines)
        else:
            # 规则载体描述：同样避免过短截断导致信息缺失
            if rule_carrier_description:
                if rule_carrier_title:
                    content_lines.extend(self._wrap_text_by_width(rule_carrier_title, font_normal, line_available_width))
                    content_lines.append("")
                max_carrier_chars = 600
                carrier_text = str(rule_carrier_description)
                if len(carrier_text) > max_carrier_chars:
                    carrier_text = carrier_text[:max_carrier_chars].rstrip() + "…"

                carrier_lines = self._wrap_text_by_width(carrier_text, font_normal, line_available_width)
                content_lines.extend(carrier_lines)

        # 计算总高度
        total_height = margin * 2 + title_height + len(content_lines) * line_height + 50

        # 重新创建图片（正确高度）
        img = Image.new('RGB', (width, total_height), color='#000000')
        draw = ImageDraw.Draw(img)

        # 绘制标题（动态居中）
        title_bbox = draw.textbbox((0, 0), title, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title, fill='#8B0000', font=font_title)

        # 绘制分隔线
        draw.line([(margin, margin + 80), (width - margin, margin + 80)], fill='#8B0000', width=2)

        # 绘制内容
        current_y = margin + 110
        for line in content_lines:
            if line == "":
                # 空行
                current_y += line_height // 2
            else:
                draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
                current_y += line_height

        # 保存图片
        img.save(output_path, 'PNG')
        logger.info(f"入场长图已生成：{output_path}")

        return output_path

    async def generate_entrance_long_image(
        self,
        scene_name: str,
        entrance_description: str,
        npc_guidance: Mapping[str, JsonValue],
        output_path: str | None = None,
        use_cache: bool = True,
    ) -> str:
        """异步生成入场长图（入场描述 + NPC引导，支持缓存）
        
        Args:
            scene_name: 场景名称
            entrance_description: 入场描述
            npc_guidance: NPC引导信息
            output_path: 输出路径
            use_cache: 是否使用缓存
        
        Returns:
            图片路径
        """
        # 生成缓存键
        cache_key = self._get_cache_key(
            type="entrance_long",
            # 变更排版/截断策略时用于自动失效旧缓存
            render_version="entrance_long_v3",
            scene_name=scene_name,
            entrance_description=entrance_description,
            npc_guidance=npc_guidance,
        )

        # 检查缓存
        if use_cache:
            cached_path = self._get_cached_image(cache_key)
            if cached_path:
                return cached_path

        # 生成新图片
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_entrance_long_image_sync,
            scene_name=scene_name,
            entrance_description=entrance_description,
            npc_guidance=npc_guidance,
            output_path=output_path,
        )
        result_path = await loop.run_in_executor(self._executor, func)

        # 缓存图片
        if use_cache:
            self._cache_image(cache_key, result_path)

        return result_path
