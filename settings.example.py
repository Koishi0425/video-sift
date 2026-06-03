from pathlib import Path

# 请填入你的 DeepSeek API Key
DEEPSEEK_API_KEY = "your_deepseek_api_key_here"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 模型设置
DEFAULT_LLM_MODEL = "deepseek-chat" # 根据需要调整
DEFAULT_DETECT_WHISPER_MODEL = "tiny"
DEFAULT_WHISPER_MODEL = "medium"
DEFAULT_TRANSCRIBE_LANGUAGE = "auto"

# 路径与分块设置
DEFAULT_WORKDIR = Path("outputs")
MAX_CHUNK_MINUTES = 25
SUMMARY_CHUNK_CHARS = 12000

# 网络代理设置 (用于 yt-dlp 提取音频，不需要则设为空字符串 "")
YTDLP_PROXY = "http://127.0.0.1:7890"

# yt-dlp 请求设置。B 站 412 通常需要接近浏览器的请求头；如仍失败，再开启 cookies。
YTDLP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
YTDLP_BILIBILI_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

# 二选一：需要登录态或仍遇到 412 时启用。浏览器名称示例：chrome、edge、firefox。
YTDLP_COOKIES_FROM_BROWSER = ""
YTDLP_COOKIES_FILE = ""
