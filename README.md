# NoMoreVideo

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

如果想直接使用 `nomorevideo` 命令，可以在项目目录执行：

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

## 使用

安装命令入口后：

```powershell
nomorevideo BV1xxxxxxxxxx
```

也可以输入完整链接：

```powershell
nomorevideo https://www.bilibili.com/video/BV1xxxxxxxxxx
```

或处理本地文件：

```powershell
nomorevideo .\example.mp4
```

如果不带参数直接运行，程序会提示你输入视频链接、BV 号或本地文件路径：

```powershell
nomorevideo
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

## License

MIT License
