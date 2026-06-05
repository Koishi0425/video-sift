import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace


APP_CONFIG_DIR_NAME = "VideoSift"
ENV_SETTINGS_PATH = "VIDEO_SIFT_SETTINGS"

DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_SETTINGS = {
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEFAULT_LLM_MODEL": "deepseek-chat",
    "DEFAULT_DETECT_WHISPER_MODEL": "tiny",
    "DEFAULT_WHISPER_MODEL": "base",
    "DEFAULT_TRANSCRIBE_LANGUAGE": "auto",
    "DEFAULT_WORKDIR": Path("outputs"),
    "MAX_CHUNK_MINUTES": 25,
    "SUMMARY_CHUNK_CHARS": 12000,
    "YTDLP_PROXY": "",
    "YTDLP_USER_AGENT": DEFAULT_BROWSER_USER_AGENT,
    "YTDLP_BILIBILI_HEADERS": {
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    },
    "YTDLP_COOKIES_FROM_BROWSER": "",
    "YTDLP_COOKIES_FILE": "",
}


def user_config_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_CONFIG_DIR_NAME
    return Path.home() / ".config" / "video-sift"


def user_settings_path() -> Path:
    return user_config_dir() / "settings.py"


def fallback_settings_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path.cwd()
    return root / ".video-sift" / "settings.py"


def project_settings_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path(__file__).resolve().parent
    return root / "settings.py"


def candidate_settings_paths(base_dir: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get(ENV_SETTINGS_PATH)
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.append(user_settings_path())
    paths.append(fallback_settings_path(base_dir))
    paths.append(project_settings_path(base_dir))
    return paths


def load_python_settings(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("video_sift_user_settings", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载配置文件：{path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        key: getattr(module, key)
        for key in DEFAULT_SETTINGS
        if hasattr(module, key)
    }


def load_settings(base_dir: Path | None = None) -> SimpleNamespace:
    values = dict(DEFAULT_SETTINGS)
    source_path = None

    for path in candidate_settings_paths(base_dir):
        if path.exists():
            values.update(load_python_settings(path))
            source_path = path
            break

    if source_path is None:
        source_path = save_user_settings(values)

    settings = SimpleNamespace(**values)
    settings.CONFIG_SOURCE_PATH = source_path
    settings.USER_SETTINGS_PATH = user_settings_path()
    return settings


def settings_template(values: dict | None = None) -> str:
    data = dict(DEFAULT_SETTINGS)
    if values:
        data.update(values)

    headers = data.get("YTDLP_BILIBILI_HEADERS") or {}
    return f'''from pathlib import Path

DEEPSEEK_API_KEY = {data.get("DEEPSEEK_API_KEY", "")!r}
DEEPSEEK_BASE_URL = {data.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")!r}

DEFAULT_LLM_MODEL = {data.get("DEFAULT_LLM_MODEL", "deepseek-chat")!r}
DEFAULT_DETECT_WHISPER_MODEL = {data.get("DEFAULT_DETECT_WHISPER_MODEL", "tiny")!r}
DEFAULT_WHISPER_MODEL = {data.get("DEFAULT_WHISPER_MODEL", "base")!r}
DEFAULT_TRANSCRIBE_LANGUAGE = {data.get("DEFAULT_TRANSCRIBE_LANGUAGE", "auto")!r}

DEFAULT_WORKDIR = Path({str(data.get("DEFAULT_WORKDIR", Path("outputs")))!r})
MAX_CHUNK_MINUTES = {int(data.get("MAX_CHUNK_MINUTES", 25))}
SUMMARY_CHUNK_CHARS = {int(data.get("SUMMARY_CHUNK_CHARS", 12000))}

YTDLP_PROXY = {data.get("YTDLP_PROXY", "")!r}
YTDLP_USER_AGENT = {data.get("YTDLP_USER_AGENT", DEFAULT_BROWSER_USER_AGENT)!r}
YTDLP_BILIBILI_HEADERS = {{
    "Referer": {headers.get("Referer", "https://www.bilibili.com/")!r},
    "Origin": {headers.get("Origin", "https://www.bilibili.com")!r},
}}
YTDLP_COOKIES_FROM_BROWSER = {data.get("YTDLP_COOKIES_FROM_BROWSER", "")!r}
YTDLP_COOKIES_FILE = {data.get("YTDLP_COOKIES_FILE", "")!r}
'''


def save_user_settings(values: dict, fallback_dir: Path | None = None) -> Path:
    path = user_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings_template(values), encoding="utf-8")
    except OSError:
        path = fallback_settings_path(fallback_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings_template(values), encoding="utf-8")
    return path
