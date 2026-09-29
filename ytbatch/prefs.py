"""Guardado de preferencias entre sesiones."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import DownloadConfig


def prefs_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "ytbatch" / "prefs.json"


def default_download_dir() -> str:
    candidates = [
        Path(os.path.expanduser("~")) / "Downloads",
        Path(os.path.expanduser("~")) / "Descargas",
        Path(os.path.expanduser("~")) / "Desktop",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return str(Path(os.path.expanduser("~")))


def load_prefs() -> dict[str, Any]:
    try:
        with prefs_path().open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(data: dict[str, Any]) -> None:
    try:
        path = prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
    except OSError:
        pass


def prefs_to_config(data: dict[str, Any], fallback_dir: str) -> DownloadConfig:
    return DownloadConfig(
        output_dir=str(data.get("output_dir") or fallback_dir),
        preset=str(data.get("preset") or "1080"),
        custom_format=str(data.get("custom_format") or ""),
        include_playlists=bool(data.get("include_playlists", True)),
        write_subtitles=bool(data.get("write_subtitles", False)),
        cookies_from_browser=str(data.get("cookies_from_browser") or ""),
        max_workers=int(data.get("max_workers") or 2),
    )


def config_to_prefs(config: DownloadConfig) -> dict[str, Any]:
    return {
        "output_dir": config.output_dir,
        "preset": config.preset,
        "custom_format": config.custom_format,
        "include_playlists": config.include_playlists,
        "write_subtitles": config.write_subtitles,
        "cookies_from_browser": config.cookies_from_browser,
        "max_workers": config.max_workers,
    }
