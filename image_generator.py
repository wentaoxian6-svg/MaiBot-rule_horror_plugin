"""
图片生成工具类
统一管理图片生成逻辑，消除重复代码
"""

from typing import List, Dict, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import os


class ImageGenerator:
    """图片生成器
    
    统一管理所有图片生成逻辑，包括：
    - 字体加载
    - 文本换行计算
    - 图片尺寸计算
    - 颜色配置
    - 常用图形绘制
    """
    
    FONT_SIZES = {
        "title": 36,
        "subtitle": 24,
        "normal": 18,
        "small": 16
    }
    
    COLORS = {
        "background_dark": "#0a0a0a",
        "background_light": "#FFFFFF",
        "title_red": "#8B0000",
        "text_red": "#FF0000",
        "text_gray": "#AAAAAA",
        "border_red": "#8B0000",
        "text_black": "#000000"
    }
    
    LAYOUT = {
        "margin": 60,
        "title_height": 80,
        "section_height": 40,
        "line_height": 30,
        "char_per_line": 40,
        "width": 900
    }
    
    def __init__(self, temp_images_dir: str):
        self.temp_images_dir = temp_images_dir
        self.fonts: Dict[str, ImageFont.FreeTypeFont] = {}
        self._load_fonts()
    
    def _load_fonts(self):
        """加载字体"""
        font_configs = [
            ("title", self.FONT_SIZES["title"]),
            ("subtitle", self.FONT_SIZES["subtitle"]),
            ("normal", self.FONT_SIZES["normal"]),
            ("small", self.FONT_SIZES["small"])
        ]
        
        for font_name, size in font_configs:
            self.fonts[font_name] = self._load_font(size)
    
    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        """加载单个字体"""
        try:
            return ImageFont.truetype("msyh.ttc", size)
        except Exception:
            try:
                return ImageFont.truetype("simhei.ttf", size)
            except Exception:
                return ImageFont.load_default()
    
    def get_font(self, font_name: str) -> ImageFont.FreeTypeFont:
        """获取字体"""
        return self.fonts.get(font_name, self.fonts["normal"])
    
    def wrap_text(self, text: str, font: ImageFont.FreeTypeFont, 
                  max_width: int) -> List[str]:
        """文本换行
        
        Args:
            text: 原始文本
            font: 字体
            max_width: 最大宽度（像素）
        
        Returns:
            换行后的文本列表
        """
        lines = []
        words = list(text)
        current_line = ""
        
        for word in words:
            test_line = current_line + word
            bbox = font.getbbox(test_line)
            width = bbox[2] - bbox[0]
            
            if width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        
        if current_line:
            lines.append(current_line)
        
        return lines
    
    def wrap_text_by_chars(self, text: str, char_per_line: int) -> List[str]:
        """按字符数换行
        
        Args:
            text: 原始文本
            char_per_line: 每行字符数
        
        Returns:
            换行后的文本列表
        """
        lines = []
        for i in range(0, len(text), char_per_line):
            lines.append(text[i:i+char_per_line])
        return lines
    
    def calculate_text_height(self, lines: List[str], line_height: int) -> int:
        """计算文本总高度
        
        Args:
            lines: 文本行列表
            line_height: 行高
        
        Returns:
            总高度
        """
        return len(lines) * line_height
    
    def create_image(self, width: int, height: int, 
                     background_color: str = COLORS["background_dark"]) -> Image.Image:
        """创建图片
        
        Args:
            width: 宽度
            height: 高度
            background_color: 背景颜色
        
        Returns:
            PIL图片对象
        """
        return Image.new('RGB', (width, height), color=background_color)
    
    def draw_text(self, draw: ImageDraw.ImageDraw, text: str, 
                  position: Tuple[int, int], font: ImageFont.FreeTypeFont,
                  fill: str = COLORS["text_red"]) -> None:
        """绘制文本
        
        Args:
            draw: 绘图对象
            text: 文本内容
            position: 位置 (x, y)
            font: 字体
            fill: 颜色
        """
        draw.text(position, text, fill=fill, font=font)
    
    def draw_centered_text(self, draw: ImageDraw.ImageDraw, text: str,
                           y: int, width: int, font: ImageFont.FreeTypeFont,
                           fill: str = COLORS["title_red"]) -> None:
        """绘制居中文本
        
        Args:
            draw: 绘图对象
            text: 文本内容
            y: Y坐标
            width: 图片宽度
            font: 字体
            fill: 颜色
        """
        bbox = font.getbbox(text)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        self.draw_text(draw, text, (x, y), font, fill)
    
    def draw_line(self, draw: ImageDraw.ImageDraw, start: Tuple[int, int],
                  end: Tuple[int, int], fill: str = COLORS["border_red"],
                  width: int = 2) -> None:
        """绘制线条
        
        Args:
            draw: 绘图对象
            start: 起点 (x, y)
            end: 终点 (x, y)
            fill: 颜色
            width: 线宽
        """
        draw.line([start, end], fill=fill, width=width)
    
    def draw_rectangle(self, draw: ImageDraw.ImageDraw, 
                       coords: Tuple[int, int, int, int],
                       fill: Optional[str] = None,
                       outline: Optional[str] = None,
                       width: int = 1) -> None:
        """绘制矩形
        
        Args:
            draw: 绘图对象
            coords: 坐标 (x1, y1, x2, y2)
            fill: 填充颜色
            outline: 边框颜色
            width: 边框宽度
        """
        draw.rectangle(coords, fill=fill, outline=outline, width=width)
    
    def generate_inventory_image(self, inventory_data: List, 
                                  player_name: str = "玩家",
                                  output_path: Optional[str] = None) -> str:
        """生成道具清单图片
        
        Args:
            inventory_data: 道具列表
            player_name: 玩家名称
            output_path: 输出路径
        
        Returns:
            生成的图片路径
        """
        font_title = self.get_font("title")
        font_subtitle = self.get_font("subtitle")
        font_normal = self.get_font("normal")
        
        margin = self.LAYOUT["margin"]
        title_height = self.LAYOUT["title_height"]
        line_height = self.LAYOUT["line_height"]
        char_per_line = self.LAYOUT["char_per_line"]
        width = self.LAYOUT["width"]
        
        content_lines = []
        if not inventory_data:
            content_lines.append("暂无道具")
        else:
            for i, item in enumerate(inventory_data, 1):
                if isinstance(item, dict):
                    item_name = item.get("name", "未知道具")
                    item_desc = item.get("description", "")
                    content_lines.append(f"{i}. {item_name}")
                    if item_desc:
                        desc_lines = self.wrap_text_by_chars(item_desc, char_per_line)
                        content_lines.extend([f"   {line}" for line in desc_lines])
                else:
                    content_lines.append(f"{i}. {item}")
        
        total_height = margin * 2 + title_height + len(content_lines) * line_height + 50
        
        img = self.create_image(width, total_height)
        draw = ImageDraw.Draw(img)
        
        self.draw_centered_text(draw, f"{player_name}的道具清单", 
                                margin, width, font_title, self.COLORS["title_red"])
        self.draw_line(draw, (margin, margin + title_height), 
                      (width - margin, margin + title_height))
        
        current_y = margin + title_height + 30
        for line in content_lines:
            if line.startswith("   "):
                self.draw_text(draw, line, (margin + 20, current_y), 
                              font_normal, self.COLORS["text_gray"])
            else:
                self.draw_text(draw, line, (margin, current_y), 
                              font_subtitle, self.COLORS["text_red"])
            current_y += line_height
        
        return self._save_image(img, output_path, "inventory")
    
    def generate_item_details_image(self, item_data: Dict,
                                     player_name: str = "玩家",
                                     output_path: Optional[str] = None) -> str:
        """生成道具详情图片
        
        Args:
            item_data: 道具数据
            player_name: 玩家名称
            output_path: 输出路径
        
        Returns:
            生成的图片路径
        """
        font_title = self.get_font("title")
        font_subtitle = self.get_font("subtitle")
        font_normal = self.get_font("normal")
        
        margin = self.LAYOUT["margin"]
        title_height = self.LAYOUT["title_height"]
        section_height = self.LAYOUT["section_height"]
        line_height = self.LAYOUT["line_height"]
        char_per_line = self.LAYOUT["char_per_line"]
        width = self.LAYOUT["width"]
        
        item_name = item_data.get("name", "未知道具")
        item_type = item_data.get("type", "其他")
        item_desc = item_data.get("description", "")
        item_hint = item_data.get("observation_hint", "")
        
        content_lines = []
        content_lines.append(f"类型：{item_type}")
        content_lines.append("")
        content_lines.append("描述：")
        
        if item_desc:
            desc_lines = self.wrap_text_by_chars(item_desc, char_per_line)
            content_lines.extend([f"  {line}" for line in desc_lines])
        
        content_lines.append("")
        content_lines.append("观察提示：")
        
        if item_hint:
            hint_lines = self.wrap_text_by_chars(item_hint, char_per_line)
            content_lines.extend([f"  {line}" for line in hint_lines])
        
        total_height = margin * 2 + title_height + section_height * 2 + \
                      len(content_lines) * line_height + 50
        
        img = self.create_image(width, total_height)
        draw = ImageDraw.Draw(img)
        
        self.draw_centered_text(draw, f"{player_name}的道具详情", 
                                margin, width, font_title, self.COLORS["title_red"])
        self.draw_line(draw, (margin, margin + title_height), 
                      (width - margin, margin + title_height))
        
        current_y = margin + title_height + 30
        self.draw_text(draw, f"道具：{item_name}", (margin, current_y), 
                      font_subtitle, self.COLORS["text_red"])
        current_y += section_height
        
        for line in content_lines:
            if line.startswith("  "):
                self.draw_text(draw, line, (margin + 20, current_y), 
                              font_normal, self.COLORS["text_gray"])
            else:
                self.draw_text(draw, line, (margin, current_y), 
                              font_normal, self.COLORS["text_red"])
            current_y += line_height
        
        return self._save_image(img, output_path, "item_details")
    
    def generate_npc_guidance_image(self, npc_name: str, npc_role: str,
                                    npc_attitude: str, npc_behavior: str,
                                    npc_dialogue: str,
                                    output_path: Optional[str] = None) -> str:
        """生成NPC引导图片
        
        Args:
            npc_name: NPC姓名
            npc_role: NPC角色
            npc_attitude: NPC态度
            npc_behavior: NPC行为描述
            npc_dialogue: NPC对话内容
            output_path: 输出路径
        
        Returns:
            生成的图片路径
        """
        font_title = self.get_font("title")
        font_subtitle = self.get_font("subtitle")
        font_normal = self.get_font("normal")
        
        margin = self.LAYOUT["margin"]
        title_height = self.LAYOUT["title_height"]
        line_height = self.LAYOUT["line_height"]
        char_per_line = self.LAYOUT["char_per_line"]
        width = self.LAYOUT["width"]
        
        content_lines = []
        content_lines.append(f"人物：{npc_name} ({npc_role})")
        content_lines.append("")
        content_lines.append("行为：")
        
        if npc_behavior:
            behavior_lines = self.wrap_text_by_chars(npc_behavior, char_per_line)
            content_lines.extend([f"  {line}" for line in behavior_lines])
        
        content_lines.append("")
        content_lines.append("对话：")
        
        if npc_dialogue:
            dialogue_lines = self.wrap_text_by_chars(npc_dialogue, char_per_line)
            content_lines.extend([f"  {line}" for line in dialogue_lines])
        
        total_height = margin * 2 + title_height + len(content_lines) * line_height + 50
        
        img = self.create_image(width, total_height)
        draw = ImageDraw.Draw(img)
        
        self.draw_centered_text(draw, "NPC引导", margin, width, 
                                font_title, self.COLORS["title_red"])
        self.draw_line(draw, (margin, margin + title_height), 
                      (width - margin, margin + title_height))
        
        current_y = margin + title_height + 30
        for line in content_lines:
            if line.startswith("  "):
                self.draw_text(draw, line, (margin + 20, current_y), 
                              font_normal, self.COLORS["text_gray"])
            else:
                self.draw_text(draw, line, (margin, current_y), 
                              font_subtitle, self.COLORS["text_red"])
            current_y += line_height
        
        return self._save_image(img, output_path, "npc_guidance")
    
    def _save_image(self, img: Image.Image, output_path: Optional[str], 
                   prefix: str) -> str:
        """保存图片
        
        Args:
            img: PIL图片对象
            output_path: 输出路径
            prefix: 文件名前缀
        
        Returns:
            保存的图片路径
        """
        if output_path is None:
            os.makedirs(self.temp_images_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(self.temp_images_dir, 
                                       f"{prefix}_{timestamp}.png")
        
        img.save(output_path)
        return output_path
    
    def calculate_image_dimensions(self, content_lines: List[str], 
                                   title_height: int = 80,
                                   extra_height: int = 50) -> Tuple[int, int]:
        """计算图片尺寸
        
        Args:
            content_lines: 内容行列表
            title_height: 标题高度
            extra_height: 额外高度
        
        Returns:
            (宽度, 高度)
        """
        margin = self.LAYOUT["margin"]
        line_height = self.LAYOUT["line_height"]
        width = self.LAYOUT["width"]
        
        total_height = margin * 2 + title_height + \
                      len(content_lines) * line_height + extra_height
        
        return (width, total_height)
