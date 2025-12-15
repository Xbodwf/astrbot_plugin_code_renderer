# 代码预览器插件

将代码渲染为精美图片并发送给用户。

## 功能特性

- 主流编程语言的语法高亮
- 多种代码主题可选
- 支持自定义字体、字体大小等
- 自动检测代码语言

使用`Highlight.js` + `highlight-js-line-number`
(Playwright)

- **支持参数指定渲染选项**

## 使用方法

### 渲染代码

```
/render
[参数]
```

引用一条包含代码的消息，然后发送 `/render`

```
/render_file
[参数]
```

引用一个代码文件，然后发送 `/render_file`
参数同render


### 支持的参数

| 使用 | 说明 | 示例 |
|------|------|------|
| `-l <语言>` | 指定代码语言(自动识别) | `-l python` |
| `-t <主题>` | 指定渲染主题 | `-t dracula` |
| `-s <字号>` | 指定字体大小 | `-s 16` |
| `-ln` | 显式配置启用行号 | `-ln` |
| `-n` | 显式配置禁用行号 | `-n` |
### 示例

使用 Markdown 代码块：

```
/render -t github-dark
​```python
def hello():
    print("Hello, World!")
​```

# 即使不填python,也会自动识别。识别的精准度取决于代码内的语言特性占比。
```

## 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| theme | 代码主题 | monokai |
| font_family | 字体名称 | Jetbrains Mono |
| font_size | 字体大小 | 14 |
| padding | 内边距 | 20 |
| blacklist | 群聊黑名单 | [] |

### 支持的主题

浏览仓库目录/assets/highlight/styles即可。

# 自定义语言配置说明

## 概述

此插件支持通过 JSON 文件定义自定义语言，并自动注册到 highlight.js 中进行语法高亮。

## 配置文件位置

将自定义语言的 JSON 文件放置在插件根目录的 `languages/` 文件夹中。文件名（不含扩展名）将作为语言标识符。

例如：`languages/mylang.json` 将注册为 `mylang` 语言。

## JSON 结构

### 必需字段

- **name** (string): 语言的显示名称
- **aliases** (array): 语言的别名列表
- **extensions** (array): 该语言的文件扩展名列表

### 可选字段

#### keywords (object | array)

定义语言的关键字。可以是简单的数组，或按类别分组的对象。

**简单数组形式：**
```json
"keywords": ["if", "else", "for", "while", "return"]
```

**分类对象形式：**
```json
"keywords": {
  "keyword": ["if", "else", "for", "while"],
  "type": ["int", "float", "string", "bool"],
  "literal": ["true", "false", "null"],
  "built_in": ["print", "console", "Math"]
}
```

#### strings (object)

配置字符串匹配规则：

```json
"strings": {
  "double_quote": true,    // 支持双引号字符串 "..."
  "single_quote": true,    // 支持单引号字符串 '...'
  "backtick": false        // 支持反引号字符串 `...`
}
```

默认值：双引号和单引号均为 true，反引号为 false。

#### numbers (object)

配置数字匹配规则：

```json
"numbers": {
  "use_default": true,     // 使用 highlight.js 默认数字模式
  "binary": true,          // 支持二进制 0b1010
  "octal": true,           // 支持八进制 0o755
  "hex": true,             // 支持十六进制 0xFF
  "decimal": true          // 支持十进制和浮点数
}
```

如果 `use_default` 为 true，将使用 highlight.js 的 C_NUMBER_MODE。

#### patterns (array)

定义额外的语法模式：

```json
"patterns": [
  {
    "className": "meta",
    "begin": "@[A-Za-z_][A-Za-z0-9_]*"
  },
  {
    "className": "function",
    "begin": "\\b(fn)\\s+([A-Za-z_][A-Za-z0-9_]*)",
    "keywords": "fn"
  }
]
```

每个模式支持的字段：
- **className**: highlight.js 的类名（如 "function", "class", "meta", "comment" 等）
- **begin**: 匹配开始的正则表达式
- **end**: 匹配结束的正则表达式（可选）
- **keywords**: 该模式内的关键字（可选）

## 完整示例

参见 `languages/ljos.json` 和 `languages/esharp.json` 文件。

### 最小示例

```json
{
  "name": "MyLang",
  "aliases": ["ml", "mylang"],
  "extensions": [".ml", ".mylang"],
  "keywords": ["if", "else", "for", "while", "fn", "return"],
  "strings": {
    "double_quote": true,
    "single_quote": false
  }
}
```

## 使用方法

1. 创建语言定义 JSON 文件
2. 放置到 `languages/` 文件夹
3. 重启插件
4. 使用文件扩展名或语言名称触发高亮

## 注意事项

- 文件名会成为语言标识符，建议使用小写字母和下划线
- aliases 数组中的别名也可以用于识别该语言
- 插件会自动将语言定义注册到 highlight.js
- 语言检测主要依赖 highlight.js 的自动检测功能
- 提供的扩展名会用于文件名匹配
 
## 使用指南
 
### 命令
 
- `/render [参数] [代码]`：引用一段代码或直接附带代码进行渲染
- `/render_file [参数]`：引用一个代码文件进行渲染
 
参数：
 
| 参数 | 说明 | 示例 |
|------|------|------|
| `-l <语言>` 或 `lang=<语言>` | 指定语言（可选） | `-l python` |
| `-t <主题>` 或 `theme=<主题>` | 指定主题 | `-t dracula` |
| `-s <字号>` 或 `size=<字号>` | 指定字体大小 | `-s 16` |
| `-ln` 或 `line` | 显式开启行号 | `-ln` |
| `-n` 或 `noline` | 显式关闭行号 | `-n` |
 
优先级：命令参数覆盖插件配置；未提供参数时遵循配置默认值。
 
### LLM 工具
 
- `render_code_to_image(code, language="", theme="github")`  
  渲染任意代码为图片并返回消息结果。默认开启行号。
 
- `render_file_to_image(theme="github", language="")`  
  引用文件消息后调用，渲染该文件内容为图片；语言可选，不填将自动检测。
 
### 配置（_conf_schema.json）
 
| 键名 | 类型 | 说明 | 默认值 |
|-----|------|------|--------|
| `default_theme` | `string` | 默认主题名称 | `github-dark` |
| `font_family` | `string` | 字体族名称 | `JetBrains Mono, ...` |
| `font_path` | `string` | 本地字体文件路径 | `""` |
| `highlight_js_path` | `string` | 自定义 `highlight.min.js` 路径 | `""` |
| `highlight_css_path` | `string` | 自定义主题 CSS 路径 | `""` |
| `line_numbers_enabled` | `bool` | 是否启用行号（总开关） | `true` |
| `line_numbers_start_from` | `int` | 行号起始值 | `1` |
| `line_numbers_single_line` | `bool` | 单行代码也显示行号 | `false` |
| `line_numbers_width` | `int` | 行号列宽（px），`0`自动 | `0` |
| `blacklist` | `list` | 群聊黑名单 | `[]` |
 
行号列宽自适应规则：当 `line_numbers_width=0` 或未设置时，按最大行号位数自动计算列宽，公式为 `max(30, digits*8 + 12)`。
 
主题位置：`assets/highlight/styles/<主题>.min.css`。如未设置 `highlight_css_path`，将根据 `default_theme` 自动加载对应 CSS。
 
## 版本历史
 
- 1.5.0  
  - 新增 `render_file_to_image` LLM 工具  
  - 行号列宽可配置并支持自适应  
  - 修复默认开启行号时未加 `-ln` 不显示的问题
 
- 1.4.7  
  - 实现行号显示
 
- 1.4.5  
  - 修复代码无法填充整张图片的问题
 
- 1.4.0-hotfix1  
  - 修复无法检测代码文件语言的问题
 
- 1.4.0  
  - 重写自定义语言支持（存在问题）
 
- 1.3.2  
  - 删除旧版自定义语言解析器（与 AI 代码检查器建议一致）

