"""Cola de descargas en segundo plano basada en yt-dlp.

El módulo no toca la interfaz: publica eventos en una cola y la GUI los
consume desde el hilo principal de Tk.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

import yt_dlp

from .config import CUSTOM, DownloadConfig, build_ydl_options

_URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)
_TRACKING = re.compile(r"[?&](utm_\w+|feature|si|pp)=[^&]*", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """Saca los enlaces de un bloque de texto, uno (o más) por línea."""
    urls: list[str] = []
    for line in text.splitlines():
        found = _URL_RE.findall(line)
        if not found:
            continue
        for raw in found:
            url = raw.strip().strip(".,;)]}\"'").rstrip(".,;")
            if url not in urls:
                urls.append(url)
    return urls


def human_size(amount: float | None) -> str:
    if not amount or amount < 0:
        return ""
    value = float(amount)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def human_speed(bytes_per_second: float | None) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return ""
    return f"{human_size(bytes_per_second)}/s"


def human_eta(seconds: float | None) -> str:
    if not seconds or seconds < 0 or seconds > 99 * 3600:
        return ""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


class JobState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    MERGING = "merging"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (JobState.DONE, JobState.ERROR, JobState.CANCELLED)


class Cancelled(Exception):
    """Se lanza dentro de los hooks para abortar la descarga en curso."""


@dataclass
class Job:
    """Una línea de la cola de descargas."""

    url: str
    key: str
    position: int
    title: str = ""
    filename: str = ""
    filepath: str = ""
    state: JobState = JobState.PENDING
    progress: float = 0.0
    message: str = "En cola"
    speed: str = ""
    eta: str = ""
    size: str = ""
    total_bytes: int | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    @property
    def label(self) -> str:
        return self.title or shorten(self.url, 60)

    @property
    def status_text(self) -> str:
        return {
            JobState.PENDING: "En cola",
            JobState.RUNNING: "Descargando",
            JobState.MERGING: "Procesando",
            JobState.DONE: "Completado",
            JobState.ERROR: "Error",
            JobState.CANCELLED: "Cancelado",
        }[self.state]


def _needs_postprocess(options: dict[str, Any]) -> bool:
    """¿Las opciones finales obligan a unir pistas o convertir el audio?"""
    return bool(options.get("postprocessors")) or "+" in str(options.get("format", ""))


def _clean_error(error: Exception) -> str:
    message = str(error).strip()
    for prefix in ("ERROR: ", "ERROR: [youtube] "):
        if message.startswith(prefix):
            message = message[len(prefix) :]
    for line in message.splitlines():
        line = line.strip()
        if line and not line.lower().startswith(("please report", "bug report")):
            message = line
            break
    return shorten(message, 220)


class _Logger:
    """Envía los mensajes de yt-dlp a la cola de eventos."""

    def __init__(self, emit: Any) -> None:
        self._emit = emit

    def debug(self, msg: str) -> None:  # pragma: no cover - ruido de yt-dlp
        pass

    def info(self, msg: str) -> None:
        self._emit("log", ("info", shorten(msg, 200)))

    def warning(self, msg: str) -> None:
        self._emit("log", ("warning", shorten(msg, 200)))

    def error(self, msg: str) -> None:
        self._emit("log", ("error", shorten(msg, 200)))


class DownloadManager:
    """Descarga varios enlaces a la vez con un número configurable de hilos."""

    POLL = 0.12

    def __init__(self) -> None:
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._pending: queue.Queue[Job | None] = queue.Queue()
        self._jobs: list[Job] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._config: DownloadConfig | None = None
        self._workers_target = 0
        self._exited = 0
        self._closing = False
        self._batch_total = 0
        self._batch_done = 0
        self._counter = 0

    # ------------------------------------------------------------------ API

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs)

    @property
    def running(self) -> bool:
        return self._workers_target > self._exited

    @property
    def busy(self) -> bool:
        """¿Hay descargas realmente en marcha o la cola está cerrándose?"""
        if any(job.state in (JobState.RUNNING, JobState.MERGING) for job in self._jobs):
            return True
        if not self._pending.empty():
            return True
        return self.running and any(job.state is JobState.PENDING for job in self._jobs)

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def batch(self) -> tuple[int, int]:
        return self._batch_done, self._batch_total

    def add(self, urls: Iterable[str]) -> list[Job]:
        created: list[Job] = []
        for url in urls:
            with self._lock:
                self._counter += 1
                job = Job(url=url, key=f"job-{self._counter}", position=self._counter)
                self._jobs.append(job)
            created.append(job)
            self._emit("added", job)
        return created

    def start(self, config: DownloadConfig) -> None:
        if self.running and not self.wind_down():
            return
        while True:
            try:
                self._pending.get_nowait()
            except queue.Empty:
                break
        self._config = config
        self._stop.clear()
        self._paused.clear()
        self._closing = False
        self._exited = 0
        workers = max(1, min(8, int(config.max_workers or 1)))
        self._workers_target = workers
        queued = [job for job in self._jobs if job.state is JobState.PENDING]
        self._batch_total = len(queued)
        self._batch_done = 0
        if not queued:
            self._emit("finished", None)
            return
        for job in queued:
            self._pending.put(job)
        for _ in range(workers):
            worker = threading.Thread(target=self._worker, name="ytdl", daemon=True)
            worker.start()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def wind_down(self, timeout: float = 2.0) -> bool:
        """Espera a que el grupo de hilos quede libre. Devuelve False si sigue ocupado."""
        if not self.running:
            return True
        self._release_workers()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                return True
            time.sleep(0.02)
        return not self.running

    def cancel(self) -> None:
        """Cancela las descargas en curso y vacía la cola."""
        self._paused.clear()
        self._stop.set()
        for job in self._jobs:
            if not job.state.finished:
                job.cancel.set()
        while True:
            try:
                job = self._pending.get_nowait()
            except queue.Empty:
                break
            if job is None:
                continue
            if not job.state.finished:
                self._set_state(job, JobState.CANCELLED, "Cancelado")
        self._release_workers()

    def reset_pending(self) -> list[Job]:
        """Vuelve a poner en cola todo lo que ya terminó (reintento o cambio de formato)."""
        restored: list[Job] = []
        for job in self._jobs:
            if job.state.finished:
                job.state = JobState.PENDING
                job.progress = 0.0
                job.message = "En cola"
                job.speed = job.eta = ""
                job.cancel.clear()
                restored.append(job)
                self._emit("state", (job, JobState.PENDING, job.message))
        return restored

    def reset(self) -> None:
        """Vacía la cola por completo (solo si no hay descargas en marcha)."""
        if self.running:
            return
        with self._lock:
            self._jobs = []
            self._counter = 0
            self._batch_total = 0
            self._batch_done = 0
        self._emit("reset", None)

    def clear_finished(self) -> list[str]:
        """Quita de la lista las descargas terminadas y devuelve sus claves."""
        with self._lock:
            removed = [j.key for j in self._jobs if j.state.finished]
            if removed:
                self._jobs = [j for j in self._jobs if not j.state.finished]
        return removed

    def clear_all(self) -> list[str]:
        if self.running:
            return []
        with self._lock:
            removed = [j.key for j in self._jobs]
            self._jobs = []
        return removed

    def shutdown(self) -> None:
        self.cancel()

    def drain(self) -> list[tuple[str, Any]]:
        events: list[tuple[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    # -------------------------------------------------------------- internos

    def _emit(self, kind: str, payload: Any) -> None:
        self._events.put((kind, payload))

    def _release_workers(self, count: int | None = None) -> None:
        total = self._workers_target if count is None else count
        for _ in range(max(0, total)):
            self._pending.put(None)

    def _signal_end(self) -> None:
        """Cierra el grupo de hilos cuando ya no queda nada por descargar."""
        with self._lock:
            if self._closing or self._stop.is_set():
                return
            if not self._pending.empty():
                return
            if any(job.state is JobState.PENDING for job in self._jobs):
                return
            self._closing = True
            spare = max(0, self._workers_target - self._exited)
        for _ in range(spare):
            self._pending.put(None)

    def _set_state(self, job: Job, state: JobState, message: str = "") -> None:
        previous = job.state
        job.state = state
        if message:
            job.message = message
        if state.finished and not previous.finished:
            with self._lock:
                self._batch_done += 1
        self._emit("state", (job, state, job.message))

    def _worker(self) -> None:
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._stop.wait(self.POLL)
                    continue
                try:
                    job = self._pending.get(timeout=self.POLL)
                except queue.Empty:
                    continue
                if job is None:
                    return
                self._execute(job)
                self._signal_end()
        finally:
            with self._lock:
                self._exited += 1
                finished = self._exited >= self._workers_target
            if finished:
                self._emit("finished", None)

    def _execute(self, job: Job) -> None:
        config = self._config
        if config is None:
            return
        if job.cancel.is_set() or self._stop.is_set():
            self._set_state(job, JobState.CANCELLED, "Cancelado")
            return

        job.state = JobState.RUNNING
        job.message = "Conectando…"
        self._emit("state", (job, JobState.RUNNING, job.message))

        options, notes = build_ydl_options(config, _Logger(self._emit))
        options["progress_hooks"] = [self._progress_hook(job)]
        options["postprocessor_hooks"] = [self._postprocessor_hook(job, _needs_postprocess(options))]
        for note in notes:
            self._emit("log", ("warning", note))

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(job.url, download=True)
            job.filepath = self._resolve_path(job)
            self._set_state(job, JobState.DONE, self._done_message(info))
        except Cancelled:
            self._set_state(job, JobState.CANCELLED, "Cancelado")
        except yt_dlp.utils.DownloadError as exc:
            self._set_state(job, JobState.ERROR, _clean_error(exc))
        except Exception as exc:  # pragma: no cover - red de seguridad
            self._set_state(job, JobState.ERROR, _clean_error(exc))

    def _done_message(self, info: Any) -> str:
        if isinstance(info, dict) and info.get("_type") == "playlist":
            total = info.get("playlist_count") or 0
            if total:
                return f"Playlist completa ({total} vídeos)"
        return "Completado"

    def _progress_hook(self, job: Job):
        def hook(data: dict) -> None:
            if job.cancel.is_set() or self._stop.is_set():
                raise Cancelled()

            status = data.get("status")
            info = data.get("info_dict") or {}

            if info.get("title"):
                title = info["title"]
                index = data.get("playlist_index")
                total = info.get("playlist_count")
                job.title = f"{index}/{total} · {title}" if index and total else title
            filename = data.get("filename")
            if filename:
                job.filename = filename
            self._emit("info", job)

            if status == "finished":
                job.progress = 100.0
                job.speed, job.eta = "", ""
                self._emit("progress", job)
                return

            if status != "downloading":
                return

            total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes") or 0
            job.total_bytes = total_bytes
            job.size = human_size(total_bytes)
            job.speed = human_speed(data.get("speed"))
            job.eta = human_eta(data.get("eta"))

            index = data.get("playlist_index")
            entries = info.get("playlist_count")
            if index and entries:
                job.progress = min(99.0, (index - 1) / entries * 100)
                job.message = f"Vídeo {index} de {entries}"
            elif total_bytes:
                job.progress = min(99.5, downloaded / total_bytes * 100)
                job.message = f"{human_size(downloaded)} de {job.size}"
            else:
                job.progress = min(99.0, max(job.progress, 1.0))
                job.message = job.speed or "Descargando"

            self._emit("progress", job)

        return hook

    def _postprocessor_hook(self, job: Job, postprocessing: bool):
        def hook(data: dict) -> None:
            if job.cancel.is_set() or self._stop.is_set():
                raise Cancelled()
            if data.get("status") == "started":
                if postprocessing:
                    self._set_state(job, JobState.MERGING, "Uniendo pistas y procesando…")
                else:
                    job.message = "Procesando archivo…"
                    self._emit("info", job)
            elif data.get("status") == "finished" and postprocessing:
                self._set_state(job, JobState.RUNNING, "Finalizando…")

        return hook

    def _resolve_path(self, job: Job) -> str:
        """Localiza el archivo final, aunque ffmpeg haya cambiado la extensión."""
        candidate = job.filename
        if candidate and os.path.exists(candidate):
            return candidate
        if not candidate:
            return ""
        folder = os.path.dirname(candidate) or "."
        stem = os.path.splitext(os.path.basename(candidate))[0]
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            return ""
        for name in entries:
            if name.startswith(stem) and os.path.isfile(os.path.join(folder, name)):
                return os.path.join(folder, name)
        return candidate
