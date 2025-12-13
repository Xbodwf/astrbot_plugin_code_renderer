import os
import re
import json
import uuid
import asyncio
import platform
import subprocess
import html
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageColor
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer, get_lexer_for_filename
from pygments.formatters import ImageFormatter, HtmlFormatter
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from pygments.lexer import RegexLexer, bygroups, include, words
from pygments.token import Text, Comment, Operator, Keyword, Name, String, Number, Punctuation, Token

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.message_components import Image as ImageComponent, Plain, Reply, File
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from playwright.async_api import async_playwright


@register("astrbot_plugin_code_renderer", "Xbodw", "将代码信息或者代码文件渲染为图片", "1.3.3")
class CodeRenderPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self.languages = {}
        self.temp_dir = os.path.join(get_astrbot_data_path(), "temp", "code_render")
        self._cached_font = None  # 缓存可用字体
        self._playwright = None   # 全局 Playwright 实例
        self._browser = None      # 共享浏览器实例

    async def initialize(self):
        """插件初始化"""
        # 加载语言配置
        self._load_languages()
        
        # 创建临时目录
        os.makedirs(self.temp_dir, exist_ok=True)

        # 确保 Playwright 浏览器可用
        await self._ensure_playwright_browser()

        # 启动共享 Playwright 浏览器实例
        try:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            if self._browser is None:
                self._browser = await self._playwright.chromium.launch(headless=True)
                logger.info("CodeRender Playwright 浏览器已启动")
        except Exception as e:
            logger.error(f"启动 Playwright 浏览器失败: {e}")

        # 启动时清理临时文件
        await self._cleanup_temp_files()

        # 启动定期清理任务
        asyncio.create_task(self._periodic_cleanup())
        
        logger.info(f"代码预览器插件已初始化，支持 {len(self.languages)} 种语言")

    def _find_cjk_font(self, font_size: int):
        """跨平台寻找可用的 CJK 字体"""
        system = platform.system()
        font_names = []
        font_dirs = []
        
        if system == "Windows":
            font_names = ["msyh.ttc", "simhei.ttf", "simsun.ttc", "msjh.ttc"]
            font_dirs = [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")]
        elif system == "Darwin": # macOS
            font_names = ["PingFang.ttc", "STHeiti Light.ttc", "STHeiti Medium.ttc"]
            font_dirs = ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
        else: # Linux / Other
            # 优先尝试使用 fc-match (fontconfig)
            try:
                # 查找支持中文的字体文件路径
                output = subprocess.check_output(['fc-match', '-f', '%{file}', ':lang=zh'], stderr=subprocess.DEVNULL).decode().strip()
                if output and os.path.exists(output):
                    try:
                        return ImageFont.truetype(output, font_size)
                    except Exception:
                        pass
            except Exception:
                pass

            # 常见 Linux CJK 字体
            font_names = [
                "NotoSansSC-Regular.otf", "NotoSansCJK-Regular.ttc", 
                "wqy-microhei.ttc", "wqy-zenhei.ttc",
                "DroidSansFallback.ttf", "uming.ttc", "ukai.ttc"
            ]
            font_dirs = [
                "/usr/share/fonts", 
                "/usr/local/share/fonts", 
                os.path.expanduser("~/.fonts"),
                os.path.expanduser("~/.local/share/fonts")
            ]

        # 遍历目录查找
        for directory in font_dirs:
            if not os.path.exists(directory):
                continue
            for root, dirs, files in os.walk(directory):
                for filename in files:
                    # 精确匹配
                    if filename in font_names:
                        try:
                            return ImageFont.truetype(os.path.join(root, filename), font_size)
                        except Exception:
                            continue
                    # 模糊匹配 Noto Sans CJK
                    if "NotoSansSC" in filename and filename.endswith((".otf", ".ttf", ".ttc")):
                        try:
                            return ImageFont.truetype(os.path.join(root, filename), font_size)
                        except Exception:
                            continue

        # 回退：尝试直接加载名称 (依赖系统路径配置)
        for name in font_names:
            try:
                return ImageFont.truetype(name, font_size)
            except Exception:
                continue
                
        return None

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
                await asyncio.sleep(900)  # 每15min检查一次
                await self._cleanup_temp_files()
            except Exception as e:
                logger.error(f"定期清理临时文件时出错: {e}")

    async def _ensure_playwright_browser(self):
        """确保 Playwright 浏览器已安装并可用"""
        try:
            from playwright.async_api import async_playwright as _ap

            async with _ap() as p:
                try:
                    browser = await p.chromium.launch(headless=True)
                    await browser.close()
                    logger.info("Playwright Chromium 浏览器已就绪")
                except Exception as e:
                    logger.warning("Playwright 浏览器未安装或不可用，尝试自动安装 Chromium ...")
                    import subprocess as _sub
                    result = _sub.run([
                        "playwright",
                        "install",
                        "chromium",
                    ], capture_output=True, text=True)
                    if result.returncode == 0:
                        logger.info("Playwright Chromium 安装成功")
                    else:
                        logger.error(f"Playwright 浏览器自动安装失败: {result.stderr}")
                        logger.error("请手动运行: playwright install chromium")
        except Exception as e:
            logger.error(f"检查 Playwright 浏览器时出错: {e}")
            logger.error("如首次使用，请在命令行手动运行: playwright install chromium")

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
            
            # 1. 尝试匹配 lexer 名称到配置的 key
            lexer_name = lexer.name.lower()
            if lexer_name in self.languages:
                return lexer_name
            
            # 2. 检查 lexer 名称是否匹配配置中的别名
            for lang, info in self.languages.items():
                if lexer_name in info.get("aliases", []):
                    return lang

            # 3. 尝试匹配 lexer 别名到配置的 key 或别名
            if lexer.aliases:
                for alias in lexer.aliases:
                    alias_lower = alias.lower()
                    # 检查 key
                    if alias_lower in self.languages:
                        return alias_lower
                    # 检查配置中的别名
                    for lang, info in self.languages.items():
                        if alias_lower in info.get("aliases", []):
                            return lang
            
            # 4. 如果没找到匹配的配置，返回 pygments 的第一个别名
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

    async def _render_code_to_image(
        self,
        code: str,
        language: str,
        theme_override: str = None,
        font_size_override: int = None,
        line_numbers_override: bool = None,
    ) -> str:
        """使用 Playwright + 本地 highlight.js 模板渲染代码为图片"""
        theme_name = theme_override or (self.config.get("default_theme", "github-dark") if self.config else "github-dark")
        font_size = font_size_override or (self.config.get("font_size", 14) if self.config else 14)
        plugin_dir = os.path.dirname(__file__)

        # 字体配置：config.font_path > 插件内 JetBrainsMono-Regular.ttf > 浏览器系统字体
        font_path = None
        if self.config and self.config.get("font_path"):
            font_path = self.config.get("font_path")
        else:
            default_font_path = os.path.join(plugin_dir, "fonts", "ttf", "JetBrainsMono-Regular.ttf")
            if os.path.exists(default_font_path):
                font_path = default_font_path

        font_family = (
            self.config.get("font_family") if self.config and self.config.get("font_family")
            else "JetBrains Mono, Consolas, Fira Code, Source Code Pro, monospace"
        )

        font_face_css = ""
        if font_path and os.path.exists(font_path):
            font_url = "file://" + font_path.replace("\\", "/")
            font_face_css = f"""
        @font-face {{
            font-family: 'CodeRenderFont';
            src: url('{font_url}');
            font-weight: normal;
            font-style: normal;
        }}
        """
            font_family = "CodeRenderFont"

        # highlight.js 与主题 CSS 路径（可通过配置覆盖）
        if self.config and self.config.get("highlight_js_path"):
            hljs_path = self.config.get("highlight_js_path")
        else:
            # 默认使用插件目录下解压的 highlight.min.js
            hljs_path = os.path.join(plugin_dir, "assets", "highlight", "highlight.min.js")

        if self.config and self.config.get("highlight_css_path"):
            hljs_css_path = self.config.get("highlight_css_path")
        else:
            # 根据主题名自动匹配 styles 目录下的 CSS 文件，例如 monokai -> monokai.min.css
            css_filename = f"{theme_name}.min.css"
            hljs_css_path = os.path.join(plugin_dir, "assets", "highlight", "styles", css_filename)

        # 读取主题 CSS，如果存在则使用；否则使用内置深色主题作为回退
        hljs_theme_css = ""
        try:
            if os.path.exists(hljs_css_path):
                with open(hljs_css_path, "r", encoding="utf-8") as f:
                    hljs_theme_css = f.read()
        except Exception as e:
            logger.error(f"读取 highlight.js 主题 CSS 失败: {e}")
            hljs_theme_css = ""

        # 读取 highlight.js 源码内联到页面中，避免 file:// 外链脚本不执行
        hljs_source = ""
        try:
            with open(hljs_path, "r", encoding="utf-8") as f:
                hljs_source = f.read()
        except Exception as e:
            logger.error(f"读取 highlight.js 失败: {e}")
            hljs_source = ""

        # 为 Ljos 语言追加自定义 highlight.js 语言定义
        ljos_hljs_def = r"""
; (function() {
    function ljosLanguage(hljs) {
        const KEYWORDS = {
            keyword:
                'mut const readonly public private protected static abstract final override ' +
                'if else for while do when break continue return throw try catch finally ' +
                'fn type where go defer move borrow using macro async await yield ' +
                'class interface enum extends implements constructor new this super import export default',
            literal:
                'nul true false',
            type:
                'int float str bool bytes'
        };

        return {
            name: 'Ljos',
            aliases: ['lj'],
            keywords: KEYWORDS,
            contains: [
                hljs.C_LINE_COMMENT_MODE,
                hljs.C_BLOCK_COMMENT_MODE,
                {
                    className: 'string',
                    variants: [
                        hljs.QUOTE_DOUBLE_MODE,
                        {
                            begin: '`', end: '`'
                        }
                    ]
                },
                {
                    className: 'number',
                    variants: [
                        { begin: /0[bB][01]([01_]*[01])?\b/ },
                        { begin: /0[oO][0-7]([0-7_]*[0-7])?\b/ },
                        { begin: /0[xX][0-9A-Fa-f]([0-9A-Fa-f_]*[0-9A-Fa-f])?\b/ },
                        { begin: /[0-9]([0-9_]*[0-9])?\.[0-9]([0-9_]*[0-9])?([eE][+-]?[0-9]([0-9_]*[0-9])?)?\b/ },
                        { begin: /[0-9]([0-9_]*[0-9])?\b/ }
                    ],
                    relevance: 0
                },
                {
                    className: 'meta',
                    begin: '@[A-Za-z_][A-Za-z0-9_]*'
                },
                {
                    className: 'function',
                    beginKeywords: 'fn',
                    end: /\(/,
                    excludeEnd: true,
                    contains: [hljs.inherit(hljs.TITLE_MODE, { begin: /[A-Za-z_][A-Za-z0-9_]*/ })]
                },
                {
                    className: 'class',
                    beginKeywords: 'class interface enum',
                    end: /\{/,
                    excludeEnd: true,
                    contains: [hljs.inherit(hljs.TITLE_MODE, { begin: /[A-Z][A-Za-z0-9_]*/ })]
                }
            ]
        };
    }

    if (typeof window !== 'undefined' && window.hljs && !window.hljs.getLanguage('ljos')) {
        window.hljs.registerLanguage('ljos', ljosLanguage);
    }
})();
"""

        # 避免内联脚本中出现 </script> 终止标签
        full_hljs_source = (hljs_source or '') + ljos_hljs_def
        hljs_inline = full_hljs_source.replace("</script>", "<\\/script>") if full_hljs_source else ""

        # 将代码安全转义后塞进 template
        escaped_code = html.escape(code)
        language_class = language or "plaintext"

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <style>
    {font_face_css}
    body {{
        margin: 0;
        padding: 20px;
        background: #1e1e1e;
    }}
    pre {{
        margin: 0;
        font-size: {font_size}px;
        line-height: 1.5;
        font-family: {font_family};
    }}
    .code-container {{
        display: inline-block;
        padding: 16px 20px;
        border-radius: 12px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.4);
        max-width: 1100px;
        overflow: auto;
    }}
    {hljs_theme_css}
    </style>
    <script>{hljs_inline}</script>
    <script>
    // 等待 highlight.js 加载完成后再执行高亮，避免 set_content 时机问题
    (function () {{
        function applyHighlight() {{
            const blocks = document.querySelectorAll('pre code');
            for (const block of blocks) {{
                try {{
                    window.hljs && window.hljs.highlightElement(block);
                }} catch (e) {{
                    console.error('highlight.js error', e);
                }}
            }}
        }}

        function waitForHLJS(retry) {{
            retry = retry || 0;
            if (window.hljs && typeof window.hljs.highlightElement === 'function') {{
                applyHighlight();
            }} else if (retry < 100) {{
                setTimeout(function () {{ waitForHLJS(retry + 1); }}, 50);
            }} else {{
                console.warn('highlight.js not available after waiting');
            }}
        }}

        if (document.readyState === 'complete' || document.readyState === 'interactive') {{
            waitForHLJS(0);
        }} else {{
            document.addEventListener('DOMContentLoaded', function () {{ waitForHLJS(0); }});
        }}
    }})();
    </script>
</head>
<body>
    <div class="code-container">
        <pre><code class="hljs language-{language_class}">{escaped_code}</code></pre>
    </div>
</body>
</html>
"""

        filename = f"{uuid.uuid4().hex}.png"
        file_path = os.path.join(self.temp_dir, filename)

        await self._ensure_playwright_browser()

        # 使用共享浏览器实例渲染截图
        if not self._browser:
            # 如果由于某些原因浏览器未启动，尝试补救启动一次
            try:
                if not self._playwright:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
                logger.info("CodeRender Playwright 浏览器在渲染时重新启动")
            except Exception as e:
                logger.error(f"渲染时启动 Playwright 浏览器失败: {e}")
                raise

        page = await self._browser.new_page(viewport={"width": 1200, "height": 800})
        await page.set_content(html_content, wait_until="networkidle")
        await page.screenshot(path=file_path, full_page=True)
        await page.close()

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
        
        # 解析原始消息获取参数（支持多行：从第二行开始解析参数）
        raw_text = event.message_str or ""
        lines = raw_text.splitlines()

        message_text = ""
        if lines:
            # 仅使用第二行及之后的内容作为参数区，第一行只保留命令本身
            rest_lines = lines[1:]
            if rest_lines:
                message_text = " ".join(l.strip() for l in rest_lines if l.strip())
        
        # 解析参数
        parsed = self._parse_render_args(message_text)
        
        # 合并参数（完全依赖消息文本动态解析）
        final_language = parsed["language"]
        final_theme = parsed["theme"]
        final_size = parsed["font_size"]
        final_line_numbers = parsed["line_numbers"]
        
        # 获取引用的消息
        reply_message = None
        
        # 尝试获取引用消息的内容
        messages = event.get_messages()
        if messages and isinstance(messages[0], Reply):
            reply_seg = messages[0]
            reply_content = ""
            
            # 1. 尝试从 chain 获取
            if hasattr(reply_seg, 'chain') and reply_seg.chain:
                for seg in reply_seg.chain:
                    if isinstance(seg, Plain):
                        reply_content += seg.text
                    elif hasattr(seg, 'text'):
                        reply_content += str(seg.text)
            
            # 2. 如果 chain 为空或提取失败，尝试 message_str
            if not reply_content and hasattr(reply_seg, 'message_str') and reply_seg.message_str:
                reply_content = reply_seg.message_str
                
            # 3. 尝试 origin (旧版兼容)
            if not reply_content and hasattr(reply_seg, 'origin') and reply_seg.origin:
                reply_content = str(reply_seg.origin)
            
            reply_message = reply_content
        
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
            # yield event.plain_result(f"🎨 正在渲染 {lang_display} 代码 (主题: {theme_display})...")
            
            # 渲染代码
            image_path = await self._render_code_to_image(
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


    @filter.command("render_file")
    async def render_file(
        self, 
        event: AstrMessageEvent,
    ):
        """渲染代码文件为图片。
        
        用法: 引用文件消息后发送 /render_file [参数]
        
        参数:
        - theme 或 -t: 指定主题
        - size 或 -s: 指定字体大小
        - noline 或 -n: 不显示行号
        - language 或 -l: 强制指定语言（通常不需要，会自动根据文件名识别）
        """
        # 检查黑名单
        if self._is_group_blocked(event):
            return

        # 解析原始消息获取参数 (支持多行，从第二行开始解析参数)
        raw_text = event.message_str or ""
        lines = raw_text.splitlines()

        message_text = ""
        if lines:
            # 仅使用第二行及之后的内容作为参数区，第一行只保留命令本身
            rest_lines = lines[1:]
            if rest_lines:
                message_text = " ".join(l.strip() for l in rest_lines if l.strip())

        parsed = self._parse_render_args(message_text)
        
        # 合并参数（完全依赖消息文本动态解析）
        final_language = parsed["language"]
        final_theme = parsed["theme"]
        final_size = parsed["font_size"]
        final_line_numbers = parsed["line_numbers"]

        # 获取引用的消息中的文件
        target_file = None
        file_name = ""
        
        messages = event.get_messages()
        if messages and isinstance(messages[0], Reply):
            reply_seg = messages[0]
            if hasattr(reply_seg, 'chain') and reply_seg.chain:
                for seg in reply_seg.chain:
                    if isinstance(seg, File):
                        target_file = seg
                        file_name = seg.name or "unknown"
                        break
        
        if not target_file:
            yield event.plain_result("❌ 请引用一条包含文件的消息")
            return

        try:
            # 获取文件路径 (get_file 会自动下载)
            file_path = await target_file.get_file()
            
            if not file_path or not os.path.exists(file_path):
                yield event.plain_result("❌ 文件获取失败")
                return
                
            # 读取文件内容
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    code = f.read()
            except UnicodeDecodeError:
                # 尝试其他编码
                try:
                    with open(file_path, "r", encoding="gbk") as f:
                        code = f.read()
                except Exception:
                    yield event.plain_result("❌ 文件编码不支持 (仅支持 UTF-8 和 GBK)")
                    return
            except Exception as e:
                yield event.plain_result(f"❌ 读取文件失败: {str(e)}")
                return
            
            if not code or len(code.strip()) == 0:
                yield event.plain_result("❌ 文件内容为空")
                return

            # 确定语言 (优先使用强制指定的，否则根据文件名检测)
            if not final_language:
                final_language = self._detect_language(code, filename=file_name)
            
            # 获取显示名称
            lang_display = self.languages.get(final_language, {}).get("display_name", final_language)
            theme_display = final_theme or (self.config.get("theme", "monokai") if self.config else "monokai")

            # 渲染
            image_path = await self._render_code_to_image(
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
            
            logger.info(f"文件渲染成功: {file_name}, 语言: {lang_display}, 主题: {theme_display}")
            
        except Exception as e:
            logger.error(f"处理文件渲染时发生错误: {e}")
            yield event.plain_result(f"❌ 处理失败: {str(e)}")

    async def terminate(self):
        """插件销毁时清理"""
        # 先清理临时文件
        await self._cleanup_temp_files()

        # 关闭 Playwright 浏览器
        try:
            if self._browser:
                await self._browser.close()
                self._browser = None
                logger.info("CodeRender Playwright 浏览器已关闭")
        except Exception as e:
            logger.error(f"关闭 Playwright 浏览器时出错: {e}")

        try:
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
                logger.info("CodeRender Playwright 实例已停止")
        except Exception as e:
            logger.error(f"停止 Playwright 实例时出错: {e}")