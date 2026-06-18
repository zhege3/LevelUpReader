# 遍历阅读 LevelUpReader

> 阅他人，寻自己 —— TXT 有声小说阅读器

基于 Microsoft Edge TTS 的 Windows 桌面小说阅读软件，支持多角色语音朗读、智能编码识别、沉浸式精简模式。

---

## 功能特性

### 📖 文本阅读
- 自动识别编码（UTF-8 / UTF-16 / GBK / GB2312 / GB18030 / Big5）
- 按章节自动分割（第X章 / Chapter X 等模式）
- 书签记忆，关闭自动保存进度，下次打开恢复
- 鼠标滚轮阅读，章末自动翻章

### 🔊 语音朗读
- 调用 edge-tts（Microsoft Edge 免费 TTS），6 种中文语音可选
- 语速 0.5x ~ 2.0x 可调
- 选中文字右键或自动弹出菜单"从此处朗读"

### 🎭 高级播放（多角色）- 目前识别混乱，慎用
- 自动分析当前章节对话，识别角色（匹配 `XXX说/道/问/喊/笑` 等模式）
- 男女声音交替自动分配（云扬/云希/云健 男声 | 晓晓/晓伊/晓萱 女声）
- 旁白使用独立语音，角色对话分别使用各自声音
- 解析流程：人物检测 → 自动分配 → 逐段朗读

### 🎹 快捷键

| 按键 | 功能 |
|---|---|
| `Ctrl + O` | 打开 TXT 文件 |
| `Ctrl + 空格` | 播放 / 暂停 |
| `Ctrl + ←` | 上一章 |
| `Ctrl + →` | 下一章 |
| `Ctrl + Enter` | 从光标位置朗读 |
| `E` | 精简模式切换 |
| `空格` | 老板键（隐藏窗口，任务栏也消失） |
| `Ctrl + Alt` | 恢复窗口（任意位置按下） |
| `⏯ ⏮ ⏭ ⏹` | 多媒体键控制播放 |
| `鼠标滚轮` | 滚动阅读 |
| `选中文字` | 弹出朗读 / 复制菜单 |

### 🕶️ 精简模式
- 隐藏工具栏、状态栏和标题栏，只留正文区域
- 窗口保持原位置大小不变
- 可按住拖动移动窗口

### 👔 老板键
- 按 `空格` 窗口从桌面和任务栏同时消失
- 朗读在后台继续不中断
- 任意界面按 `Ctrl + Alt` 恢复（松开生效）

### 🔍 文本搜索
- 工具栏搜索按钮，实时高亮匹配
- 上一个 / 下一个跳转
- 橙色高亮标记

---

## 安装

### 环境要求
- Windows 10 / 11
- Python 3.9+

### 安装依赖
```bash
pip install edge-tts pygame chardet
```

### 运行
```bash
python LevelUpReader.py
```
双击 `LevelUpReader.pyw` 无控制台窗口启动。

---

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=icon.ico --name LevelUpReader LevelUpReader.py
```

产物在 `dist/LevelUpReader.exe`，约 20MB。

---

## 项目结构

```
LevelUpReader.py      # 主程序（单文件，约 1500 行）
LevelUpReader.pyw     # 无控制台启动器
icon.ico              # EXE 图标
icon.png              # 窗口图标（嵌入源码）
.gitignore            # Git 忽略规则
README.md             # 项目说明
```

---

## 配置与数据

| 文件 | 路径 | 说明 |
|---|---|---|
| 全局配置 | `%APPDATA%\LevelUpReader\config.json` | 窗口大小、语速、默认语音、字体大小 |
| 阅读进度 | `%APPDATA%\LevelUpReader\bookmarks\<hash>.json` | 章节索引、滚动位置、角色声音映射 |

---

## 技术栈

| 组件 | 用途 |
|---|---|
| `tkinter` | Windows XP 经典风格 GUI |
| `edge-tts` | Microsoft Edge 免费 TTS 引擎 |
| `pygame` | MP3 音频播放（混音器） |
| `chardet` | 文本编码自动检测 |
| `asyncio` | edge-tts 异步调用 |
| `ctypes` | Win32 API（IME 禁用、全局热键） |

---

## 协议

MIT License
