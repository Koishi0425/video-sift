# video-sift

一个通过 Whisper 转写音视频，并用 DeepSeek 提炼结构化摘要的小工具。

它适合用来快速处理很长的视频、播客或会议录音：先提取音频，再转成文字，最后输出 `summary.md`。

## 功能

- 支持本地音频/视频文件。
- 支持 `yt-dlp` 能处理的在线视频链接，例如 Bilibili、YouTube。
- 支持直接输入 Bilibili BV 号，例如 `BV1xxxxxxx`。
- 支持自动检测转写语言，也可以通过 `--language` 手动指定。
- 长音频会自动切分，长文本会分块总结后再合并。
- 会复用已经生成过的音频、转写和总结，避免重复等待。
- 处理完成后会在控制台显示可点击的结果文件位置。

## 安装

需要 Python 3.8 或更高版本，并确保系统已安装 `ffmpeg`。

Windows 可以用 Scoop 或 Chocolatey 安装：

```powershell
scoop install ffmpeg
```

或：

```powershell
choco install ffmpeg
```

安装 Python 依赖：

```powershell
pip install -r requirements.txt
```

如果想直接使用 `video-sift` 命令，可以在项目目录执行：

```powershell
pip install -e .
```

## 配置

首次运行前，复制配置模板：

```powershell
copy settings.example.py settings.py
```

然后在 `settings.py` 里填写：

- `DEEPSEEK_API_KEY`
- `YTDLP_PROXY`，如果不需要代理可以设为空字符串
- 默认 Whisper 模型、DeepSeek 模型等配置

`settings.py` 已经在 `.gitignore` 中，不要把真实 API Key 提交到仓库。

## 使用 GUI (图形界面)

项目中已经包含了一个基于 PySide6 的图形界面版。配置完成后，你可以通过以下命令启动 GUI：

```powershell
python gui.py
```

或者，如果你通过 `pip install -e .` 安装了该项目，也可以直接使用命令：

```powershell
video-sift-gui
```

在图形界面中，你可以更方便地管理配置、提交任务，以及查看历史处理结果。

## 使用 CLI (命令行)

安装命令入口后（如果通过 `pip install -e .` 安装），可以在终端中直接执行：

```powershell
video-sift BV1xxxxxxxxxx
```

也可以输入完整链接：

```powershell
video-sift https://www.bilibili.com/video/BV1xxxxxxxxxx
```

或处理本地文件：

```powershell
video-sift .\example.mp4
```

如果不带参数直接运行，程序会提示你输入视频链接、BV 号或本地文件路径：

```powershell
video-sift
```

仍然可以用原来的方式运行：

```powershell
python main.py BV1xxxxxxxxxx
```

## 常用参数

- `--workdir`: 中间文件和结果输出目录，默认是 `outputs`。
- `--detect-model`: 自动检测语言用的 Whisper 模型，默认来自 `settings.py`。
- `--whisper-model`: 转写用的 Whisper 模型，默认来自 `settings.py`。
- `--llm-model`: DeepSeek 总结模型，默认来自 `settings.py`。
- `--language`: 转写语言，例如 `auto`、`zh`、`ja`、`en`。
- `--force`: 重新提取音频并转写。
- `--force-summary`: 只强制重新生成总结。
- `--download-only`: 只下载或提取音频。
- `--transcript-only`: 只转写，不调用 DeepSeek。
- `--summary-only`: 复用已有 `transcript.txt` 重新总结。

## 输出

每个输入会在 `outputs` 下生成一个独立任务目录。目录名会优先包含平台、视频号和标题，例如：

```text
bilibili_BV1xxxxxxxxxx_视频标题_ab12cd34ef
```

常见文件包括：

- `audio.mp3`: 提取出的音频。
- `transcript.txt`: Whisper 转写文本。
- `transcript_with_timestamps.md`: 带时间戳的转写文本。
- `transcript_segments.json`: 原始分段信息。
- `summary.md`: DeepSeek 生成的结构化总结。
- `run.log`: 本次运行日志。

处理完成后，控制台会直接显示这些关键文件的完整路径；在支持链接的终端里可以直接点击打开。

## PyAppify 打包发行

项目已提供 `pyappify.yml` 和 GitHub Actions workflow，可通过 PyAppify 打包发行 GUI 版本。

当前发行配置：

- 应用名：`video-sift`
- 入口脚本：`gui.py`
- Python 版本：`3.12`
- 依赖文件：`requirements.txt`
- 代码仓库：`https://github.com/Koishi0425/video-sift.git`
- GitHub Actions：`.github/workflows/pyappify-release.yml`

### 通过 GitHub Actions 发行

PyAppify 使用 Git 标签管理版本。准备发行前，创建语义化版本标签并推送：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

推送 `v*` 标签后，GitHub Actions 会在 Windows runner 上执行 PyAppify 构建，并将 `pyappify_dist/*` 上传到对应的 GitHub Release。

也可以在 GitHub Actions 页面手动运行 `PyAppify Release` workflow。手动运行会生成 `video-sift-pyappify` artifact，适合测试打包结果，但不会自动创建 Release。

手动运行 workflow 时可以勾选 `build_exe_only`。这个模式只构建 PyAppify launcher exe，适合快速验证图标、配置和入口脚本；正式发布标签不要启用它，因为完整发行包仍需要预构建 Python、虚拟环境和依赖。

包含 `PySide6`、`openai-whisper`、`torch` 的完整发行包会比较大。GitHub Actions 在 `makensis` 阶段压缩安装包时可能耗时 15-30 分钟，这是正常现象。workflow 会在构建前检查并清理常见生成目录，避免把 `outputs/`、缓存、虚拟环境或大文件误带进发行包。

### 轻量启动器发行

如果只想分发轻量启动器，将以下文件放在同一目录：

- `pyappify.yml`
- `pyappify.exe`

用户首次启动时，PyAppify 会从 Git 仓库拉取代码、下载隔离 Python 环境并安装依赖。GitHub Actions 方式则会预构建包含 Python、虚拟环境和依赖的数据包，更适合离线或少折腾的发行。

### 发布版配置

发布版不会内置 `settings.py` 或 API Key。首次启动 GUI 时，程序会自动生成用户本地配置文件：

```text
%APPDATA%\VideoSift\settings.py
```

用户可以直接在 GUI 的「设置」页面填写 DeepSeek API Key、代理、cookies、默认模型和默认语言；保存后会写入该用户配置文件。命令行版本也会读取同一个用户配置文件。

`ffmpeg` 和 `ffprobe` 通常不需要手动填写路径。程序会先从当前环境变量查找，再读取 Windows 用户/系统 PATH，并额外检查 Scoop、Chocolatey 的常见安装目录。只有发布版仍然检测不到时，才需要在 GUI「设置」页面填写 `ffmpeg.exe` / `ffprobe.exe` 的完整路径。

发布版会使用内置 Python 环境运行 `yt-dlp` Python 模块，因此不要求用户额外把 `yt-dlp.exe` 放进 PATH。`outputs` 目录也会在首次运行任务时自动创建，不应作为缺失依赖处理。

如果用户配置目录不可写，程序会自动退回到项目/应用目录下的 `.video-sift/settings.py`，仍然不需要用户手动创建配置文件。

如果需要临时指定其他配置文件，可以设置环境变量 `VIDEO_SIFT_SETTINGS` 指向一个自定义 `settings.py`。

## GUI 优化 TODO

### P0 应用结构

- [x] 重构 GUI 为左侧导航栏 + 主内容区结构。
- [x] 设置主分区：「处理任务」「历史任务」「设置」「关于」。
- [x] 将下载、转写、总结作为任务阶段展示，而不是独立导航分区。
- [x] 将日志作为当前任务的可展开详情展示，默认折叠，支持清空和复制。

### P1 处理任务

- [x] 优化任务输入区域，清晰支持 URL、BV 号和本地音视频文件。
- [x] 增加明确的拖拽区域和文件选择入口。
- [x] 保留处理模式选择：完整处理、仅下载音频、仅转写、仅重新总结。
- [x] 优化语言、Whisper 模型等参数选择。
- [x] 增加任务开始、取消、运行中状态展示。
- [x] 默认仅展示当前阶段、整体进度条和简短状态说明。
- [x] 将详细日志折叠展示，用户可手动展开查看完整输出。
- [x] 任务失败时自动提示关键错误，并提供展开日志入口。
- [ ] 对下载失败、配置缺失、依赖缺失给出可操作提示。

### P2 历史任务与结果阅读

- [x] 增加历史任务列表，展示来源、标题、模型、语言、状态和输出路径。
- [x] 增加结果阅读区，直接渲染 `summary.md`，让用户无需打开外部编辑器即可阅读总结。
- [x] 支持切换查看「总结」「转写文本」「带时间戳转写」「日志」。
- [x] 增加结果操作：复制总结、打开文件、打开任务目录、重新总结。
- [x] 优化 Markdown 阅读样式，提升标题、段落、列表和代码块可读性。

### P3 设置与发行

- [x] 增加设置页面，管理 DeepSeek API、代理、cookies、默认模型、默认语言。
- [x] 增加依赖检查，提示 ffmpeg、yt-dlp、Whisper 模型缓存等状态。
- [x] 为 PyAppify 准备稳定 GUI 入口和发行配置。
- [ ] 增加应用图标、版本号、发行说明和更新说明。
- [x] 规划模型缓存、输出目录、配置文件在发行版中的默认位置。

## License

MIT License
