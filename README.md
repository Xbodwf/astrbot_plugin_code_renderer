# 代码预览器插件

将代码渲染为精美图片并发送给用户。

## 功能特性

- 支持 40+ 种主流编程语言的语法高亮
- 多种代码主题可选（Monokai、Dracula、GitHub Dark 等）
- 支持自定义字体、字体大小、行号显示等
- 支持添加第三方语言配置
- 自动检测代码语言
- **支持命令行参数指定渲染选项**

## 使用方法

### 渲染代码

```
/render [参数(支持换行)]
```

引用一条包含代码的消息，然后发送 `/render [参数]`

### 支持的参数

| 参数 | 简写 | 说明 | 示例 |
|------|------|------|------|
| `lang=<语言>` | `-l <语言>` | 指定代码语言 | `-l python` |
| `theme=<主题>` | `-t <主题>` | 指定渲染主题 | `-t dracula` |
| `size=<字号>` | `-s <字号>` | 指定字体大小 | `-s 16` |
| `noline` | `-n` | 不显示行号 | `-n` |
| `line` | `-ln` | 显示行号 | `-ln` |

### 示例

```
# 指定语言
/render -l python print("Hello, World!")

# 指定语言和主题
/render -l python -t dracula print("Hello")

# 使用等号格式
/render lang=js theme=nord console.log("hi")

# 不显示行号
/render -l python -n def foo(): pass

# 指定字体大小
/render -l python -s 18 print("大字体")
```

使用 Markdown 代码块：

```
/render -t github-dark
​```python
def hello():
    print("Hello, World!")
​```
```

### 查看支持的语言和主题

- `/render_langs` - 查看所有支持的编程语言
- `/render_themes` - 查看所有支持的主题

## 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| theme | 代码主题 | monokai |
| font_family | 字体名称 | Consolas |
| font_size | 字体大小 | 14 |
| line_numbers | 显示行号 | true |
| padding | 内边距 | 20 |
| max_lines | 最大行数 | 100 |
| blacklist | 群聊黑名单 | [] |

### 支持的主题

- `monokai` - 经典 Monokai 主题
- `dracula` - Dracula 暗色主题
- `github-dark` - GitHub 暗色主题
- `one-dark` - Atom One Dark 主题
- `vs-dark` - VS Code 暗色主题
- `nord` - Nord 主题

## 添加自定义语言

1. 在插件目录创建 `custom_languages.json` 文件
2. 参考 `custom_languages.json.example` 的格式添加语言配置

```json
{
  "mylang": {
    "extensions": [".mylang", ".ml"],
    "aliases": ["my", "myl"],
    "display_name": "My Language"
  }
}
```

## 依赖

- Pillow >= 9.0.0
- Pygments >= 2.15.0

## 注意事项

- 插件启动时会自动清理临时文件
- 渲染的图片会在 1 小时后自动清理
- 单次渲染最多支持 100 行代码（可配置）
