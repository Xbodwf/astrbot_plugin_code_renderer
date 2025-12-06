import os
import re
import json
import uuid
import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer, get_lexer_for_filename
from pygments.formatters import ImageFormatter
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.message_components import Image as ImageComponent, Plain, Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


# 字体回退列表（按优先级排序）
FONT_FALLBACK_LIST = [
    "JetBrains Mono",
    "Consolas",
    "Fira Code",
    "Source Code Pro",
    "Monaco",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Courier New",
    "monospace",
]

# 主题配置
THEMES = {
    "monokai": {
        "style": "monokai",
        "background": "#272822",
        "line_number_bg": "#3e3d32",
        "line_number_fg": "#8f908a"
    },
    "dracula": {
        "style": "dracula",
        "background": "#282a36",
        "line_number_bg": "#21222c",
        "line_number_fg": "#6272a4"
    },
    "github-dark": {
        "style": "github-dark",
        "background": "#0d1117",
        "line_number_bg": "#161b22",
        "line_number_fg": "#484f58"
    },
    "one-dark": {
        "style": "one-dark",
        "background": "#282c34",
        "line_number_bg": "#21252b",
        "line_number_fg": "#636d83"
    },
    "vs-dark": {
        "style": "vs",
        "background": "#1e1e1e",
        "line_number_bg": "#252526",
        "line_number_fg": "#858585"
    },
    "nord": {
        "style": "nord",
        "background": "#2e3440",
        "line_number_bg": "#3b4252",
        "line_number_fg": "#616e88"
    }
}


def _find_available_font(font_name: str, font_size: int = 14) -> str:
    """查找可用字体，如果指定字体不可用则使用回退列表
    
    Args:
        font_name: 首选字体名称
        font_size: 字体大小（用于测试）
    
    Returns:
        可用的字体名称
    """
    # 首先尝试用户指定的字体
    fonts_to_try = [font_name] if font_name else []
    # 添加回退列表
    fonts_to_try.extend(FONT_FALLBACK_LIST)
    
    for font in fonts_to_try:
        try:
            # 尝试创建 ImageFormatter 来验证字体可用性
            test_formatter = ImageFormatter(font_name=font, font_size=font_size)
            # 如果没有抛出异常，字体可用
            logger.debug(f"使用字体: {font}")
            return font
        except Exception as e:
            logger.debug(f"字体 {font} 不可用: {e}")
            continue
    
    # 如果所有字体都不可用，返回 None 让 pygments 使用默认字体
    logger.warning("所有字体都不可用，使用 pygments 默认字体")
    return None


@register("code_render", "memsys_lizi", "将代码渲染为精美图片并发送给用户", "1.0.0")
class CodeRenderPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self.languages = {}
        self.temp_dir = os.path.join(get_astrbot_data_path(), "temp", "code_render")
        self._cached_font = None  # 缓存可用字体

    async def initialize(self):
        """插件初始化"""
        # 加载语言配置
        self._load_languages()
        
        # 创建临时目录
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # 启动时清理临时文件
        await self._cleanup_temp_files()
        
        # 启动定期清理任务
        asyncio.create_task(self._periodic_cleanup())
        
        logger.info(f"代码预览器插件已初始化，支持 {len(self.languages)} 种语言")

    def _load_languages(self):
        """加载语言配置文件"""
        plugin_dir = Path(__file__).parent
        lang_file = plugin_dir / "languages.json"
        
        # 加载用户自定义语言配置
        custom_lang_file = plugin_dir / "custom_languages.json"
        
        try:
            with open(lang_file, "r", encoding="utf-8") as f:
                self.languages = json.load(f)
                # 移除注释字段
                self.languages.pop("_comment", None)
        except Exception as e:
            logger.error(f"加载语言配置失败: {e}")
            self.languages = {}
        
        # 加载自定义语言配置（如果存在）
        if custom_lang_file.exists():
            try:
                with open(custom_lang_file, "r", encoding="utf-8") as f:
                    custom_langs = json.load(f)
                    custom_langs.pop("_comment", None)
                    self.languages.update(custom_langs)
                    logger.info(f"已加载自定义语言配置: {len(custom_langs)} 种")
            except Exception as e:
                logger.warning(f"加载自定义语言配置失败: {e}")

    async def _cleanup_temp_files(self):
        """清理临时文件"""
        if not os.path.exists(self.temp_dir):
            return
        
        count = 0
        for filename in os.listdir(self.temp_dir):
            file_path = os.path.join(self.temp_dir, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    count += 1
            except Exception as e:
                logger.warning(f"删除临时文件失败 {filename}: {e}")
        
        if count > 0:
            logger.info(f"已清理 {count} 个临时文件")

    async def _periodic_cleanup(self):
        """定期清理超过1小时的临时文件"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时检查一次
                await self._cleanup_temp_files()
            except Exception as e:
                logger.error(f"定期清理临时文件时出错: {e}")

    def _is_group_blocked(self, event: AstrMessageEvent) -> bool:
        """检查当前群是否在黑名单中"""
        if not self.config:
            return False
        
        session_id = event.session_id
        if not session_id:
            return False
        
        blacklist = self.config.get("blacklist", [])
        return session_id in blacklist

    def _detect_language(self, code: str, hint: str = None, filename: str = None) -> str:
        """检测代码语言"""
        # 如果提供了语言提示
        if hint:
            hint_lower = hint.lower().strip()
            # 直接匹配语言名
            if hint_lower in self.languages:
                return hint_lower
            # 匹配别名
            for lang, info in self.languages.items():
                if hint_lower in info.get("aliases", []):
                    return lang
        
        # 如果提供了文件名，根据扩展名判断
        if filename:
            ext = os.path.splitext(filename)[1].lower()
            for lang, info in self.languages.items():
                if ext in info.get("extensions", []):
                    return lang
        
        # 使用 pygments 猜测语言
        try:
            lexer = guess_lexer(code)
            lexer_name = lexer.name.lower()
            # 尝试匹配
            for lang in self.languages:
                if lang in lexer_name or lexer_name in lang:
                    return lang
            return lexer.aliases[0] if lexer.aliases else "text"
        except ClassNotFound:
            return "text"

    def _get_lexer(self, language: str, code: str):
        """获取语法高亮器"""
        # 检查是否有自定义的 pygments_lexer 映射
        lang_config = self.languages.get(language, {})
        lexer_name = lang_config.get("pygments_lexer", language)
        
        try:
            return get_lexer_by_name(lexer_name, stripall=True)
        except ClassNotFound:
            # 如果指定的 lexer 不存在，尝试原语言名
            if lexer_name != language:
                try:
                    return get_lexer_by_name(language, stripall=True)
                except ClassNotFound:
                    pass
            # 尝试猜测
            try:
                return guess_lexer(code)
            except ClassNotFound:
                return get_lexer_by_name("text", stripall=True)

    def _extract_code_from_message(self, text: str) -> tuple[str, str]:
        """从消息中提取代码和语言提示
        
        Returns:
            (code, language_hint)
        """
        # 匹配 markdown 代码块 ```language\ncode```
        code_block_pattern = r'```(\w*)\n?([\s\S]*?)```'
        match = re.search(code_block_pattern, text)
        if match:
            lang_hint = match.group(1) or None
            code = match.group(2).strip()
            return code, lang_hint
        
        # 匹配单行代码 `code`
        inline_code_pattern = r'`([^`]+)`'
        match = re.search(inline_code_pattern, text)
        if match:
            return match.group(1), None
        
        # 没有代码块标记，返回原文本
        return text.strip(), None

    def _render_code_to_image(
        self, 
        code: str, 
        language: str,
        theme_override: str = None,
        font_size_override: int = None,
        line_numbers_override: bool = None
    ) -> str:
        """将代码渲染为图片
        
        Args:
            code: 代码内容
            language: 语言标识
            theme_override: 覆盖默认主题
            font_size_override: 覆盖默认字体大小
            line_numbers_override: 覆盖默认行号显示设置
        
        Returns:
            图片文件路径
        """
        # 获取配置（支持参数覆盖）
        theme_name = theme_override or (self.config.get("theme", "monokai") if self.config else "monokai")
        font_family = self.config.get("font_family", "JetBrains Mono") if self.config else "JetBrains Mono"
        font_size = font_size_override or (self.config.get("font_size", 14) if self.config else 14)
        show_line_numbers = line_numbers_override if line_numbers_override is not None else (self.config.get("line_numbers", True) if self.config else True)
        padding = self.config.get("padding", 20) if self.config else 20
        max_lines = self.config.get("max_lines", 100) if self.config else 100
        
        # 查找可用字体（使用缓存）
        if self._cached_font is None:
            self._cached_font = _find_available_font(font_family, font_size)
        actual_font = self._cached_font
        
        # 限制代码行数
        lines = code.split('\n')
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append(f"... (省略了 {len(code.split(chr(10))) - max_lines} 行)")
            code = '\n'.join(lines)
        
        # 获取主题配置
        theme_config = THEMES.get(theme_name, THEMES["monokai"])
        
        # 获取 lexer
        lexer = self._get_lexer(language, code)
        
        # 尝试获取样式
        try:
            style = get_style_by_name(theme_config["style"])
        except ClassNotFound:
            style = get_style_by_name("monokai")
        
        # 创建 ImageFormatter（使用回退后的字体）
        formatter = ImageFormatter(
            style=style,
            font_name=actual_font,
            font_size=font_size,
            line_numbers=show_line_numbers,
            line_number_bg=theme_config.get("line_number_bg", "#3e3d32"),
            line_number_fg=theme_config.get("line_number_fg", "#8f908a"),
            line_number_pad=10,
            image_pad=padding,
        )
        
        # 渲染代码
        result = highlight(code, lexer, formatter)
        
        # 保存图片
        filename = f"{uuid.uuid4().hex}.png"
        file_path = os.path.join(self.temp_dir, filename)
        
        with open(file_path, "wb") as f:
            f.write(result)
        
        return file_path

    def _parse_render_args(self, args_str: str) -> dict:
        """解析渲染参数
        
        支持的参数格式:
        - lang=python 或 -l python
        - theme=dracula 或 -t dracula  
        - size=16 或 -s 16
        - noline 或 -n (不显示行号)
        - line 或 -ln (显示行号)
        
        Returns:
            解析后的参数字典
        """
        result = {
            "language": None,
            "theme": None,
            "font_size": None,
            "line_numbers": None,
            "remaining": ""  # 剩余的代码内容
        }
        
        if not args_str:
            return result
        
        parts = args_str.split()
        remaining_parts = []
        i = 0
        
        while i < len(parts):
            part = parts[i]
            
            # 解析 lang= 或 -l
            if part.startswith("lang="):
                result["language"] = part[5:]
            elif part == "-l" and i + 1 < len(parts):
                i += 1
                result["language"] = parts[i]
            # 解析 theme= 或 -t
            elif part.startswith("theme="):
                result["theme"] = part[6:]
            elif part == "-t" and i + 1 < len(parts):
                i += 1
                result["theme"] = parts[i]
            # 解析 size= 或 -s
            elif part.startswith("size="):
                try:
                    result["font_size"] = int(part[5:])
                except ValueError:
                    pass
            elif part == "-s" and i + 1 < len(parts):
                i += 1
                try:
                    result["font_size"] = int(parts[i])
                except ValueError:
                    pass
            # 解析行号开关
            elif part in ("noline", "-n", "--no-line"):
                result["line_numbers"] = False
            elif part in ("line", "-ln", "--line"):
                result["line_numbers"] = True
            else:
                # 不是参数，加入剩余部分
                remaining_parts.append(part)
            
            i += 1
        
        result["remaining"] = " ".join(remaining_parts)
        return result

    @filter.command("render")
    async def render_code(
        self, 
        event: AstrMessageEvent, 
        language: str = "",
        theme: str = "",
        size: int = 0,
        noline: str = ""
    ):
        """渲染代码为图片。
        
        用法: /render [参数] [代码]
        
        参数:
        - language 或 -l: 指定语言 (如 python, js)
        - theme 或 -t: 指定主题 (monokai, dracula, github-dark, one-dark, vs-dark, nord)
        - size 或 -s: 指定字体大小
        - noline 或 -n: 不显示行号
        - line 或 -ln: 显示行号
        
        示例:
        /render -l python -t dracula print("hello")
        /render lang=js theme=nord console.log("hi")
        """
        # 检查黑名单
        if self._is_group_blocked(event):
            return
        
        # 解析原始消息获取参数
        message_text = event.message_str
        # 移除命令前缀
        if message_text.startswith("/render"):
            message_text = message_text[7:].strip()
        
        # 解析参数
        parsed = self._parse_render_args(message_text)
        
        # 合并参数（命令行参数优先级高于解析的参数）
        final_language = language if language else parsed["language"]
        final_theme = theme if theme else parsed["theme"]
        final_size = size if size > 0 else parsed["font_size"]
        final_line_numbers = False if noline.lower() in ("true", "1", "yes") else parsed["line_numbers"]
        
        # 验证主题
        if final_theme and final_theme not in THEMES:
            available_themes = ", ".join(THEMES.keys())
            yield event.plain_result(f"❌ 不支持的主题: {final_theme}\n可用主题: {available_themes}")
            return
        
        # 获取引用的消息
        reply_message = None
        
        # 尝试获取引用消息的内容
        messages = event.get_messages()
        if messages and isinstance(messages[0], Reply):
            reply_seg = messages[0]
            # 从 Reply 的 chain 属性获取原始消息内容
            if hasattr(reply_seg, 'chain') and reply_seg.chain:
                # chain 是消息链，提取文本内容
                reply_message = ''.join(
                    str(seg) if isinstance(seg, Plain) else getattr(seg, 'text', str(seg))
                    for seg in reply_seg.chain
                    if isinstance(seg, Plain) or hasattr(seg, 'text')
                )
            elif hasattr(reply_seg, 'origin') and reply_seg.origin:
                reply_message = str(reply_seg.origin)
        
        # 如果没有引用消息，使用解析后的剩余内容
        if not reply_message:
            if parsed["remaining"]:
                reply_message = parsed["remaining"]
            else:
                yield event.plain_result(
                    "❌ 请引用一条包含代码的消息，或直接在命令后附带代码。\n\n"
                    "用法: /render [参数] [代码]\n\n"
                    "参数:\n"
                    "• -l <语言> 或 lang=<语言>\n"
                    "• -t <主题> 或 theme=<主题>\n"
                    "• -s <字号> 或 size=<字号>\n"
                    "• -n 或 noline (不显示行号)\n\n"
                    "示例: /render -l python -t dracula print('hello')"
                )
                return
        
        # 提取代码
        code, detected_lang = self._extract_code_from_message(reply_message)
        
        if not code or len(code.strip()) == 0:
            yield event.plain_result("❌ 未能从消息中提取到代码")
            return
        
        # 确定语言
        if not final_language:
            final_language = detected_lang
        if not final_language:
            final_language = self._detect_language(code)
        
        # 获取语言显示名称
        lang_display = self.languages.get(final_language, {}).get("display_name", final_language)
        theme_display = final_theme or (self.config.get("theme", "monokai") if self.config else "monokai")
        
        try:
            # 发送处理中提示
            yield event.plain_result(f"🎨 正在渲染 {lang_display} 代码 (主题: {theme_display})...")
            
            # 渲染代码
            image_path = self._render_code_to_image(
                code, 
                final_language,
                theme_override=final_theme,
                font_size_override=final_size,
                line_numbers_override=final_line_numbers
            )
            
            if not os.path.exists(image_path):
                yield event.plain_result("❌ 渲染失败：图片生成失败")
                return
            
            # 发送图片
            result = MessageEventResult()
            result.chain.append(ImageComponent(file=image_path))
            
            yield result
            
            logger.info(f"代码渲染成功: {lang_display}, 主题: {theme_display}, {len(code)} 字符")
            
        except Exception as e:
            logger.error(f"渲染代码时发生错误: {e}")
            yield event.plain_result(f"❌ 渲染失败: {str(e)}")

    @filter.command("render_themes")
    async def list_themes(self, event: AstrMessageEvent):
        """列出支持的代码主题"""
        if self._is_group_blocked(event):
            return
        
        lines = ["🎨 支持的代码主题:\n"]
        for theme_name in THEMES.keys():
            lines.append(f"• {theme_name}")
        
        lines.append("\n💡 使用 /render -t <主题名> 指定主题")
        
        yield event.plain_result("\n".join(lines))

    @filter.command("render_file")
    async def render_file(self, event: AstrMessageEvent):
        """渲染代码文件为图片。用法: 引用文件消息后发送 /render_file"""
        # 检查黑名单
        if self._is_group_blocked(event):
            return
        
        yield event.plain_result("❌ 文件渲染功能暂未实现，请直接发送代码文本并使用 /render 命令")

    @filter.command("render_langs")
    async def list_languages(self, event: AstrMessageEvent):
        """列出支持的编程语言"""
        if self._is_group_blocked(event):
            return
        
        # 按字母排序
        sorted_langs = sorted(self.languages.items(), key=lambda x: x[0])
        
        # 分组显示
        lines = ["📋 支持的编程语言:\n"]
        for lang, info in sorted_langs:
            display_name = info.get("display_name", lang)
            aliases = info.get("aliases", [])
            alias_str = f" ({', '.join(aliases)})" if aliases else ""
            lines.append(f"• {display_name}{alias_str}")
        
        lines.append(f"\n共 {len(self.languages)} 种语言")
        lines.append("💡 可在 custom_languages.json 中添加更多语言")
        
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        """插件销毁时清理"""
        await self._cleanup_temp_files()
