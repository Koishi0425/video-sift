import hashlib
import importlib.util
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
from pydub import AudioSegment


def load_settings():
    try:
        import settings as settings_module

        return settings_module
    except ModuleNotFoundError as exc:
        if exc.name != "settings":
            raise

    settings_path = Path(__file__).resolve().with_name("settings.py")
    if not settings_path.exists():
        raise RuntimeError(
            f"找不到配置文件：{settings_path}。请先根据 settings.example.py 创建 settings.py。"
        )

    spec = importlib.util.spec_from_file_location("settings", settings_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载配置文件：{settings_path}")

    settings_module = importlib.util.module_from_spec(spec)
    sys.modules["settings"] = settings_module
    spec.loader.exec_module(settings_module)
    return settings_module


settings = load_settings()


SYSTEM_PROMPT = """你是一个逻辑严密的知识提炼专家。用户会给你一份从视频中提取的语音识别文本。这个视频的 up主讲解可能缺乏条理、内容跳脱、有很多口水话。你的任务是：
忽略原本杂乱的叙述顺序，提取出核心观点。
剔除废话、重复内容和不相关的情感宣泄。
让读者能以最高的效率获取视频的有效信息。
不仅仅要整理视频内容，还要在结尾总结出视频的核心论点和结论，帮助读者快速理解视频的价值。
直接输出结构化的 Markdown 格式输出（包括：核心主题、背景/问题、核心论点分解、结论）。"""

DEFAULT_MODEL = settings.DEFAULT_LLM_MODEL
DEFAULT_WORKDIR = settings.DEFAULT_WORKDIR
DEFAULT_TRANSCRIBE_LANGUAGE = getattr(settings, "DEFAULT_TRANSCRIBE_LANGUAGE", "auto")
MAX_CHUNK_MINUTES = settings.MAX_CHUNK_MINUTES
SUMMARY_CHUNK_CHARS = getattr(settings, "SUMMARY_CHUNK_CHARS", 12000)
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
BILIBILI_BVID_PATTERN = re.compile(r"(?i)(?<![0-9a-z])BV[0-9a-z]{10}(?![0-9a-z])")


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
    audio_path: Path,
    language: str,
    detected_language: str | None = None,
    video_info: dict | None = None,
) -> None:
    metadata = {
        "source": source,
        "source_hash": source_hash(source),
        "source_label": source_label(source, video_info),
        "whisper_model": whisper_model,
        "language": language,
        "detected_language": detected_language,
        "audio_path": audio_path.name,
    }
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
    return bool(
        metadata
        and metadata.get("source") == source
        and metadata.get("source_hash") == source_hash(source)
        and metadata.get("whisper_model") == whisper_model
        and metadata.get("language") == language
    )


def is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise click.ClickException("未检测到 ffmpeg。请先安装 ffmpeg，并确保它在 PATH 中。")


def prepare_workdir(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)


def ytdlp_proxy_args() -> list[str]:
    proxy = getattr(settings, "YTDLP_PROXY", "")
    return ["--proxy", proxy] if proxy else []


def fetch_video_info(source: str) -> dict | None:
    if not is_url(source):
        return None

    command = [
        "yt-dlp",
        "--dump-single-json",
        "--no-playlist",
        "--skip-download",
        *ytdlp_proxy_args(),
        source,
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None

    info = {
        "id": data.get("id"),
        "title": data.get("title"),
        "uploader": data.get("uploader"),
        "webpage_url": data.get("webpage_url"),
        "extractor": data.get("extractor_key") or data.get("extractor"),
    }
    return {key: value for key, value in info.items() if value}


def format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _probe_audio_codec(filepath: Path) -> str | None:
    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        codec = result.stdout.strip()
        return codec if codec else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def extract_audio(source: str, workdir: Path) -> Path:
    prepare_workdir(workdir)
    audio_path = workdir / "audio.mp3"

    if is_url(source):
        command = [
            "yt-dlp",
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
            *ytdlp_proxy_args(),
            source,
        ]
        logging.debug("执行下载命令：%s", " ".join(command))
        subprocess.run(command, check=True)
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
        command = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-acodec", "copy", str(audio_path)]
    else:
        logging.info("输入文件编码为 %s，转码为 mp3", codec or "未知")
        command = ["ffmpeg", "-y", "-i", str(input_path), "-vn", "-acodec", "libmp3lame", str(audio_path)]
        
    logging.debug("执行音频提取命令：%s", " ".join(command))
    subprocess.run(command, check=True)
    return audio_path


def split_audio_if_needed(audio_path: Path, workdir: Path, max_minutes: int = MAX_CHUNK_MINUTES) -> list[tuple[Path, int]]:
    audio = AudioSegment.from_file(audio_path)
    max_ms = max_minutes * 60 * 1000
    if len(audio) <= max_ms:
        logging.info("音频长度 %.1f 分钟，不需要分段", len(audio) / 60000)
        return [(audio_path, 0)]

    chunks_dir = workdir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index, start_ms in enumerate(range(0, len(audio), max_ms), start=1):
        chunk = audio[start_ms : start_ms + max_ms]
        chunk_path = chunks_dir / f"chunk_{index:03d}.mp3"
        chunk.export(chunk_path, format="mp3")
        chunks.append((chunk_path, start_ms))
    logging.info("音频长度 %.1f 分钟，将分为 %d 段", len(audio) / 60000, len(chunks))
    return chunks


def detect_audio_language(model, audio_path: Path) -> str:
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


def transcribe_audio(audio_path: Path, workdir: Path, detect_model_name: str, whisper_model: str, language: str) -> tuple[str, str | None]:
    chunks = split_audio_if_needed(audio_path, workdir)

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
        result = model.transcribe(str(chunk_path), language=transcribe_language, fp16=False)
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
        raise click.ClickException("未设置 DeepSeek API Key。请在环境变量 DEEPSEEK_API_KEY 或 settings.py 中配置 DEEPSEEK_API_KEY。")
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


def summarize_transcript(transcript: str, model: str, workdir: Path, force_summary: bool) -> str:
    chunks = split_text(transcript, SUMMARY_CHUNK_CHARS)
    partial_dir = workdir / "partial_summaries"
    partial_dir.mkdir(parents=True, exist_ok=True)

    if len(chunks) == 1:
        logging.info("转写文本未超过分块阈值，直接总结")
        return call_deepseek(f"请重构并总结以下视频转写文本：\n\n{transcript}", model)

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
            f"下面是同一个视频转写文本的第 {index}/{len(chunks)} 部分。请先提炼这一部分的核心信息，保留重要事实、论点和例子，不要输出最终总总结：\n\n{chunk}",
            model,
        )
        partial_path.write_text(partial_summary, encoding="utf-8")
        partial_summaries.append(partial_summary)
        log_stage(f"文本块 {index}/{len(chunks)} 总结", started_at)

    merged = "\n\n".join(f"## 分块摘要 {index}\n\n{summary}" for index, summary in enumerate(partial_summaries, start=1))
    return call_deepseek(f"下面是同一个视频的多个分块摘要。请去重、合并并输出最终结构化 Markdown 总结：\n\n{merged}", model)


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
    ensure_ffmpeg_available()
    video_info = fetch_video_info(source)
    job_dir = resolve_job_dir(source, workdir, video_info)
    prepare_workdir(job_dir)
    setup_logging(job_dir, log_level)
    logging.info("任务目录：%s", job_dir)
    logging.info("输入源：%s", source)
    logging.info("Whisper 模型：%s，转写语言：%s，LLM 模型：%s", whisper_model, language, llm_model)

    if video_info and video_info.get("title"):
        logging.info("视频标题：%s", video_info["title"])

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
        if not force and metadata_matches(job_dir, source, whisper_model, language) and is_nonempty_file(audio_path):
            logging.info("复用已存在的音频：%s", audio_path)
        else:
            started_at = time.perf_counter()
            logging.info("正在提取音频...")
            audio_path = extract_audio(source, job_dir)
            write_metadata(job_dir, source, whisper_model, audio_path, language, video_info=video_info)
            log_stage("音频提取", started_at)

        if download_only:
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
            started_at = time.perf_counter()
            logging.info("正在进行语音转文字...")
            transcript, detected_language = transcribe_audio(audio_path, job_dir, detect_model, whisper_model, language)
            write_metadata(job_dir, source, whisper_model, audio_path, language, detected_language, video_info)
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

    if not force_summary and not force and metadata_matches(job_dir, source, whisper_model, language) and is_nonempty_file(cached_summary_path):
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
    summary = summarize_transcript(transcript, llm_model, job_dir, force_summary or force)
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
