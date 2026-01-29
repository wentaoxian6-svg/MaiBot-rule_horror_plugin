"""异步图片生成器 - 使用线程池处理 CPU 密集型操作"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class AsyncImageGenerator:
    """异步图片生成器"""

    def __init__(self, output_dir: str, max_workers: int = 4):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    async def close(self) -> None:
        """关闭线程池"""
        self._executor.shutdown(wait=True)

    def _get_font(self, font_name: str, size: int) -> ImageFont.FreeTypeFont:
        """获取字体（带缓存）"""
        key = (font_name, size)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(font_name, size)
            except Exception:
                # 尝试备用字体
                for fallback in ["simhei.ttf", "simsun.ttc", "arial.ttf"]:
                    try:
                        self._font_cache[key] = ImageFont.truetype(fallback, size)
                        break
                    except Exception:
                        continue
                else:
                    self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

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
        new_location: Optional[str],
        random_event: Optional[str],
        output_path: Optional[str] = None,
    ) -> str:
        """同步方法：生成行动结果图片（支持理智崩坏效果）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"action_{timestamp}_{random.randint(1000, 9999)}.png")

        # 理智崩坏模式判定
        is_insane_mode = (sanity == 0 and not is_dead)
        is_low_sanity = (sanity < 30 and not is_dead)

        # 获取字体
        if is_insane_mode:
            font_title = self._get_font("msyh.ttc", 48)  # 更大的字体
            font_normal = self._get_font("msyh.ttc", 24)
        else:
            font_title = self._get_font("msyh.ttc", 36)
            font_subtitle = self._get_font("msyh.ttc", 24)
            font_normal = self._get_font("msyh.ttc", 18)

        # 计算布局
        margin = 50
        line_height = 32 if is_insane_mode else 26
        char_per_line = 30 if is_insane_mode else 45

        content_lines = []

        if is_dead:
            content_lines.append(f"行动结果 - {user_name}")
            content_lines.append(f"行动：{action}")
            content_lines.append("你已死亡！")
        elif is_insane_mode:
            # 理智崩坏模式：只显示场景描述和行动反馈，使用深红色大字体
            if scene_description:
                for i in range(0, len(scene_description), char_per_line):
                    content_lines.append(scene_description[i:i+char_per_line])
                content_lines.append("")
            if action_feedback:
                for i in range(0, len(action_feedback), char_per_line):
                    content_lines.append(action_feedback[i:i+char_per_line])
        else:
            # 正常模式
            content_lines.append(f"行动结果 - {user_name}")
            content_lines.append(f"行动：{action}")
            content_lines.append("")
            content_lines.append("场景描述：")
            for i in range(0, len(scene_description), char_per_line):
                content_lines.append(scene_description[i:i+char_per_line])

            content_lines.append("")
            content_lines.append("身体状况：")
            content_lines.append(f"  体力值：{health}/100")
            content_lines.append(f"  受伤：{injury}")
            content_lines.append(f"  疲劳：{fatigue}")

            content_lines.append("")
            content_lines.append("精神状况：")
            content_lines.append(f"  理智值：{sanity}/100")
            content_lines.append(f"  状态：{state}")
            content_lines.append(f"  情绪：{emotion}")

            if found_items:
                content_lines.append("")
                content_lines.append("获得物品：")
                for item in found_items:
                    content_lines.append(f"  • {item}")

            if new_location:
                content_lines.append("")
                content_lines.append(f"位置变更：{new_location}")

            if random_event:
                content_lines.append("")
                content_lines.append(f"环境事件：{random_event}")

        # 计算图片尺寸
        img_width = 1000
        img_height = margin * 2 + len(content_lines) * line_height + 100

        # 创建图片
        if is_insane_mode:
            # 理智崩坏：深红色背景
            img = Image.new("RGB", (img_width, img_height), color=(40, 0, 0))
        else:
            img = Image.new("RGB", (img_width, img_height), color=(20, 20, 30))
        
        draw = ImageDraw.Draw(img)

        # 绘制背景渐变效果
        if is_insane_mode:
            # 理智崩坏：深红色渐变
            for y in range(img_height):
                r = int(40 + (y / img_height) * 20)
                g = int(0 + (y / img_height) * 5)
                b = int(0 + (y / img_height) * 5)
                draw.line([(0, y), (img_width, y)], fill=(r, g, b))
        else:
            for y in range(img_height):
                r = int(20 + (y / img_height) * 10)
                g = int(20 + (y / img_height) * 5)
                b = int(30 + (y / img_height) * 10)
                draw.line([(0, y), (img_width, y)], fill=(r, g, b))

        # 绘制内容
        y = margin
        for i, line in enumerate(content_lines):
            # 对文本应用理智崩坏效果
            distorted_line = self._distort_text(line, sanity)
            
            # 文字错位效果（理智值低于30时）
            if sanity < 30 and sanity > 0 and random.random() < 0.3:
                offset_x = random.randint(-3, 3)
                offset_y = random.randint(-2, 2)
                base_x = margin + offset_x
                base_y = y + offset_y
            else:
                base_x = margin
                base_y = y
            
            if is_insane_mode:
                # 理智崩坏：深红色大字体
                draw.text((base_x, base_y), distorted_line, font=font_normal, fill=(139, 0, 0))
                y += line_height
            elif i == 0:
                draw.text((base_x, base_y), distorted_line, font=font_title, fill=(220, 220, 240))
                y += 50
            elif line.startswith("  "):
                draw.text((base_x + 20, base_y), distorted_line, font=font_normal, fill=(180, 180, 200))
                y += line_height
            elif line.endswith("："):
                draw.text((base_x, base_y), distorted_line, font=font_subtitle, fill=(200, 200, 220))
                y += 35
            else:
                draw.text((base_x, base_y), distorted_line, font=font_normal, fill=(200, 200, 220))
                y += line_height

        # 应用理智崩坏的视觉扭曲效果（使用原版精确逻辑）
        img, draw = self._apply_sanity_distortion(img, draw, sanity, font_normal)

        # 保存图片
        img.save(output_path, "PNG")
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
        found_items: Optional[list[str]] = None,
        new_location: Optional[str] = None,
        random_event: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """
        异步生成行动结果图片

        使用线程池执行 CPU 密集型的图片生成操作
        """
        loop = asyncio.get_event_loop()

        # 使用 functools.partial 绑定参数
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
            new_location=new_location,
            random_event=random_event,
            output_path=output_path,
        )

        # 在线程池中执行
        return await loop.run_in_executor(self._executor, func)

    def _generate_rules_image_sync(
        self,
        rules_title: str,
        rules: list[dict[str, Any]],
        win_condition: str,
        game_mode: str = "单人",
        output_path: Optional[str] = None,
        sanity: int = 100,
    ) -> str:
        """同步方法：生成规则图片"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"rules_{timestamp}.png")

        font_title = self._get_font("msyh.ttc", 32)
        font_rule = self._get_font("msyh.ttc", 20)
        font_normal = self._get_font("msyh.ttc", 18)

        margin = 40
        line_height = 30

        content_lines = [rules_title, ""]
        content_lines.append(f"游戏模式：{game_mode}")
        content_lines.append("")
        content_lines.append("=== 规则 ===")

        for i, rule in enumerate(rules, 1):
            rule_text = rule.get("text", rule.get("content", str(rule)))
            content_lines.append(f"{i}. {rule_text}")

        content_lines.append("")
        content_lines.append("=== 通关条件 ===")
        content_lines.append(win_condition)

        # 计算尺寸
        img_width = 900
        img_height = margin * 2 + len(content_lines) * line_height + 50

        # 创建图片
        img = Image.new("RGB", (img_width, img_height), color=(25, 25, 35))
        draw = ImageDraw.Draw(img)

        # 绘制背景
        for y in range(img_height):
            r = int(25 + (y / img_height) * 15)
            g = int(25 + (y / img_height) * 10)
            b = int(35 + (y / img_height) * 15)
            draw.line([(0, y), (img_width, y)], fill=(r, g, b))

        # 绘制内容
        y = margin
        for line in content_lines:
            if line == rules_title:
                draw.text((margin, y), line, font=font_title, fill=(240, 200, 100))
                y += 45
            elif line.startswith("==="):
                draw.text((margin, y), line.replace("=", "").strip(), font=font_rule, fill=(200, 200, 100))
                y += 35
            elif line.startswith("游戏模式"):
                draw.text((margin, y), line, font=font_normal, fill=(150, 200, 150))
                y += line_height
            else:
                draw.text((margin, y), line, font=font_normal, fill=(220, 220, 240))
                y += line_height

        img.save(output_path, "PNG")
        return output_path

    async def generate_rules_image(
        self,
        rules_title: str,
        rules: list[dict[str, Any]],
        win_condition: str,
        game_mode: str = "单人",
        output_path: Optional[str] = None,
        sanity: int = 100,
    ) -> str:
        """异步生成规则图片"""
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
        return await loop.run_in_executor(self._executor, func)

    def _generate_inventory_image_sync(
        self,
        inventory_data: list[dict[str, Any]],
        player_name: str = "玩家",
        output_path: Optional[str] = None,
    ) -> str:
        """同步方法：生成物品栏图片"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"inventory_{timestamp}.png")

        font_title = self._get_font("msyh.ttc", 28)
        font_item = self._get_font("msyh.ttc", 20)
        font_desc = self._get_font("msyh.ttc", 16)

        margin = 40
        line_height = 28

        content_lines = [f"{player_name} 的物品栏", ""]

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

        img = Image.new("RGB", (img_width, img_height), color=(30, 30, 40))
        draw = ImageDraw.Draw(img)

        # 背景
        for y in range(img_height):
            r = int(30 + (y / img_height) * 10)
            g = int(30 + (y / img_height) * 5)
            b = int(40 + (y / img_height) * 10)
            draw.line([(0, y), (img_width, y)], fill=(r, g, b))

        y = margin
        for line in content_lines:
            if line == f"{player_name} 的物品栏":
                draw.text((margin, y), line, font=font_title, fill=(200, 180, 100))
                y += 40
            elif line.startswith("•"):
                draw.text((margin, y), line, font=font_item, fill=(220, 220, 200))
                y += line_height
            elif line.startswith("  "):
                draw.text((margin + 20, y), line.strip(), font=font_desc, fill=(180, 180, 180))
                y += line_height - 5
            else:
                y += line_height

        img.save(output_path, "PNG")
        return output_path

    async def generate_inventory_image(
        self,
        inventory_data: list[dict[str, Any]],
        player_name: str = "玩家",
        output_path: Optional[str] = None,
    ) -> str:
        """异步生成物品栏图片"""
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_inventory_image_sync,
            inventory_data=inventory_data,
            player_name=player_name,
            output_path=output_path,
        )
        return await loop.run_in_executor(self._executor, func)

    def _generate_scene_image_sync(
        self,
        scene_name: str,
        background: str,
        arrival_reason: str,
        core_symbols: Optional[list[dict[str, str]]] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """同步方法：生成剧情导入长图（黑暗背景+鲜红字体）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"plot_{timestamp}.png")

        font_title = self._get_font("msyh.ttc", 48)
        font_subtitle = self._get_font("msyh.ttc", 28)
        font_normal = self._get_font("msyh.ttc", 20)

        margin = 60
        line_height = 32
        char_per_line = 40

        content_lines = [scene_name, ""]
        
        # 背景故事
        content_lines.append("=== 背景故事 ===")
        for i in range(0, len(background), char_per_line):
            content_lines.append(background[i:i+char_per_line])
        
        content_lines.append("")
        
        # 玩家身份
        content_lines.append("=== 你的身份 ===")
        for i in range(0, len(arrival_reason), char_per_line):
            content_lines.append(arrival_reason[i:i+char_per_line])
        
        # 核心象征符号
        if core_symbols:
            content_lines.append("")
            content_lines.append("=== 核心象征符号 ===")
            for symbol in core_symbols:
                symbol_name = symbol.get("symbol", "")
                symbol_desc = symbol.get("description", "")
                content_lines.append(f"• {symbol_name}")
                for i in range(0, len(symbol_desc), char_per_line - 4):
                    content_lines.append(f"  {symbol_desc[i:i+char_per_line-4]}")

        # 计算尺寸
        img_width = 1100
        img_height = margin * 2 + len(content_lines) * line_height + 100

        # 创建图片（黑暗背景）
        img = Image.new("RGB", (img_width, img_height), color=(15, 15, 20))
        draw = ImageDraw.Draw(img)

        # 绘制背景渐变
        for y in range(img_height):
            r = int(15 + (y / img_height) * 10)
            g = int(15 + (y / img_height) * 5)
            b = int(20 + (y / img_height) * 10)
            draw.line([(0, y), (img_width, y)], fill=(r, g, b))

        # 绘制内容
        y = margin
        for line in content_lines:
            if line == scene_name:
                # 标题：鲜红色
                draw.text((margin, y), line, font=font_title, fill=(200, 0, 0))
                y += 60
            elif line.startswith("==="):
                # 章节标题：深红色
                draw.text((margin, y), line.replace("=", "").strip(), font=font_subtitle, fill=(180, 0, 0))
                y += 40
            elif line.startswith("•"):
                # 列表项：浅红色
                draw.text((margin, y), line, font=font_normal, fill=(200, 50, 50))
                y += line_height
            elif line.startswith("  "):
                # 缩进文本：灰红色
                draw.text((margin + 20, y), line.strip(), font=font_normal, fill=(150, 50, 50))
                y += line_height
            else:
                # 普通文本：浅灰红色
                draw.text((margin, y), line, font=font_normal, fill=(180, 80, 80))
                y += line_height

        img.save(output_path, "PNG")
        return output_path

    async def generate_scene_structure_text_image(
        self,
        building_type: str,
        overall_layout: str,
        floors: list[dict[str, Any]],
        connections: list[str],
        special_areas: list[str],
        output_path: Optional[str] = None,
    ) -> str:
        """异步生成场景结构文字长图"""
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
        return await loop.run_in_executor(self._executor, func)
    
    def _generate_scene_structure_text_image_sync(
        self,
        building_type: str,
        overall_layout: str,
        floors: list[dict[str, Any]],
        connections: list[str],
        special_areas: list[str],
        output_path: Optional[str] = None,
    ) -> str:
        """同步方法：生成场景结构文字长图（白底黑字）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"scene_structure_text_{timestamp}.png")
        
        # 加载字体
        font_title = self._get_font("msyh.ttc", 32)
        font_subtitle = self._get_font("msyh.ttc", 24)
        font_normal = self._get_font("msyh.ttc", 18)
        
        # 预估图片高度
        margin = 30
        title_height = 70
        section_height = 45
        line_height = 28
        line_length = 900 - 2 * margin
        
        def wrap_text(text: str, font, max_width: int) -> list[str]:
            """根据文本宽度自动换行"""
            lines = []
            current_line = ""
            temp_img = Image.new('RGB', (1, 1))
            temp_draw = ImageDraw.Draw(temp_img)
            
            for char in text:
                test_line = current_line + char
                bbox = temp_draw.textbbox((0, 0), test_line, font=font)
                text_width = bbox[2] - bbox[0]
                
                if text_width <= max_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = char
            
            if current_line:
                lines.append(current_line)
            
            return lines
        
        # 计算总体布局需要的行数
        layout_lines = wrap_text(overall_layout, font_normal, line_length)
        
        # 计算楼层布局需要的行数
        floor_lines = []
        for floor in floors:
            floor_name = floor.get('floor', '')
            areas = floor.get('areas', [])
            floor_text = f"  - {floor_name}: {', '.join(areas)}"
            floor_lines.extend(wrap_text(floor_text, font_normal, line_length))
        
        # 计算连接通道需要的行数
        conn_text = f"连接通道：{', '.join(connections)}"
        conn_lines = wrap_text(conn_text, font_normal, line_length)
        
        # 计算特殊区域需要的行数
        special_text = f"特殊区域：{', '.join(special_areas)}"
        special_lines = wrap_text(special_text, font_normal, line_length)
        
        # 计算总高度
        total_height = (margin * 2 + title_height + section_height + 
                       len(layout_lines) * line_height + section_height + 
                       len(floor_lines) * line_height + section_height + 
                       len(conn_lines) * line_height + section_height + 
                       len(special_lines) * line_height + 100)
        
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
        
        # 绘制建筑类型
        current_y = margin + 100
        draw.text((margin, current_y), f"建筑类型：{building_type}", fill='#000000', font=font_subtitle)
        
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

    async def generate_scene_image(
        self,
        scene_name: str,
        background: str,
        arrival_reason: str,
        core_symbols: Optional[list[dict[str, str]]] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """异步生成剧情导入长图"""
        loop = asyncio.get_event_loop()
        func = functools.partial(
            self._generate_scene_image_sync,
            scene_name=scene_name,
            background=background,
            arrival_reason=arrival_reason,
            core_symbols=core_symbols,
            output_path=output_path,
        )
        return await loop.run_in_executor(self._executor, func)

    def _generate_ending_image_sync(
        self,
        ending_title: str,
        ending_description: str,
        reasoning_analysis: str,
        truth_revealed: bool,
        hidden_truth: Optional[str] = None,
        ending_type: str = "失败",
        output_path: Optional[str] = None,
    ) -> str:
        """同步方法：生成结局长图（黑暗背景+鲜红字体）"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self.output_dir / f"ending_{timestamp}.png")

        font_title = self._get_font("msyh.ttc", 48)
        font_subtitle = self._get_font("msyh.ttc", 28)
        font_normal = self._get_font("msyh.ttc", 20)

        margin = 60
        line_height = 32
        char_per_line = 40

        content_lines = [ending_title, ""]
        
        # 结局描述
        for i in range(0, len(ending_description), char_per_line):
            content_lines.append(ending_description[i:i+char_per_line])
        
        content_lines.append("")
        content_lines.append("=== 推理分析 ===")
        for i in range(0, len(reasoning_analysis), char_per_line):
            content_lines.append(reasoning_analysis[i:i+char_per_line])
        
        # 隐藏真相
        if truth_revealed and hidden_truth:
            content_lines.append("")
            content_lines.append("=== 隐藏真相 ===")
            for i in range(0, len(hidden_truth), char_per_line):
                content_lines.append(hidden_truth[i:i+char_per_line])
        
        content_lines.append("")
        content_lines.append(f"结局类型：{ending_type}")

        # 计算尺寸
        img_width = 1100
        img_height = margin * 2 + len(content_lines) * line_height + 100

        # 创建图片
        img = Image.new("RGB", (img_width, img_height), color=(15, 15, 20))
        draw = ImageDraw.Draw(img)

        # 绘制背景渐变
        for y in range(img_height):
            r = int(15 + (y / img_height) * 10)
            g = int(15 + (y / img_height) * 5)
            b = int(20 + (y / img_height) * 10)
            draw.line([(0, y), (img_width, y)], fill=(r, g, b))

        # 绘制内容
        y = margin
        for line in content_lines:
            if line == ending_title:
                draw.text((margin, y), line, font=font_title, fill=(200, 0, 0))
                y += 60
            elif line.startswith("==="):
                draw.text((margin, y), line.replace("=", "").strip(), font=font_subtitle, fill=(180, 0, 0))
                y += 40
            elif line.startswith("结局类型"):
                draw.text((margin, y), line, font=font_subtitle, fill=(200, 50, 50))
                y += 40
            else:
                draw.text((margin, y), line, font=font_normal, fill=(180, 80, 80))
                y += line_height

        img.save(output_path, "PNG")
        return output_path

    async def generate_ending_image(
        self,
        ending_title: str,
        ending_description: str,
        reasoning_analysis: str,
        truth_revealed: bool,
        hidden_truth: Optional[str] = None,
        ending_type: str = "失败",
        output_path: Optional[str] = None,
    ) -> str:
        """异步生成结局长图"""
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

    def _apply_sanity_distortion(
        self,
        img: Image.Image,
        draw: ImageDraw.ImageDraw,
        sanity: int,
        font_normal: ImageFont.FreeTypeFont,
    ) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        """应用理智崩坏时的视觉扭曲效果（基于原版plugin_old.py的精确逻辑）
        
        Args:
            img: PIL Image对象
            draw: ImageDraw对象
            sanity: 理智值
            font_normal: 字体对象
        
        Returns:
            处理后的img和draw对象
        """
        _ = font_normal
        if sanity >= 30 or sanity == 0:
            return img, draw
        
        width, height = img.size
        
        # 计算理智崩坏程度
        # 30-20: 最轻微 (0.0-0.33)
        # 20-10: 中等 (0.33-0.67)
        # 10-0: 最强 (0.67-1.0)
        if sanity > 20:
            insanity_level = (30 - sanity) / 30.0 * 0.33
        elif sanity > 10:
            insanity_level = (20 - sanity) / 10.0 * 0.33 + 0.33
        else:
            insanity_level = (10 - sanity) / 10.0 * 0.33 + 0.67
        
        # 效果1：红色涂鸦遮盖（根据insanity_level控制数量和大小）
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
        
        # 效果2：红色斜线遮盖（根据insanity_level控制数量）
        num_lines = int(1 + 2 * insanity_level)
        for _ in range(num_lines):
            y = random.randint(150, height - 150)
            line_width = int(2 + 3 * insanity_level)
            draw.line([(50, y), (width - 50, y)], fill=(255, 0, 0), width=line_width)
        
        # 效果3：黑色涂抹效果（模拟文字被涂抹）
        num_black_scribbles = int(2 + 4 * insanity_level)
        for _ in range(num_black_scribbles):
            x1 = random.randint(50, width - 50)
            y1 = random.randint(100, height - 100)
            scribble_width = int(30 + 80 * insanity_level)
            scribble_height = int(8 + 15 * insanity_level)
            x2 = x1 + scribble_width
            y2 = y1 + scribble_height
            draw.rectangle([x1, y1, x2, y2], fill=(0, 0, 0))
        
        # 效果4：红色涂抹效果（模拟血迹涂抹）
        num_red_scribbles = int(2 + 5 * insanity_level)
        for _ in range(num_red_scribbles):
            x1 = random.randint(50, width - 50)
            y1 = random.randint(100, height - 100)
            scribble_width = int(40 + 90 * insanity_level)
            scribble_height = int(12 + 18 * insanity_level)
            x2 = x1 + scribble_width
            y2 = y1 + scribble_height
            alpha = int(100 + 155 * insanity_level)
            draw.rectangle([x1, y1, x2, y2], fill=(200, 0, 0, alpha))
        
        return img, draw

    def _distort_text(self, text: str, sanity: int) -> str:
        """对文本进行理智崩坏扭曲处理（基于原版plugin_old.py的精确逻辑）
        
        Args:
            text: 原始文本
            sanity: 理智值
        
        Returns:
            扭曲后的文本
        """
        import re
        
        if sanity >= 30 or sanity == 0:
            return text
        
        # 计算理智崩坏程度
        # 30-20: 最轻微 (0.0-0.33)
        # 20-10: 中等 (0.33-0.67)
        # 10-0: 最强 (0.67-1.0)
        if sanity > 20:
            insanity_level = (30 - sanity) / 30.0 * 0.33
        elif sanity > 10:
            insanity_level = (20 - sanity) / 10.0 * 0.33 + 0.33
        else:
            insanity_level = (10 - sanity) / 10.0 * 0.33 + 0.67
        
        # 效果1：插入乱码符号（根据insanity_level控制数量）
        symbols = ['#', '@', '$', '%', '^', '&', '*', '!', '?', '~', '×', '÷', '※', '※', '●', '■', '◆', '★']
        chinese_garbled = ['乱', '码', '崩', '坏', '死', '亡', '恐', '惧', '绝', '望', '疯', '狂']
        num_insertions = int(2 + 5 * insanity_level)
        text_list = list(text)
        for _ in range(num_insertions):
            if len(text_list) == 0:
                break
            pos = random.randint(0, len(text_list))
            if random.random() < 0.3:
                text_list.insert(pos, random.choice(chinese_garbled))
            else:
                text_list.insert(pos, random.choice(symbols))
        text = ''.join(text_list)
        
        # 效果2：重复词语（针对中文，根据insanity_level控制重复次数）
        words = re.findall(r'[\u4e00-\u9fff]+', text)
        if words:
            word_to_repeat = random.choice(words)
            if len(word_to_repeat) >= 2:
                repeat_count = int(2 + 3 * insanity_level)
                text = text.replace(word_to_repeat, word_to_repeat * repeat_count, 1)
        
        # 效果3：字符错位（随机交换相邻字符，根据insanity_level控制交换频率）
        text_list = list(text)
        step = int(10 - 7 * insanity_level)
        if step < 2:
            step = 2
        for i in range(0, len(text_list) - 1, step):
            if i + 1 < len(text_list):
                text_list[i], text_list[i + 1] = text_list[i + 1], text_list[i]
        text = ''.join(text_list)
        
        # 效果4：文字缺失（随机删除部分文字字符，根据insanity_level控制删除数量）
        if insanity_level > 0.33:
            text_list = list(text)
            num_deletions = int(len(text_list) * (0.05 + 0.15 * insanity_level))
            for _ in range(num_deletions):
                if len(text_list) > 0:
                    pos = random.randint(0, len(text_list) - 1)
                    text_list.pop(pos)
            text = ''.join(text_list)
        
        return text
