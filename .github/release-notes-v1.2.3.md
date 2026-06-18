Video Sift v1.2.3 聚焦 GUI 体验和转写效率：减少 Windows 下的命令窗口干扰，并在可用时优先使用站点字幕生成转写。

## 主要变化

- Windows GUI 流程会尽量隐藏 ffmpeg、ffprobe 以及 Whisper 间接拉起的命令窗口。
- 长音频切分改为 ffprobe 探测时长、ffmpeg 直接分段，减少不可控子进程弹窗。
- Bilibili 视频会优先尝试使用可公开获取的站点字幕生成转写。
- 字幕命中时会直接生成：
  - `transcript.txt`
  - `transcript_with_timestamps.md`
  - `transcript_segments.json`
  - `site_subtitles/` 原始字幕文件
- 字幕转写成功时会跳过音频下载和 Whisper，提高处理速度。
- 字幕不可用、下载失败、解析失败或内容为空时，会自动回退到原有 Whisper 流程。
- GUI 历史任务会显示转写来源，区分“字幕转写”和“Whisper 转写”。

## 升级说明

- 旧任务结果仍然可以正常读取；没有转写来源字段的旧任务会按 Whisper 转写显示。
- `--download-only` 和 `--summary-only` 行为保持不变。
- 本版本不保证获取 Bilibili 播放器内需要登录态或特殊接口才显示的 AI 字幕；拿不到站点字幕时会继续使用 Whisper。
- 没有新增 Python 依赖。
