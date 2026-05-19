# NoMoreVideo

一个通过 Whisper 进行语音识别，并利用 DeepSeek 总结视频或音频核心内容的工具。
帮你告别冗长的视频和废话，直接提取结构化的内容要点和核心信息。

## 功能特性

- 支持本地音频/视频文件以及在线视频链接（通过 `yt-dlp` 支持的站点，如 YouTube/Bilibili 等）
- 本地基于 Whisper 模型的精准语音转文本
- 智能全自动语言检测（采用 `tiny` 模型快速检测，`medium` 或较大模型进行长音频转写），省时高效
- 大语言模型（DeepSeek）长文本分块处理与内容提取、结构化总结

## 安装依赖

本项目需要 Python 3.8 或以上版本。

1. **克隆仓库代码：**
   ```bash
   git clone https://github.com/Koishi0425/video-sift.git
   cd video-shift
   ```

2. **安装系统依赖 (ffmpeg):**
   请务必在你的系统上安装 `ffmpeg`，并将其添加到环境变量 `PATH` 中：
   - Windows: 可通过 Scoop (`scoop install ffmpeg`)、Chocolatey 安装或直接下载二进制文件。
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`

3. **安装 Python 依赖库:**
   ```bash
   pip install -r requirements.txt
   ```

## 配置项

初次运行前，请基于配置文件模板创建你的个人配置：

```bash
cp settings.example.py settings.py
```

在 `settings.py` 中填入你的：
- `DEEPSEEK_API_KEY`
- (可选) 代理设置 `YTDLP_PROXY` 等参数。

> ⚠️ 注意：`settings.py` 已经被加入 `.gitignore`，请勿将你的 API Key 和隐私配置提交到版本库！

## 使用方法

**基本用法：**
```bash
python main.py <视频链接或本地文件路径>
```

**示例（处理在线视频）：**
```bash
python main.py https://www.bilibili.com/video/BV1...
```

**跳过检测直接使用特定语言转写（例如日语）：**
```bash
python main.py https://www.youtube.com/watch?v=... --language ja --whisper-model medium
```

### 命令参数

- `--workdir`: 中间文件和结果输出根目录 (默认: `outputs`)
- `--detect-model`: 用于自动检测语言的较小 Whisper 模型名称 (默认: `tiny`)
- `--whisper-model`: 本地 Whisper 模型名称 (默认: `medium`)
- `--language`: 转写语言代码，例如 `auto`/`zh`/`ja`/`en` (默认: `auto`)
- `--force`: 忽略已有音频和转写文本，强制重新处理
- `--download-only`: 只下载/提取音频，不转写也不总结
- `--transcript-only`: 只执行到语音转文字，不调用大模型阶段

## 开源协议

MIT License
