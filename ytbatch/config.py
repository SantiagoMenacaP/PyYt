"""Configuración de descarga y catálogo de formatos disponibles."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any

CUSTOM = "custom"


def _video(height: int | None = None) -> str:
    """Selector de formato que prioriza MP4/H.264 hasta la altura indicada."""
    h = f"[height<={height}]" if height else ""
    return (
        f"bestvideo{h}[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo{h}[ext=mp4]+bestaudio[ext=m4a]/"
        f"best{h}[ext=mp4]/"
        f"best{h}"
    )


def _extract_audio(codec: str, quality: str | None = None) -> dict[str, Any]:
    postprocessor: dict[str, Any] = {
        "key": "FFmpegExtractAudio",
        "preferredcodec": codec,
    }
    if quality:
        postprocessor["preferredquality"] = quality
    return {"format": "bestaudio/best", "postprocessors": [postprocessor]}


@dataclass(frozen=True)
class Preset:
    """Un formato seleccionable desde la interfaz."""

    key: str
    label: str
    kind: str  # "video" | "audio" | "custom"
    options: dict[str, Any] = field(default_factory=dict)
    hint: str = ""
    height: int | None = None

    @property
    def needs_ffmpeg(self) -> bool:
        """True si el formato exige unir pistas o convertir el audio."""
        return bool(self.options.get("postprocessors")) or "+" in str(self.options.get("format", ""))


PRESETS: list[Preset] = [
    Preset(
        "auto",
        "Máxima calidad (automático)",
        "video",
        {"format": "bestvideo*+bestaudio/best", "merge_output_format": "mp4"},
        hint="Elige el mejor vídeo y audio disponibles y los une en MP4.",
    ),
    Preset(
        "compat",
        "Máxima calidad MP4 (H.264)",
        "video",
        {"format": _video(), "merge_output_format": "mp4"},
        hint="El mejor formato compatible con cualquier reproductor.",
    ),
    Preset(
        "2160",
        "2160p · 4K MP4",
        "video",
        {"format": _video(2160), "merge_output_format": "mp4"},
        height=2160,
        hint="Hasta 4K, sin superar 2160p de altura.",
    ),
    Preset(
        "1440",
        "1440p · 2K MP4",
        "video",
        {"format": _video(1440), "merge_output_format": "mp4"},
        height=1440,
        hint="Ideal para pantallas QHD.",
    ),
    Preset(
        "1080",
        "1080p · Full HD MP4",
        "video",
        {"format": _video(1080), "merge_output_format": "mp4"},
        height=1080,
        hint="Full HD, la calidad más habitual.",
    ),
    Preset(
        "720",
        "720p · HD MP4",
        "video",
        {"format": _video(720), "merge_output_format": "mp4"},
        height=720,
        hint="Buen equilibrio entre calidad y peso.",
    ),
    Preset(
        "480",
        "480p · SD MP4",
        "video",
        {"format": _video(480), "merge_output_format": "mp4"},
        height=480,
        hint="Calidad estándar, archivos pequeños.",
    ),
    Preset(
        "360",
        "360p · SD ligero MP4",
        "video",
        {"format": _video(360), "merge_output_format": "mp4"},
        height=360,
        hint="Modo ligero para conexiones lentas.",
    ),
    Preset(
        "videoonly",
        "Solo vídeo (sin audio)",
        "video",
        {"format": "bestvideo[ext=mp4][vcodec^=avc1]/bestvideo[ext=mp4]/bestvideo"},
        hint="Descarga únicamente la pista de vídeo.",
    ),
    Preset(
        "audio",
        "Audio original (M4A / Opus)",
        "audio",
        {"format": "bestaudio/best"},
        hint="Mejor audio disponible sin convertir. No requiere ffmpeg.",
    ),
    Preset(
        "mp3_320",
        "MP3 · 320 kbps",
        "audio",
        _extract_audio("mp3", "320"),
        hint="Máxima calidad MP3. Requiere ffmpeg.",
    ),
    Preset(
        "mp3_192",
        "MP3 · 192 kbps",
        "audio",
        _extract_audio("mp3", "192"),
        hint="Buena calidad con menos peso. Requiere ffmpeg.",
    ),
    Preset(
        "mp3_128",
        "MP3 · 128 kbps",
        "audio",
        _extract_audio("mp3", "128"),
        hint="Ligero, ideal para listas de reproducción. Requiere ffmpeg.",
    ),
    Preset(
        "opus",
        "Opus · 160 kbps",
        "audio",
        _extract_audio("opus", "160"),
        hint="Códec de audio moderno y eficiente. Requiere ffmpeg.",
    ),
    Preset(
        "wav",
        "WAV (sin comprimir)",
        "audio",
        _extract_audio("wav"),
        hint="Audio sin pérdida y sin comprimir. Requiere ffmpeg.",
    ),
    Preset(
        CUSTOM,
        "Personalizado (código de formato)",
        "custom",
        hint="Escribe el selector de formato de yt-dlp, p. ej. 720p[ext=mp4].",
    ),
]

PRESETS_BY_KEY: dict[str, Preset] = {p.key: p for p in PRESETS}

COOKIE_BROWSERS: list[tuple[str, str]] = [
    ("", "Sin cookies"),
    ("chrome", "Google Chrome"),
    ("edge", "Microsoft Edge"),
    ("firefox", "Firefox"),
    ("brave", "Brave"),
    ("opera", "Opera"),
    ("vivaldi", "Vivaldi"),
    ("chromium", "Chromium"),
]


@dataclass
class DownloadConfig:
    """Ajustes de una tanda de descargas."""

    output_dir: str
    preset: str = "1080"
    custom_format: str = ""
    include_playlists: bool = True
    write_subtitles: bool = False
    cookies_from_browser: str = ""
    max_workers: int = 2

    @property
    def preset_obj(self) -> Preset:
        return PRESETS_BY_KEY.get(self.preset, PRESETS_BY_KEY["1080"])


def ffmpeg_path() -> str | None:
    """Ruta de ffmpeg si está disponible en el sistema."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for folder in ("ffmpeg/bin", "."):
        for name in ("ffmpeg.exe", "ffmpeg"):
            if shutil.which(name):
                return name
    return None


def js_runtimes() -> dict[str, dict[str, Any]]:
    """Motores de JavaScript instalados, necesarios para sortear algunos avisos."""
    found: dict[str, dict[str, Any]] = {}
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            found[name] = {}
    return found


def build_ydl_options(config: DownloadConfig, logger: Any = None) -> tuple[dict[str, Any], list[str]]:
    """Construye las opciones de ``yt_dlp.YoutubeDL`` y los avisos para el usuario."""
    notes: list[str] = []
    options: dict[str, Any] = {
        "outtmpl": {"default": "%(title).140B [%(id)s].%(ext)s"},
        "paths": {"home": config.output_dir},
        "windowsfilenames": True,
        "trim_file_name": 180,
        "noprogress": True,
        "quiet": True,
        "no_color": True,
        "no_warnings": False,
        "noplaylist": not config.include_playlists,
        "ignoreerrors": False,
        "overwrites": False,
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 20,
    }
    if logger is not None:
        options["logger"] = logger

    if config.write_subtitles:
        options["writesubtitles"] = True
        options["writeautomaticsub"] = True
        options["subtitlesformat"] = "srt/best"
        options["subtitleslangs"] = ["es.*", "en.*", "es-orig", "en-orig"]

    if config.cookies_from_browser:
        options["cookiesfrombrowser"] = (config.cookies_from_browser, None, None, None)

    if config.preset == CUSTOM:
        selector = config.custom_format.strip() or "best"
        options["format"] = selector
    else:
        options.update(config.preset_obj.options)

    if ffmpeg := ffmpeg_path():
        options["ffmpeg_location"] = ffmpeg
    else:
        notes.extend(_degrade_without_ffmpeg(config, options))

    if runtimes := js_runtimes():
        options["js_runtimes"] = runtimes
        options["remote_components"] = ["ejs:github"]

    return options, notes


def _degrade_without_ffmpeg(config: DownloadConfig, options: dict[str, Any]) -> list[str]:
    """Sin ffmpeg se recurre a un único archivo para que la descarga no falle."""
    notes: list[str] = []
    install = "Instálalo con «winget install Gyan.FFmpeg»."
    selector = str(options.get("format", ""))

    if options.get("postprocessors"):
        options.pop("postprocessors", None)
        options["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        notes.append(f"Sin ffmpeg no se puede convertir el audio: se guarda el audio original. {install}")
    elif "+" in selector:
        limit = f"[height<={config.preset_obj.height}]" if config.preset_obj.height else ""
        options["format"] = f"bestvideo{limit}/best"
        options.pop("merge_output_format", None)
        notes.append(
            "Sin ffmpeg no se puede unir el audio con el vídeo: se descargará solo la imagen, sin sonido. "
            f"{install}"
        )
    return notes
