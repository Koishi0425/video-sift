import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import click
from openai import OpenAI
import whisper
from config_utils import find_executable, load_settings


settings = load_settings(Path(__file__).resolve().parent)


def bootstrap_tool_path() -> None:
    tool_dirs = []
    for command, setting_name in (("ffmpeg", "FFMPEG_PATH"), ("ffprobe", "FFPROBE_PATH")):
        configured = getattr(settings, setting_name, "")
        path = find_executable(command, configured)
        if path:
            tool_dirs.append(str(Path(path).parent))

    if tool_dirs:
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join([*dict.fromkeys(tool_dirs), current_path])


bootstrap_tool_path()


SYSTEM_PROMPT = """你是一个逻辑严密的知识提炼专家。用户会给你一份从视频中提取的语音识别文本。这个视频的讲解可能缺乏条理、内容跳脱、有很多口水话。你的任务是：
忽略原本杂乱的叙述顺序，提取出核心观点。
剔除废话、重复内容和不相关的情绪表达。
让读者能以最高的效率获取视频的有效信息。
直接输出结构化的 Markdown，不要逐字复述转写稿。"""

SUMMARY_MODES = {
    "general": {
        "label": "通用总结",
        "description": "适合大多数视频，强调核心主题、背景、主要观点和结论。",
        "partial": "提炼本块的核心主题、关键事实、主要观点、重要例子和可忽略内容。",
        "final": """请按以下结构输出：
# 核心主题
## 背景/问题
## 核心论点
## 重要信息
## 结论
## 值得继续看的部分""",
    },
    "course": {
        "label": "课程笔记",
        "description": "适合课程、教程、讲座，强调概念、步骤、例子和学习路径。",
        "partial": "提炼本块讲到的概念、步骤、例子、易错点和前后依赖关系。",
        "final": """请按以下结构输出：
# 课程主题
## 学习目标
## 核心概念
## 步骤/流程
## 例子与应用
## 易错点
## 学习路径建议""",
    },
    "meeting": {
        "label": "会议纪要",
        "description": "适合会议、访谈、讨论，强调议题、结论、待办和责任人线索。",
        "partial": "提炼本块的讨论议题、明确结论、分歧点、待办事项和责任人线索。",
        "final": """请按以下结构输出：
# 会议主题
## 议题概览
## 已达成结论
## 待办事项
## 责任人/相关方线索
## 风险与分歧
## 后续跟进建议""",
    },
    "review": {
        "label": "测评结论",
        "description": "适合产品、游戏、服务、内容测评，强调评价维度、优缺点和适合人群。",
        "partial": "提炼本块涉及的评价对象、评价维度、优点、缺点、证据和适合/不适合人群。",
        "final": """请按以下结构输出：
# 测评对象
## 总体结论
## 评价维度
## 优点
## 缺点
## 适合人群
## 不适合人群
## 购买/观看/使用建议""",
    },
    "argument": {
        "label": "观点分析",
        "description": "适合观点输出、评论、争议内容，强调立场、论据、漏洞和反方视角。",
        "partial": "提炼本块的核心立场、论据、例子、隐含假设、可能漏洞和反方视角。",
        "final": """请按以下结构输出：
# 核心立场
## 主要论据
## 关键例子
## 隐含假设
## 论证漏洞
## 反方视角
## 综合判断""",
    },
}

DEFAULT_MODEL = settings.DEFAULT_LLM_MODEL
DEFAULT_WORKDIR = settings.DEFAULT_WORKDIR
DEFAULT_TRANSCRIBE_LANGUAGE = getattr(settings, "DEFAULT_TRANSCRIBE_LANGUAGE", "auto")
MAX_CHUNK_MINUTES = settings.MAX_CHUNK_MINUTES
SUMMARY_CHUNK_CHARS = getattr(settings, "SUMMARY_CHUNK_CHARS", 12000)
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
SUBPROCESS_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
BILIBILI_BVID_PATTERN = re.compile(r"(?i)(?<![0-9a-z])BV[0-9a-z]{10}(?![0-9a-z])")
MAX_CONTEXT_TERMS = 30
MAX_CONTEXT_DESCRIPTION_CHARS = 500
MAX_WHISPER_INITIAL_PROMPT_CHARS = 800
MAX_SUMMARY_CONTEXT_CHARS = 1200
DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
TRANSCRIPT_SOURCE_WHISPER = "whisper"
TRANSCRIPT_SOURCE_SITE_SUBTITLE = "site_subtitle"
SUBTITLE_EXTENSIONS = {".json", ".json3", ".vtt", ".srt"}
SUBTITLE_TIMING_PATTERN = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})"
)


def setup_logging(workdir: Path, log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler(workdir / "run.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def log_stage(stage: str, started_at: float) -> None:
    logging.info("%s完成，耗时 %.1f 秒", stage, time.perf_counter() - started_at)


def announce_outputs(title: str, paths: list[tuple[str, Path]]) -> None:
    click.echo()
    click.secho(title, fg="green", bold=True)
    for label, path in paths:
        click.echo(f"{label}: {terminal_path_link(path)}")


def terminal_path_link(path: Path) -> str:
    resolved = path.resolve()
    if not sys.stdout.isatty():
        return str(resolved)

    uri = resolved.as_uri()
    return f"\033]8;;{uri}\033\\{resolved}\033]8;;\033\\"


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]


def extract_bilibili_bvid(value: str) -> str | None:
    match = BILIBILI_BVID_PATTERN.search(value)
    if not match:
        return None
    bvid = match.group(0)
    return f"BV{bvid[2:]}"


def bilibili_video_url(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}"


def normalize_source(source: str) -> str:
    normalized = source.strip()
    if is_url(normalized) or Path(normalized).expanduser().exists():
        return normalized

    bvid = extract_bilibili_bvid(normalized)
    if bvid:
        return bilibili_video_url(bvid)

    return normalized


def safe_name(value: str, max_length: int = 60) -> str:
    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", value.strip())
    name = re.sub(r"_+", "_", name).strip("._")
    return name[:max_length] or "video"


def source_label(source: str, video_info: dict | None = None) -> str:
    if not is_url(source):
        return safe_name(Path(source).expanduser().stem)

    parsed = urlparse(source)
    query = parse_qs(parsed.query)
    title = ""
    if video_info and video_info.get("title"):
        title = safe_name(str(video_info["title"]), 80)

    if "bilibili.com" in parsed.netloc:
        bvid = extract_bilibili_bvid(parsed.path)
        if bvid:
            base = f"bilibili_{bvid}"
            return safe_name(f"{base}_{title}", 140) if title else safe_name(base)
    if parsed.netloc.endswith("youtube.com") and query.get("v"):
        base = f"youtube_{query['v'][0]}"
        return safe_name(f"{base}_{title}", 140) if title else safe_name(base)
    if parsed.netloc.endswith("youtu.be") and parsed.path.strip("/"):
        base = f"youtube_{parsed.path.strip('/')}"
        return safe_name(f"{base}_{title}", 140) if title else safe_name(base)

    path_part = Path(parsed.path).stem or parsed.netloc
    base = f"{parsed.netloc}_{path_part}"
    return safe_name(f"{base}_{title}", 140) if title else safe_name(base)


def find_existing_job_dir(source: str, base_workdir: Path) -> Path | None:
    if not base_workdir.exists():
        return None

    current_hash = source_hash(source)
    for candidate in base_workdir.iterdir():
        if not candidate.is_dir():
            continue
        metadata = load_metadata(candidate)
        if metadata and metadata.get("source_hash") == current_hash and metadata.get("source") == source:
            return candidate

    return None


def resolve_job_dir(source: str, base_workdir: Path, video_info: dict | None = None) -> Path:
    label = source_label(source, video_info)
    preferred = base_workdir / f"{label}_{source_hash(source)}"
    existing = find_existing_job_dir(source, base_workdir)
    if existing:
        if existing != preferred and not preferred.exists():
            existing.rename(preferred)
            return preferred
        return existing

    return preferred


def metadata_path(workdir: Path) -> Path:
    return workdir / "metadata.json"


def transcript_path(workdir: Path) -> Path:
    return workdir / "transcript.txt"


def summary_path(workdir: Path) -> Path:
    return workdir / "summary.md"


def write_metadata(
    workdir: Path,
    source: str,
    whisper_model: str,
    audio_path: Path | None,
    language: str,
    detected_language: str | None = None,
    video_info: dict | None = None,
    transcript_source: str = TRANSCRIPT_SOURCE_WHISPER,
    subtitle_path: Path | None = None,
) -> None:
    metadata = {
        "source": source,
        "source_hash": source_hash(source),
        "source_label": source_label(source, video_info),
        "whisper_model": whisper_model,
        "language": language,
        "detected_language": detected_language,
        "transcript_source": transcript_source,
    }
    if audio_path is not None:
        metadata["audio_path"] = audio_path.name
    if subtitle_path is not None:
        metadata["subtitle_path"] = str(subtitle_path.relative_to(workdir))
    if video_info:
        metadata["video_info"] = video_info
    metadata_path(workdir).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def load_metadata(workdir: Path) -> dict | None:
    path = metadata_path(workdir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def metadata_matches(workdir: Path, source: str, whisper_model: str, language: str) -> bool:
    metadata = load_metadata(workdir)
    if not metadata:
        return False
    source_matches = metadata.get("source") == source and metadata.get("source_hash") == source_hash(source)
    language_matches = metadata.get("language") == language
    if metadata.get("transcript_source") == TRANSCRIPT_SOURCE_SITE_SUBTITLE:
        return source_matches and language_matches
    return bool(
        source_matches
        and language_matches
        and metadata.get("whisper_model") == whisper_model
    )


def is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def ensure_ffmpeg_available() -> None:
    if resolve_executable("ffmpeg", "FFMPEG_PATH") is None:
        raise click.ClickException("未检测到 ffmpeg。请先安装 ffmpeg 并确保它在 PATH 中，或在设置页填写 ffmpeg.exe 路径。")


def resolve_executable(command: str, setting_name: str | None = None) -> str | None:
    configured = getattr(settings, setting_name, "") if setting_name else ""
    return find_executable(command, configured)


def ffmpeg_command() -> str:
    return resolve_executable("ffmpeg", "FFMPEG_PATH") or "ffmpeg"


def ffprobe_command() -> str:
    return resolve_executable("ffprobe", "FFPROBE_PATH") or "ffprobe"


def python_command() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        console_python = executable.with_name("python.exe")
        if console_python.exists():
            return str(console_python)
    return sys.executable


def ytdlp_command() -> list[str]:
    return [python_command(), "-m", "yt_dlp"]


def ffmpeg_location_args() -> list[str]:
    ffmpeg = resolve_executable("ffmpeg", "FFMPEG_PATH")
    if not ffmpeg:
        return []
    return ["--ffmpeg-location", str(Path(ffmpeg).parent)]


def tool_env() -> dict[str, str]:
    env = os.environ.copy()
    tool_dirs = []
    for command, setting_name in (("ffmpeg", "FFMPEG_PATH"), ("ffprobe", "FFPROBE_PATH")):
        path = resolve_executable(command, setting_name)
        if path:
            tool_dirs.append(str(Path(path).parent))

    if tool_dirs:
        current_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*dict.fromkeys(tool_dirs), current_path])

    return env


def run_command(command: list[str], **kwargs):
    if SUBPROCESS_CREATION_FLAGS:
        kwargs.setdefault("creationflags", SUBPROCESS_CREATION_FLAGS)
    return subprocess.run(command, **kwargs)


def patch_whisper_subprocess() -> None:
    if not SUBPROCESS_CREATION_FLAGS:
        return

    audio_module = getattr(whisper, "audio", None)
    if audio_module is None or getattr(audio_module, "_video_sift_hidden_run", False):
        return

    original_run = getattr(audio_module, "run", None)
    if original_run is None:
        return

    def hidden_run(*args, **kwargs):
        kwargs.setdefault("creationflags", SUBPROCESS_CREATION_FLAGS)
        try:
            return original_run(*args, **kwargs)
        except TypeError:
            kwargs.pop("creationflags", None)
            return original_run(*args, **kwargs)

    audio_module.run = hidden_run
    audio_module._video_sift_hidden_run = True


def prepare_workdir(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)


def ytdlp_proxy_args(source: str) -> list[str]:
    proxy = getattr(settings, "YTDLP_PROXY", "")
    if not proxy:
        return []
    
    if "youtube.com" in source or "youtu.be" in source:
        return ["--proxy", proxy]
    
    return []


def is_bilibili_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.netloc.lower().endswith("bilibili.com")


def ytdlp_request_args(source: str) -> list[str]:
    args = [*ytdlp_proxy_args(source)]

    user_agent = getattr(settings, "YTDLP_USER_AGENT", DEFAULT_BROWSER_USER_AGENT)
    if user_agent:
        args.extend(["--user-agent", user_agent])

    if is_bilibili_url(source):
        headers = getattr(
            settings,
            "YTDLP_BILIBILI_HEADERS",
            {
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
            },
        )
        for name, value in headers.items():
            if value:
                args.extend(["--add-headers", f"{name}:{value}"])

    cookies_file = getattr(settings, "YTDLP_COOKIES_FILE", "")
    if cookies_file:
        args.extend(["--cookies", str(Path(cookies_file).expanduser())])

    cookies_from_browser = getattr(settings, "YTDLP_COOKIES_FROM_BROWSER", "")
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", cookies_from_browser])

    return args


def fetch_video_info(source: str) -> dict | None:
    if not is_url(source):
        return None

    command = [
        *ytdlp_command(),
        "--dump-single-json",
        "--no-playlist",
        "--skip-download",
        *ffmpeg_location_args(),
        *ytdlp_request_args(source),
        source,
    ]

    try:
        result = run_command(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=tool_env(),
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None

    info = {
        "id": data.get("id"),
        "title": data.get("title"),
        "uploader": data.get("uploader"),
        "channel": data.get("channel"),
        "categories": data.get("categories"),
        "tags": data.get("tags"),
        "description": compact_text(str(data.get("description")), MAX_CONTEXT_DESCRIPTION_CHARS) if data.get("description") else None,
        "playlist": data.get("playlist"),
        "series": data.get("series"),
        "duration": data.get("duration"),
        "view_count": data.get("view_count"),
        "webpage_url": data.get("webpage_url"),
        "extractor": data.get("extractor_key") or data.get("extractor"),
    }
    return {key: value for key, value in info.items() if value}


def compact_text(value: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def context_term_candidates(source: str, video_info: dict | None = None) -> list[str]:
    terms: list[str] = []

    def add(value) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = compact_text(str(value), 80)
        if text and text not in terms:
            terms.append(text)

    if video_info:
        add(video_info.get("title"))
        add(video_info.get("uploader"))
        add(video_info.get("channel"))
        add(video_info.get("playlist"))
        add(video_info.get("series"))
        add(video_info.get("categories"))
        add(video_info.get("tags"))
    elif not is_url(source):
        add(Path(source).expanduser().stem)

    return terms[:MAX_CONTEXT_TERMS]


def source_context(source: str, video_info: dict | None = None) -> dict:
    context = {
        "title": "",
        "uploader": "",
        "terms": context_term_candidates(source, video_info),
        "description": "",
    }
    if video_info:
        context["title"] = str(video_info.get("title") or "")
        context["uploader"] = str(video_info.get("uploader") or video_info.get("channel") or "")
        description = video_info.get("description")
        if description:
            context["description"] = compact_text(str(description), MAX_CONTEXT_DESCRIPTION_CHARS)
    elif not is_url(source):
        context["title"] = Path(source).expanduser().stem
    return {key: value for key, value in context.items() if value}


def whisper_initial_prompt(context: dict) -> str | None:
    parts = []
    title = context.get("title")
    if title:
        parts.append(f"标题：{title}")
    uploader = context.get("uploader")
    if uploader:
        parts.append(f"作者或频道：{uploader}")
    terms = context.get("terms") or []
    if terms:
        parts.append("可能出现的专有名词：" + "、".join(terms))
    if not parts:
        return None
    prompt = "。".join(parts) + "。请尽量按以上写法转写专有名词，并保留自然标点。"
    return compact_text(prompt, MAX_WHISPER_INITIAL_PROMPT_CHARS)


def summary_context_text(context: dict, include_description: bool = True) -> str:
    lines = []
    title = context.get("title")
    if title:
        lines.append(f"- 标题：{title}")
    uploader = context.get("uploader")
    if uploader:
        lines.append(f"- 作者/频道：{uploader}")
    terms = context.get("terms") or []
    if terms:
        lines.append("- 可能出现的专有名词：" + "、".join(terms))
    description = context.get("description")
    if include_description and description:
        lines.append(f"- 简介摘录：{description}")
    text = "\n".join(lines).strip()
    if len(text) <= MAX_SUMMARY_CONTEXT_CHARS:
        return text
    return text[:MAX_SUMMARY_CONTEXT_CHARS].rstrip() + "..."


def format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _probe_audio_codec(filepath: Path) -> str | None:
    command = [
        ffprobe_command(),
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath),
    ]
    try:
        result = run_command(command, capture_output=True, text=True, check=True, env=tool_env())
        codec = result.stdout.strip()
        return codec if codec else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def probe_audio_duration(audio_path: Path) -> float | None:
    command = [
        ffprobe_command(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = run_command(
            command,
            capture_output=True,
            text=True,
            check=True,
            env=tool_env(),
        )
        duration = float(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None
    return duration if duration > 0 else None


def extract_audio(source: str, workdir: Path) -> Path:
    prepare_workdir(workdir)
    audio_path = workdir / "audio.mp3"

    if is_url(source):
        command = [
            *ytdlp_command(),
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--progress",
            "--progress-template",
            "download:%(progress._default_template)s",
            "-o",
            str(workdir / "source.%(ext)s"),
            *ffmpeg_location_args(),
            *ytdlp_request_args(source),
            source,
        ]
        logging.debug("执行下载命令：%s", " ".join(command))
        run_command(command, check=True, env=tool_env())
        downloaded = sorted(workdir.glob("source*.mp3"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not downloaded:
            raise click.ClickException("yt-dlp 未生成 mp3 音频文件。")
        downloaded[0].replace(audio_path)
        return audio_path

    input_path = Path(source).expanduser().resolve()
    if not input_path.exists():
        raise click.ClickException(f"找不到输入文件：{input_path}")

    codec = _probe_audio_codec(input_path)
    if codec == "mp3":
        logging.info("输入文件音频已是 mp3 编码，直接提取并跳过转码")
        command = [ffmpeg_command(), "-y", "-i", str(input_path), "-vn", "-acodec", "copy", str(audio_path)]
    else:
        logging.info("输入文件编码为 %s，转码为 mp3", codec or "未知")
        command = [ffmpeg_command(), "-y", "-i", str(input_path), "-vn", "-acodec", "libmp3lame", str(audio_path)]
        
    logging.debug("执行音频提取命令：%s", " ".join(command))
    run_command(command, check=True, env=tool_env())
    return audio_path


def split_audio_if_needed(audio_path: Path, workdir: Path, max_minutes: int = MAX_CHUNK_MINUTES) -> list[tuple[Path, int]]:
    duration = probe_audio_duration(audio_path)
    if duration is None:
        logging.warning("无法探测音频时长，将不切分音频。")
        return [(audio_path, 0)]

    max_seconds = max_minutes * 60
    if duration <= max_seconds:
        logging.info("音频长度 %.1f 分钟，不需要分段", duration / 60)
        return [(audio_path, 0)]

    chunks_dir = workdir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    index = 1
    start_seconds = 0.0
    while start_seconds < duration:
        chunk_path = chunks_dir / f"chunk_{index:03d}.mp3"
        chunk_duration = min(max_seconds, duration - start_seconds)
        command = [
            ffmpeg_command(),
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{chunk_duration:.3f}",
            "-i",
            str(audio_path),
            "-vn",
            "-acodec",
            "libmp3lame",
            str(chunk_path),
        ]
        logging.debug("执行音频切分命令：%s", " ".join(command))
        run_command(command, check=True, env=tool_env())
        start_ms = int(start_seconds * 1000)
        chunks.append((chunk_path, start_ms))
        index += 1
        start_seconds += max_seconds
    logging.info("音频长度 %.1f 分钟，将分为 %d 段", duration / 60, len(chunks))
    return chunks


def detect_audio_language(model, audio_path: Path) -> str:
    patch_whisper_subprocess()
    audio = whisper.load_audio(str(audio_path))
    audio = whisper.pad_or_trim(audio)
    mel = whisper.log_mel_spectrogram(audio).to(model.device)
    _, probabilities = model.detect_language(mel)
    language = max(probabilities, key=probabilities.get)
    logging.info("检测到音频语言：%s，置信度 %.2f", language, probabilities[language])
    return language


def save_timestamp_files(workdir: Path, segments: list[dict]) -> None:
    (workdir / "transcript_segments.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    for segment in segments:
        start = format_timestamp(segment["start"])
        end = format_timestamp(segment["end"])
        text = segment["text"].strip()
        if text:
            lines.append(f"[{start} - {end}] {text}")
    (workdir / "transcript_with_timestamps.md").write_text("\n\n".join(lines), encoding="utf-8")


def subtitle_timestamp_seconds(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) >= 2 else 0
    hours = int(parts[-3]) if len(parts) >= 3 else 0
    return hours * 3600 + minutes * 60 + seconds


def clean_subtitle_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\\.*?\}", "", text)
    text = text.replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def dedupe_subtitle_segments(segments: list[dict]) -> list[dict]:
    cleaned = []
    previous = ""
    for segment in sorted(segments, key=lambda item: (float(item["start"]), float(item["end"]))):
        text = clean_subtitle_text(str(segment.get("text", "")))
        normalized = re.sub(r"\s+", "", text)
        if not text or (normalized and normalized == previous):
            continue
        cleaned.append(
            {
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "text": text,
            }
        )
        previous = normalized
    return cleaned


def parse_bilibili_json_subtitle(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    segments = []

    if isinstance(data, dict) and isinstance(data.get("body"), list):
        for item in data["body"]:
            text = clean_subtitle_text(str(item.get("content", "")))
            if text:
                segments.append(
                    {
                        "start": float(item.get("from", 0)),
                        "end": float(item.get("to", item.get("from", 0))),
                        "text": text,
                    }
                )
        return dedupe_subtitle_segments(segments)

    if isinstance(data, dict) and isinstance(data.get("events"), list):
        for event in data["events"]:
            parts = [
                str(segment.get("utf8", ""))
                for segment in event.get("segs", [])
                if isinstance(segment, dict)
            ]
            text = clean_subtitle_text("".join(parts))
            if text:
                start = float(event.get("tStartMs", 0)) / 1000
                duration = float(event.get("dDurationMs", 0)) / 1000
                segments.append({"start": start, "end": start + duration, "text": text})
        return dedupe_subtitle_segments(segments)

    return []


def parse_text_subtitle(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    segments = []
    current_start = None
    current_end = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_end, current_lines
        if current_start is None or current_end is None:
            current_lines = []
            return
        caption = clean_subtitle_text(" ".join(current_lines))
        if caption:
            segments.append({"start": current_start, "end": current_end, "text": caption})
        current_start = None
        current_end = None
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = SUBTITLE_TIMING_PATTERN.search(line)
        if match:
            flush()
            current_start = subtitle_timestamp_seconds(match.group("start"))
            current_end = subtitle_timestamp_seconds(match.group("end"))
            continue
        if not line:
            flush()
            continue
        if line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            continue
        if current_start is None and line.isdigit():
            continue
        if current_start is not None:
            current_lines.append(line)

    flush()
    return dedupe_subtitle_segments(segments)


def parse_subtitle_file(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    try:
        if suffix in {".json", ".json3"}:
            return parse_bilibili_json_subtitle(path)
        if suffix in {".vtt", ".srt"}:
            return parse_text_subtitle(path)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        logging.debug("解析字幕失败：%s，原因：%s", path, exc)
    return []


def subtitle_language_score(path: Path, language: str) -> tuple[int, str]:
    name = path.name.lower()
    score = 100
    requested = language.lower()
    if requested != "auto" and requested in name:
        score -= 40
    if any(token in name for token in ("zh-hans", "zh-cn", ".zh.", "chinese", "zho", "chi")):
        score -= 30
    if any(token in name for token in ("ai", "auto")):
        score += 5
    score += {".json": 0, ".json3": 1, ".vtt": 2, ".srt": 3}.get(path.suffix.lower(), 20)
    return score, name


def subtitle_candidates(subtitle_dir: Path, language: str) -> list[Path]:
    files = [
        path
        for path in subtitle_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS
    ]
    return sorted(files, key=lambda path: subtitle_language_score(path, language))


def append_subtitle_text(current: str, text: str) -> str:
    if not current:
        return text
    if re.search(r"[\u4e00-\u9fff，。！？；：、）】》]$", current) or re.match(
        r"^[\u4e00-\u9fff，。！？；：、）】》]",
        text,
    ):
        return current + text
    return current + " " + text


def subtitle_segments_to_transcript(segments: list[dict]) -> str:
    paragraphs = []
    current = ""
    for segment in segments:
        text = str(segment["text"]).strip()
        current = append_subtitle_text(current, text)
        if len(current) >= 180 or text.endswith(("。", "！", "？", ".", "!", "?")):
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs).strip()


def download_site_subtitles(source: str, workdir: Path, language: str) -> Path | None:
    subtitle_dir = workdir / "site_subtitles"
    if subtitle_dir.exists():
        shutil.rmtree(subtitle_dir)
    subtitle_dir.mkdir(parents=True, exist_ok=True)

    command = [
        *ytdlp_command(),
        "--skip-download",
        "--no-playlist",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "all",
        "--sub-format",
        "json/json3/vtt/srt/best",
        "-o",
        str(subtitle_dir / "%(id)s.%(language)s.%(ext)s"),
        *ytdlp_request_args(source),
        source,
    ]
    logging.debug("执行字幕下载命令：%s", " ".join(command))
    try:
        run_command(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=tool_env(),
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        logging.info("未能获取站点字幕，将回退 Whisper：%s", exc)
        return None

    for path in subtitle_candidates(subtitle_dir, language):
        segments = parse_subtitle_file(path)
        transcript = subtitle_segments_to_transcript(segments)
        if transcript:
            transcript_path(workdir).write_text(transcript, encoding="utf-8")
            save_timestamp_files(workdir, segments)
            logging.info("已使用站点字幕生成转写：%s", path)
            return path

    logging.info("未找到可解析的站点字幕，将回退 Whisper。")
    return None


def transcribe_from_site_subtitles(source: str, workdir: Path, language: str) -> tuple[str, Path] | None:
    if not is_bilibili_url(source):
        return None

    logging.info("正在尝试使用 Bilibili 站点字幕/AI 字幕...")
    subtitle_path = download_site_subtitles(source, workdir, language)
    if subtitle_path is None:
        return None

    transcript = transcript_path(workdir).read_text(encoding="utf-8")
    if not transcript.strip():
        return None
    return transcript, subtitle_path


def transcribe_audio(
    audio_path: Path,
    workdir: Path,
    detect_model_name: str,
    whisper_model: str,
    language: str,
    context: dict | None = None,
) -> tuple[str, str | None]:
    patch_whisper_subprocess()
    chunks = split_audio_if_needed(audio_path, workdir)
    initial_prompt = whisper_initial_prompt(context or {})
    if initial_prompt:
        logging.info("已为 Whisper 提供标题/标签上下文提示，帮助识别专有名词")

    if language == "auto":
        logging.info("加载语言检测模型：%s", detect_model_name)
        d_model = whisper.load_model(detect_model_name)
        transcribe_language = detect_audio_language(d_model, chunks[0][0])
        del d_model
    else:
        transcribe_language = language

    detected_language = transcribe_language if language == "auto" else None
    logging.info("转写语言：%s", transcribe_language)

    logging.info("加载转写 Whisper 模型：%s", whisper_model)
    model = whisper.load_model(whisper_model)

    transcripts = []
    all_segments = []

    for index, (chunk_path, start_ms) in enumerate(chunks, start=1):
        started_at = time.perf_counter()
        logging.info("正在转写第 %d/%d 段音频：%s", index, len(chunks), chunk_path)
        result = model.transcribe(
            str(chunk_path),
            language=transcribe_language,
            fp16=False,
            initial_prompt=initial_prompt,
        )
        transcripts.append(result["text"].strip())

        offset_seconds = start_ms / 1000
        for segment in result.get("segments", []):
            all_segments.append(
                {
                    "start": segment["start"] + offset_seconds,
                    "end": segment["end"] + offset_seconds,
                    "text": segment["text"].strip(),
                }
            )
        log_stage(f"第 {index}/{len(chunks)} 段转写", started_at)

    transcript = "\n\n".join(part for part in transcripts if part)
    transcript_path(workdir).write_text(transcript, encoding="utf-8")
    save_timestamp_files(workdir, all_segments)
    return transcript, detected_language


def deepseek_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY") or settings.DEEPSEEK_API_KEY
    if not api_key:
        raise click.ClickException(
            f"未设置 DeepSeek API Key。请在 GUI 的设置页填写，或设置环境变量 DEEPSEEK_API_KEY。"
            f"当前用户配置文件：{settings.USER_SETTINGS_PATH}"
        )
    return OpenAI(api_key=api_key, base_url=settings.DEEPSEEK_BASE_URL)


def call_deepseek(user_prompt: str, model: str) -> str:
    response = deepseek_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    content = response.choices[0].message.content
    if not content:
        raise click.ClickException("DeepSeek 返回了空总结。")
    return content


def split_text(text: str, max_chars: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    chunks = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start : start + max_chars])
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks or [text]


def summary_mode_config(summary_mode: str) -> dict:
    return SUMMARY_MODES.get(summary_mode, SUMMARY_MODES["general"])


def summary_mode_label(summary_mode: str) -> str:
    return summary_mode_config(summary_mode)["label"]


def prompt_context_section(context: dict | None, include_description: bool = True) -> str:
    if not context:
        return ""
    text = summary_context_text(context, include_description)
    if not text:
        return ""
    return f"""可参考的来源上下文如下。它用于纠正专有名词和理解主题；如果和转写内容冲突，不要凭空扩写。

{text}

"""


def final_summary_prompt(transcript: str, summary_mode: str, context: dict | None = None) -> str:
    config = summary_mode_config(summary_mode)
    return f"""请按“{config["label"]}”模式重构并总结以下视频转写文本。

模式说明：{config["description"]}

输出要求：
- 使用 Markdown。
- 不要逐字复述转写稿。
- 优先提炼有效信息，忽略口癖、重复和跑题内容。
- 保持结构稳定，不要临时发明新的一级栏目。

{config["final"]}

{prompt_context_section(context)}转写文本：

{transcript}"""


def partial_summary_prompt(chunk: str, index: int, total: int, summary_mode: str, context: dict | None = None) -> str:
    config = summary_mode_config(summary_mode)
    return f"""下面是同一个视频转写文本的第 {index}/{total} 部分。

请先做分块提炼，不要输出最终总总结。

本块提炼要求：
- {config["partial"]}
- 保留重要事实、论点、例子和数字。
- 标出与前后文可能相关的信息。
- 去掉口癖、重复和明显跑题内容。

{prompt_context_section(context, include_description=False)}转写文本：

{chunk}"""


def merge_summary_prompt(partial_summaries: list[str], summary_mode: str, context: dict | None = None) -> str:
    config = summary_mode_config(summary_mode)
    merged = "\n\n".join(f"## 分块摘要 {index}\n\n{summary}" for index, summary in enumerate(partial_summaries, start=1))
    return f"""下面是同一个视频的多个分块摘要。请按“{config["label"]}”模式合并为最终总结。

模式说明：{config["description"]}

合并要求：
- 使用 Markdown。
- 去重，避免反复列出同一个观点。
- 保留跨块反复出现的核心观点。
- 修正分块摘要之间的前后顺序和逻辑关系。
- 不要逐字复述分块摘要。
- 保持结构稳定，不要临时发明新的一级栏目。

{config["final"]}

{prompt_context_section(context)}分块摘要：

{merged}"""


def summarize_transcript(
    transcript: str,
    model: str,
    workdir: Path,
    force_summary: bool,
    summary_mode: str,
    context: dict | None = None,
) -> str:
    chunks = split_text(transcript, SUMMARY_CHUNK_CHARS)
    partial_dir = workdir / "partial_summaries"
    if summary_mode != "general":
        partial_dir = partial_dir / summary_mode
    partial_dir.mkdir(parents=True, exist_ok=True)

    if len(chunks) == 1:
        logging.info("转写文本未超过分块阈值，直接总结")
        return call_deepseek(final_summary_prompt(transcript, summary_mode, context), model)

    logging.info("转写文本较长，将分为 %d 块分别总结后再合并", len(chunks))
    partial_summaries = []
    for index, chunk in enumerate(chunks, start=1):
        partial_path = partial_dir / f"partial_{index:03d}.md"
        if not force_summary and is_nonempty_file(partial_path):
            logging.info("复用分块总结 %d/%d：%s", index, len(chunks), partial_path)
            partial_summaries.append(partial_path.read_text(encoding="utf-8"))
            continue

        started_at = time.perf_counter()
        logging.info("正在总结文本块 %d/%d", index, len(chunks))
        partial_summary = call_deepseek(
            partial_summary_prompt(chunk, index, len(chunks), summary_mode, context),
            model,
        )
        partial_path.write_text(partial_summary, encoding="utf-8")
        partial_summaries.append(partial_summary)
        log_stage(f"文本块 {index}/{len(chunks)} 总结", started_at)

    return call_deepseek(merge_summary_prompt(partial_summaries, summary_mode, context), model)


def validate_stage_options(download_only: bool, transcript_only: bool, summary_only: bool) -> None:
    selected = [download_only, transcript_only, summary_only]
    if sum(1 for item in selected if item) > 1:
        raise click.ClickException("--download-only、--transcript-only、--summary-only 只能选择一个。")


@click.command()
@click.argument("source", required=False)
@click.option("--workdir", type=click.Path(path_type=Path), default=DEFAULT_WORKDIR, show_default=True, help="中间文件和结果输出根目录。")
@click.option("--detect-model", default=settings.DEFAULT_DETECT_WHISPER_MODEL, show_default=True, help="用于自动检测语言的较小 Whisper 模型名称，例如 tiny/base。")
@click.option("--whisper-model", default=settings.DEFAULT_WHISPER_MODEL, show_default=True, help="本地 Whisper 模型名称，例如 tiny/base/small/medium/large。")
@click.option("--llm-model", default=DEFAULT_MODEL, show_default=True, help="DeepSeek 总结模型。")
@click.option("--summary-mode", default="general", type=click.Choice(list(SUMMARY_MODES)), show_default=True, help="总结模式：general/course/meeting/review/argument。")
@click.option("--language", default=DEFAULT_TRANSCRIBE_LANGUAGE, show_default=True, help="转写语言代码，例如 auto/zh/ja/en。auto 会先检测首段音频语言，再固定该语言转写。")
@click.option("--force", is_flag=True, help="忽略已有音频和转写文本，强制重新处理。")
@click.option("--force-summary", is_flag=True, help="忽略已有 summary.md 和分块摘要，强制重新总结。")
@click.option("--download-only", is_flag=True, help="只下载/提取音频，不转写也不总结。")
@click.option("--transcript-only", is_flag=True, help="只执行到语音转文字，不调用 DeepSeek 总结。")
@click.option("--summary-only", is_flag=True, help="只使用已匹配的 transcript.txt 重新总结，不下载或转写。")
@click.option("--log-level", default="INFO", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False), show_default=True, help="日志级别。")
def main(
    source: str | None,
    workdir: Path,
    detect_model: str,
    whisper_model: str,
    llm_model: str,
    summary_mode: str,
    language: str,
    force: bool,
    force_summary: bool,
    download_only: bool,
    transcript_only: bool,
    summary_only: bool,
    log_level: str,
) -> None:
    """将视频 URL 或本地视频文件转写并整理为 Markdown 摘要。"""
    if not source:
        source = click.prompt("请输入视频链接、BV号或本地文件路径", type=str)

    raw_source = source
    source = normalize_source(source)
    language = language.strip().lower()
    validate_stage_options(download_only, transcript_only, summary_only)
    video_info = fetch_video_info(source)
    job_dir = resolve_job_dir(source, workdir, video_info)
    prepare_workdir(job_dir)
    if not video_info:
        existing_metadata = load_metadata(job_dir)
        if existing_metadata and isinstance(existing_metadata.get("video_info"), dict):
            video_info = existing_metadata["video_info"]
    context = source_context(source, video_info)
    setup_logging(job_dir, log_level)
    logging.info("任务目录：%s", job_dir)
    logging.info("输入源：%s", source)
    logging.info("Whisper 模型：%s，转写语言：%s，LLM 模型：%s", whisper_model, language, llm_model)
    logging.info("总结模式：%s", summary_mode_label(summary_mode))
    if context.get("terms"):
        logging.info("上下文提示词：%s", "、".join(context["terms"][:8]))

    if video_info and video_info.get("title"):
        logging.info("视频标题：%s", video_info["title"])
    if video_info and video_info.get("duration"):
        logging.info("视频时长：%s", format_timestamp(float(video_info["duration"])))

    if source != raw_source.strip():
        logging.info("已将输入解析为：%s", source)

    audio_path = job_dir / "audio.mp3"
    cached_transcript_path = transcript_path(job_dir)
    cached_summary_path = summary_path(job_dir)

    if summary_only:
        if not metadata_matches(job_dir, source, whisper_model, language) or not is_nonempty_file(cached_transcript_path):
            raise click.ClickException("--summary-only 需要当前输入源和 Whisper 模型匹配的 transcript.txt。")
        transcript = cached_transcript_path.read_text(encoding="utf-8")
    else:
        if download_only:
            ensure_ffmpeg_available()
            if not force and metadata_matches(job_dir, source, whisper_model, language) and is_nonempty_file(audio_path):
                logging.info("复用已存在的音频：%s", audio_path)
            else:
                started_at = time.perf_counter()
                logging.info("正在提取音频...")
                audio_path = extract_audio(source, job_dir)
                write_metadata(job_dir, source, whisper_model, audio_path, language, video_info=video_info)
                log_stage("音频提取", started_at)
            logging.info("已按 --download-only 停止：%s", audio_path)
            announce_outputs(
                "音频已准备好",
                [
                    ("音频文件", audio_path),
                    ("任务目录", job_dir),
                ],
            )
            return

        if not force and metadata_matches(job_dir, source, whisper_model, language) and is_nonempty_file(cached_transcript_path):
            logging.info("复用已匹配的转写文本：%s", cached_transcript_path)
            transcript = cached_transcript_path.read_text(encoding="utf-8")
        else:
            transcript = ""
            started_at = time.perf_counter()
            subtitle_result = transcribe_from_site_subtitles(source, job_dir, language)
            if subtitle_result:
                transcript, subtitle_path = subtitle_result
                write_metadata(
                    job_dir,
                    source,
                    whisper_model,
                    None,
                    language,
                    video_info=video_info,
                    transcript_source=TRANSCRIPT_SOURCE_SITE_SUBTITLE,
                    subtitle_path=subtitle_path,
                )
                log_stage("站点字幕转写", started_at)
            else:
                ensure_ffmpeg_available()
                if not force and metadata_matches(job_dir, source, whisper_model, language) and is_nonempty_file(audio_path):
                    logging.info("复用已存在的音频：%s", audio_path)
                else:
                    started_at = time.perf_counter()
                    logging.info("正在提取音频...")
                    audio_path = extract_audio(source, job_dir)
                    write_metadata(job_dir, source, whisper_model, audio_path, language, video_info=video_info)
                    log_stage("音频提取", started_at)

                started_at = time.perf_counter()
                logging.info("正在进行语音转文字...")
                transcript, detected_language = transcribe_audio(audio_path, job_dir, detect_model, whisper_model, language, context)
                write_metadata(
                    job_dir,
                    source,
                    whisper_model,
                    audio_path,
                    language,
                    detected_language,
                    video_info,
                    transcript_source=TRANSCRIPT_SOURCE_WHISPER,
                )
                log_stage("语音转文字", started_at)

    if not transcript.strip():
        raise click.ClickException("转写结果为空，无法总结。")

    if transcript_only:
        logging.info("已按 --transcript-only 停止：%s", cached_transcript_path)
        announce_outputs(
            "转写已完成",
            [
                ("转写文本", cached_transcript_path),
                ("带时间戳文本", job_dir / "transcript_with_timestamps.md"),
                ("任务目录", job_dir),
            ],
        )
        return

    if not summary_only and not force_summary and not force and metadata_matches(job_dir, source, whisper_model, language) and is_nonempty_file(cached_summary_path):
        logging.info("复用已存在的总结：%s", cached_summary_path)
        announce_outputs(
            "总结已完成（复用已有结果）",
            [
                ("总结文件", cached_summary_path),
                ("转写文本", cached_transcript_path),
                ("任务目录", job_dir),
            ],
        )
        return

    logging.info("正在调用 DeepSeek 进行逻辑重构...")
    started_at = time.perf_counter()
    summary = summarize_transcript(transcript, llm_model, job_dir, force_summary or force or summary_only, summary_mode, context)
    cached_summary_path.write_text(summary, encoding="utf-8")
    log_stage("DeepSeek 总结", started_at)

    logging.info("完成：%s", cached_summary_path)
    announce_outputs(
        "总结已完成",
        [
            ("总结文件", cached_summary_path),
            ("转写文本", cached_transcript_path),
            ("带时间戳文本", job_dir / "transcript_with_timestamps.md"),
            ("任务目录", job_dir),
        ],
    )


if __name__ == "__main__":
    main()
