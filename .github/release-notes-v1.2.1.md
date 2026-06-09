Video Sift v1.2.1 是 v1.2.0 之后的补丁版本，主要补齐中文文档、更新说明和失败时的可操作提示。

## 主要变化

- README 改为精简版中文说明，减少首页阅读负担。
- 更新日志和 GitHub Release 说明改为中文。
- 任务失败时会根据日志给出更明确的处理建议：
  - Bilibili HTTP 412：提示配置 cookies。
  - DeepSeek API Key 缺失：提示到设置页填写。
  - ffmpeg 缺失：提示安装或填写 `ffmpeg.exe` / `ffprobe.exe` 路径。
  - 网络或代理问题：提示检查网络或设置 yt-dlp 代理。
  - 本地文件缺失：提示检查文件路径。
- 移除 Qt 6 高 DPI 废弃设置，减少启动警告。
- 清理项目图标 PNG 的色彩配置，避免项目自带资源触发 libpng iCCP 警告。

## 升级说明

- 用户本地设置不会被打包覆盖，API Key 仍保存在本机配置文件中。
- 如果 Bilibili 返回 HTTP 412，请在设置页配置 cookies 后重试。
- 如果发布版仍提示找不到 ffmpeg，请在设置页填写 `ffmpeg.exe` / `ffprobe.exe` 的完整路径。
