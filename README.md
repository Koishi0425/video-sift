# video-sift

Video Sift 是一个音视频内容提取与总结工具：先用 `yt-dlp` / `ffmpeg` 提取音频，再用 Whisper 转写，最后调用 DeepSeek 生成结构化 Markdown 总结。

适合处理长视频、播客、会议录音、课程内容等信息密度不稳定的素材。

## 功能

- 支持本地音频/视频文件、在线视频链接、Bilibili BV 号。
- 支持自动识别转写语言，也可以手动指定语言。
- 长音频会自动切分，长文本会分块总结后再合并。
- 会复用已有音频、转写和总结，避免重复处理。
- GUI 支持任务进度、折叠日志、历史任务、总结阅读、设置管理。

## 快速开始

安装依赖：

```powershell
pip install -r requirements.txt
```

启动 GUI：

```powershell
python gui.py
```

命令行处理一个 Bilibili 视频：

```powershell
python main.py BV1xxxxxxxxxx --language auto --whisper-model base
```

处理本地文件：

```powershell
python main.py .\example.mp4
```

## 配置

首次运行可以复制配置模板：

```powershell
copy settings.example.py settings.py
```

常用配置：

- `DEEPSEEK_API_KEY`：DeepSeek API Key。
- `YTDLP_PROXY`：yt-dlp 代理，不需要可以留空。
- `YTDLP_COOKIES_FILE` / `YTDLP_COOKIES_FROM_BROWSER`：Bilibili 等站点需要登录态时使用。
- `FFMPEG_PATH` / `FFPROBE_PATH`：正常可留空，发布版检测不到 PATH 时再手动填写。

发布版不会内置 `settings.py` 或 API Key。GUI 会自动创建用户本地配置文件：

```text
%APPDATA%\VideoSift\settings.py
```

## 输出

每个任务会在 `outputs/` 下生成独立目录，常见文件包括：

- `audio.mp3`：提取出的音频。
- `transcript.txt`：Whisper 转写文本。
- `transcript_with_timestamps.md`：带时间戳的转写文本。
- `summary.md`：DeepSeek 生成的结构化总结。
- `run.log`：本次运行日志。

## 打包发行

当前版本：`v1.2.2`

项目已提供 PyAppify 配置：

- `pyappify.yml`
- `.github/workflows/pyappify-release.yml`
- `icon.png`
- `assets/app_icon.ico`

发布方式：

```powershell
git tag v1.2.2
git push origin v1.2.2
```

推送 `v*` 标签后，GitHub Actions 会构建 Windows 发行包并上传到对应 GitHub Release。

发布前请更新：

- `CHANGELOG.md`
- `.github/release-notes-v1.2.2.md`

## 常见问题

**Bilibili 下载失败，提示 HTTP 412**

通常是需要登录态。请在 GUI 设置页配置 cookies 文件，或设置 `cookies-from-browser` 后重试。

**发布版提示找不到 ffmpeg**

程序会自动从 PATH、Windows 注册表 PATH、Scoop、Chocolatey 常见路径查找。仍然失败时，在 GUI 设置页填写 `ffmpeg.exe` 和 `ffprobe.exe` 的完整路径。

**运行时没有弹出命令行窗口**

这是预期行为。GUI 会静默运行下载、转写和总结流程，进度与错误会显示在日志面板中。

## License

MIT License
