"""Interfaz gráfica del descargador masivo de YouTube."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from typing import Any, Callable

import ttkbootstrap as ttk
from ttkbootstrap import ToolTip
from ttkbootstrap.constants import BOTH, CENTER, LEFT, RIGHT, VERTICAL, W, Y

from .config import (
    COOKIE_BROWSERS,
    CUSTOM,
    PRESETS,
    PRESETS_BY_KEY,
    DownloadConfig,
    ffmpeg_path,
)
from .downloader import DownloadManager, Job, JobState, extract_urls, shorten
from .prefs import (
    config_to_prefs,
    default_download_dir,
    load_prefs,
    prefs_to_config,
    save_prefs,
)

FONT = "Segoe UI"

STATE_STYLES: dict[JobState, str] = {
    JobState.PENDING: "pending",
    JobState.RUNNING: "running",
    JobState.MERGING: "merging",
    JobState.DONE: "done",
    JobState.ERROR: "error",
    JobState.CANCELLED: "cancelled",
}


# --------------------------------------------------------------------- utils


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _shade(color: str, amount: float) -> str:
    """amount > 0 aclara hacia el blanco, amount < 0 oscurece."""
    r, g, b = _rgb(color)
    if amount >= 0:
        return _hex((r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount))
    factor = 1 + amount
    return _hex((r * factor, g * factor, b * factor))


def open_path(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


# -------------------------------------------------------------------- widgets


class ScrollArea(ttk.Frame):
    """Contenedor con desplazamiento vertical para la cola de descargas."""

    def __init__(self, master: tk.Misc, background: str) -> None:
        super().__init__(master, style="dl.card.TFrame")
        self._bg = background
        self.canvas = tk.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            background=background,
        )
        self.scrollbar = ttk.Scrollbar(self, orient=VERTICAL, command=self.canvas.yview, bootstyle="secondary-round")
        self.inner = ttk.Frame(self.canvas, style="dl.card.TFrame")

        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._sync_scrollbar)

        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.scrollbar.pack(side=RIGHT, fill=Y)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def set_background(self, color: str) -> None:
        self._bg = color
        self.canvas.configure(background=color)

    def _sync_scrollbar(self, first: str, last: str) -> None:
        needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        if needed and not self.scrollbar.winfo_ismapped():
            self.scrollbar.pack(side=RIGHT, fill=Y)
        elif not needed and self.scrollbar.winfo_ismapped():
            self.scrollbar.pack_forget()

    def _on_inner_configure(self, _event: tk.Event) -> None:
        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(scrollregion=bbox)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event: tk.Event) -> str:
        if not self.scrollbar.winfo_ismapped():
            return ""
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta * 3, "units")
        return "break"

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)


class DownloadRow(ttk.Frame):
    """Una línea de la cola: título, detalle, porcentaje y barra de progreso."""

    def __init__(self, master: tk.Misc, job: Job, palette: dict[str, str], striped: bool) -> None:
        style = "dl.row2.TFrame" if striped else "dl.row.TFrame"
        super().__init__(master, style=style, padding=(11, 7, 11, 8))
        self.job = job
        self.palette = palette

        self.grid_columnconfigure(1, weight=1)

        self.badge = ttk.Label(self, text=f"{job.position:02d}", style="dl.badge.TLabel", width=4, anchor=CENTER)
        self.badge.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 11))

        self.title = ttk.Label(self, text=shorten(job.label, 50), style="dl.rowtitle.TLabel", anchor=W)
        self.title.grid(row=0, column=1, sticky="ew")
        self._tip = ToolTip(self.title, text=job.url, wraplength=460)

        self.state_label = ttk.Label(self, text=job.status_text, style="dl.muted.TLabel", anchor="e", width=10)
        self.state_label.grid(row=0, column=2, sticky="e", padx=(10, 0))

        self.detail = ttk.Label(self, text=job.message, style="dl.faint.TLabel", anchor=W)
        self.detail.grid(row=1, column=1, sticky="ew", pady=(3, 0))
        self.percent = ttk.Label(self, text="0 %", style="dl.muted.TLabel", anchor="e", width=7)
        self.percent.grid(row=1, column=2, sticky="e", padx=(10, 0))

        self.bar = ttk.Progressbar(
            self,
            style="dl.pending.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.bar.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(7, 0))

    def refresh(self, job: Job) -> None:
        self.job = job
        color = self.palette[STATE_STYLES[job.state]]
        self.title.configure(text=shorten(job.label, 50))
        self._tip.configure(text=job.title or job.url)
        self.state_label.configure(text=job.status_text, foreground=color)
        self.percent.configure(
            text=f"{job.progress:.0f} %" if job.state is JobState.RUNNING else "",
            foreground=color,
        )
        self.detail.configure(text=self._detail_text(job), foreground=self.palette["faint"])
        self.bar.configure(value=job.progress, style=f"dl.{STATE_STYLES[job.state]}.Horizontal.TProgressbar")

    def _detail_text(self, job: Job) -> str:
        if job.state is JobState.PENDING:
            return shorten(job.url, 70)
        if job.state is JobState.DONE:
            parts = [shorten(os.path.basename(job.filepath or job.filename), 55)]
            if job.size:
                parts.append(job.size)
            return " · ".join(p for p in parts if p) or "Completado"
        if job.state in (JobState.ERROR, JobState.CANCELLED):
            return shorten(job.message, 80)
        parts = [p for p in (job.speed, f"ETA {job.eta}" if job.eta else "", job.message) if p]
        return " · ".join(parts) or "Descargando…"


# ---------------------------------------------------------------- aplicación


class BatchDownloaderApp:
    THEMES = ["darkly", "superhero", "cyborg", "litera", "morph"]
    THEME_LABELS = {
        "darkly": "Oscuro",
        "superhero": "Medianoche",
        "cyborg": "Cyber",
        "litera": "Claro",
        "morph": "Claro azul",
    }

    def __init__(self, window: ttk.Window) -> None:
        self.window = window
        self.style = ttk.Style()
        self.manager = DownloadManager()
        self.rows: dict[str, DownloadRow] = {}
        self.palette: dict[str, str] = {}
        self._paused = False
        self._after: str | None = None
        self._prefs = load_prefs()
        self._config = prefs_to_config(self._prefs, default_download_dir())

        self._build()
        self._apply_theme(str(self._prefs.get("theme") or "darkly"))
        self._sync_empty()
        self._sync_buttons()
        self._sync_links()
        self._poll()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.bind("<Control-Return>", lambda _e: self._on_start())
        self.window.bind("<F5>", lambda _e: self._on_start())

    # ----------------------------------------------------------------- estilos

    def _apply_theme(self, theme: str) -> None:
        if theme not in self.THEMES:
            theme = "darkly"
        self.window.style.theme_use(theme)

        style = self.style
        colors = style.colors
        background = str(style.lookup("TFrame", "background") or colors.dark)
        foreground = str(style.lookup("TLabel", "foreground") or colors.light)
        dark = _rgb(background)[0] < 128

        card = _shade(background, 0.045 if dark else -0.03)
        palette = {
            "bg": background,
            "card": card,
            "card_alt": _shade(background, 0.085 if dark else -0.055),
            "border": _shade(background, 0.18 if dark else -0.12),
            "trough": _shade(background, 0.11 if dark else -0.07),
            "text": foreground,
            "muted": _shade(foreground, -0.25 if dark else -0.30),
            "faint": _shade(foreground, -0.42 if dark else -0.45),
            "pending": colors.secondary,
            "running": colors.primary,
            "merging": colors.info,
            "done": colors.success,
            "error": colors.danger,
            "cancelled": colors.warning,
        }
        self.palette = palette

        style.configure("dl.TFrame", background=background)
        style.configure("dl.card.TFrame", background=card)
        style.configure("dl.row.TFrame", background=card)
        style.configure("dl.row2.TFrame", background=palette["card_alt"])

        style.configure(
            "dl.h1.TLabel", background=background, foreground=foreground, font=(FONT, 19, "bold")
        )
        style.configure(
            "dl.sub.TLabel", background=background, foreground=palette["muted"], font=(FONT, 10)
        )
        style.configure(
            "dl.h2.TLabel", background=card, foreground=foreground, font=(FONT, 11, "bold"), padding=(0, 0)
        )
        style.configure(
            "dl.muted.TLabel", background=card, foreground=palette["muted"], font=(FONT, 10), padding=(0, 0)
        )
        style.configure(
            "dl.faint.TLabel", background=card, foreground=palette["faint"], font=(FONT, 9), padding=(0, 0)
        )
        style.configure(
            "dl.hint.TLabel",
            background=card,
            foreground=palette["faint"],
            font=(FONT, 9),
            padding=(0, 0),
            wraplength=340,
        )
        style.configure(
            "dl.rowtitle.TLabel",
            background=card,
            foreground=foreground,
            font=(FONT, 10, "bold"),
            padding=(0, 0),
        )
        style.configure(
            "dl.badge.TLabel",
            background=palette["trough"],
            foreground=palette["muted"],
            font=(FONT, 9, "bold"),
            padding=(0, 2),
        )
        style.configure(
            "dl.status.TLabel", background=card, foreground=palette["muted"], font=(FONT, 9), padding=(0, 0)
        )
        style.configure(
            "dl.warn.TLabel",
            background=card,
            foreground=colors.warning,
            font=(FONT, 9),
            padding=(0, 0),
            wraplength=400,
        )

        for key, color in (
            ("pending", palette["pending"]),
            ("running", palette["running"]),
            ("merging", palette["merging"]),
            ("done", palette["done"]),
            ("error", palette["error"]),
            ("cancelled", palette["cancelled"]),
        ):
            name = f"dl.{key}.Horizontal.TProgressbar"
            style.configure(
                name,
                thickness=6,
                borderwidth=0,
                troughcolor=palette["trough"],
                background=color,
                lightcolor=color,
                darkcolor=color,
                bordercolor=color,
            )
            style.layout(name, style.layout("Horizontal.TProgressbar"))

        style.configure(
            "dl.total.Horizontal.TProgressbar",
            thickness=8,
            borderwidth=0,
            troughcolor=palette["trough"],
            background=colors.primary,
            lightcolor=colors.primary,
            darkcolor=colors.primary,
            bordercolor=colors.primary,
        )
        style.layout("dl.total.Horizontal.TProgressbar", style.layout("Horizontal.TProgressbar"))

        for widget in (self.link_text,):
            widget.configure(background=card, foreground=foreground, insertbackground=foreground)
        if self.scroll_area is not None:
            self.scroll_area.set_background(card)

        for row in self.rows.values():
            row.palette = self.palette
            row.refresh(row.job)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        self.container = ttk.Frame(self.window, style="dl.TFrame", padding=(20, 16, 20, 16))
        self.container.pack(fill=BOTH, expand=True)
        self.container.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_options()
        self._build_body()
        self._build_footer()

    def _build_header(self) -> None:
        header = ttk.Frame(self.container, style="dl.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(1, weight=1)

        titles = ttk.Frame(header, style="dl.TFrame")
        titles.grid(row=0, column=0, sticky=W)
        ttk.Label(titles, text="Descargador de YouTube", style="dl.h1.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Label(
            titles,
            text="Pega los enlaces, elige el formato y descarga todos de una vez.",
            style="dl.sub.TLabel",
        ).grid(row=1, column=0, sticky=W, pady=(3, 0))

        side = ttk.Frame(header, style="dl.TFrame")
        side.grid(row=0, column=2, sticky="e")
        ttk.Label(side, text="Tema", style="dl.sub.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.theme_box = ttk.Combobox(
            side,
            values=[self.THEME_LABELS[t] for t in self.THEMES],
            state="readonly",
            width=12,
            bootstyle="secondary",
        )
        self.theme_box.grid(row=0, column=1)
        self.theme_box.set(self.THEME_LABELS.get(str(self._prefs.get("theme") or ""), "Oscuro"))
        self.theme_box.bind("<<ComboboxSelected>>", self._on_theme_selected)
        self._theme_keys = {self.THEME_LABELS[t]: t for t in self.THEMES}

    # ---------------------------------------------------------------- opciones

    def _build_options(self) -> None:
        card = ttk.Frame(self.container, style="dl.card.TFrame", padding=16)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=4, minsize=300)
        card.grid_columnconfigure(1, weight=6, minsize=280)
        card.grid_columnconfigure(2, weight=0, minsize=140)
        card.grid_columnconfigure(3, weight=3, minsize=170)

        # -- formato
        self.format_box = ttk.Combobox(
            card,
            values=[preset.label for preset in PRESETS],
            state="readonly",
            width=32,
        )
        self.format_box.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        self.format_box.bind("<<ComboboxSelected>>", lambda _e: self._on_format_selected())
        self._format_keys = {preset.label: preset.key for preset in PRESETS}
        self._current_preset = self._config.preset
        self.format_box.set(
            PRESETS_BY_KEY[self._current_preset].label if self._current_preset in PRESETS_BY_KEY else PRESETS[4].label
        )
        ttk.Label(card, text="Formato de descarga", style="dl.hint.TLabel").grid(
            row=0, column=0, sticky=W, pady=(0, 5)
        )
        self.hint_label = ttk.Label(card, text="", style="dl.hint.TLabel")
        self.hint_label.grid(row=2, column=0, sticky=W, pady=(6, 0), padx=(0, 12))

        # -- formato personalizado
        self.custom_var = tk.StringVar(value=self._config.custom_format)
        self.custom_entry = ttk.Entry(card, textvariable=self.custom_var, bootstyle="secondary")
        self.custom_entry.grid(row=3, column=0, sticky="ew", pady=(8, 0), padx=(0, 12))
        ttk.Label(card, text="Selector de formato", style="dl.faint.TLabel").grid(
            row=4, column=0, sticky=W, pady=(4, 0)
        )

        # -- carpeta
        self.folder_var = tk.StringVar(value=self._config.output_dir)
        folder_box = ttk.Frame(card, style="dl.card.TFrame")
        folder_box.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        folder_box.grid_columnconfigure(0, weight=1)
        self.folder_entry = ttk.Entry(folder_box, textvariable=self.folder_var, bootstyle="secondary")
        self.folder_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            folder_box,
            text="Carpeta…",
            bootstyle="secondary-outline",
            width=10,
            command=self._on_pick_folder,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(card, text="Guardar en", style="dl.hint.TLabel").grid(row=0, column=1, sticky=W, pady=(0, 5))

        # -- simultáneas
        self.workers_var = tk.IntVar(value=max(1, min(8, self._config.max_workers)))
        self.workers_box = ttk.Spinbox(
            card,
            from_=1,
            to=8,
            textvariable=self.workers_var,
            width=6,
            bootstyle="secondary",
            command=self._on_workers_changed,
        )
        self.workers_box.grid(row=1, column=2, sticky=W)
        ttk.Label(card, text="A la vez", style="dl.hint.TLabel").grid(row=0, column=2, sticky=W, pady=(0, 5))

        # -- cookies
        self.cookies_box = ttk.Combobox(
            card,
            values=[label for _key, label in COOKIE_BROWSERS],
            state="readonly",
            width=18,
            bootstyle="secondary",
        )
        self.cookies_box.grid(row=1, column=3, sticky="ew")
        self._cookie_keys = {label: key for key, label in COOKIE_BROWSERS}
        browser = self._config.cookies_from_browser
        self.cookies_box.set(self._cookie_keys.get(browser, "Sin cookies"))
        ttk.Label(card, text="Cookies del navegador", style="dl.hint.TLabel").grid(
            row=0, column=3, sticky=W, pady=(0, 5)
        )

        # -- fila de interruptores
        switches = ttk.Frame(card, style="dl.card.TFrame")
        switches.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        self.playlists_var = tk.BooleanVar(value=self._config.include_playlists)
        self.subs_var = tk.BooleanVar(value=self._config.write_subtitles)
        ttk.Checkbutton(
            switches,
            text="Incluir playlists completas",
            variable=self.playlists_var,
            bootstyle="secondary-round-toggle",
        ).grid(row=0, column=0, sticky=W)
        ttk.Checkbutton(
            switches,
            text="Descargar subtítulos",
            variable=self.subs_var,
            bootstyle="secondary-round-toggle",
        ).grid(row=0, column=1, sticky=W, padx=(24, 0))
        switches.grid_columnconfigure(2, weight=1)

        self.ffmpeg_note = ttk.Label(card, text="", style="dl.warn.TLabel")
        self.ffmpeg_note.grid(row=6, column=0, columnspan=4, sticky=W, pady=(10, 0))
        self._on_format_selected()
        self._update_ffmpeg_note()

    # ------------------------------------------------------------------ cuerpo

    def _build_body(self) -> None:
        body = ttk.Frame(self.container, style="dl.TFrame")
        body.grid(row=2, column=0, sticky="nsew", pady=(0, 14))
        self.container.grid_rowconfigure(2, weight=1)
        body.grid_columnconfigure(0, weight=9, uniform="body")
        body.grid_columnconfigure(1, weight=11)
        body.grid_rowconfigure(0, weight=1)

        # -- enlaces
        links = ttk.Frame(body, style="dl.card.TFrame", padding=(16, 14, 16, 14))
        links.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        links.grid_columnconfigure(0, weight=1)
        links.grid_rowconfigure(1, weight=1)

        head = ttk.Frame(links, style="dl.card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        head.grid_columnconfigure(1, weight=1)
        ttk.Label(head, text="1 · Enlaces", style="dl.h2.TLabel").grid(row=0, column=0, sticky=W)
        self.link_count = ttk.Label(head, text="0 enlaces", style="dl.muted.TLabel")
        self.link_count.grid(row=0, column=2, sticky="e")

        self.link_text = ttk.ScrolledText(
            links,
            height=8,
            width=36,
            auto_hide=True,
            wrap="word",
            undo=True,
            font=(FONT, 10),
            padx=10,
            pady=8,
            spacing1=1,
            spacing3=3,
            insertwidth=2,
        )
        self.link_text.grid(row=1, column=0, sticky="nsew")
        self.link_text.bind("<<Modified>>", self._on_links_modified)
        self.link_text.edit_modified(False)

        tools = ttk.Frame(links, style="dl.card.TFrame")
        tools.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(tools, text="Pegar", bootstyle="secondary", command=self._on_paste).grid(row=0, column=0)
        ttk.Button(tools, text="Limpiar", bootstyle="secondary-outline", command=self._on_clear_links).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Label(tools, text="Un enlace por línea", style="dl.faint.TLabel").grid(row=0, column=2, sticky="e")
        tools.grid_columnconfigure(2, weight=1)

        # -- cola
        queue = ttk.Frame(body, style="dl.card.TFrame", padding=(16, 14, 16, 12))
        queue.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        queue.grid_columnconfigure(0, weight=1)
        queue.grid_rowconfigure(1, weight=1)

        qhead = ttk.Frame(queue, style="dl.card.TFrame")
        qhead.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        qhead.grid_columnconfigure(1, weight=1)
        ttk.Label(qhead, text="2 · Cola de descargas", style="dl.h2.TLabel").grid(row=0, column=0, sticky=W)
        self.queue_summary = ttk.Label(qhead, text="Vacía", style="dl.muted.TLabel")
        self.queue_summary.grid(row=0, column=2, sticky="e")

        self.scroll_area: ScrollArea | None = None
        self.empty_label = ttk.Label(
            queue,
            text="Aún no hay nada en la cola.\nPega enlaces y pulsa «Iniciar descargas».",
            style="dl.faint.TLabel",
            anchor=CENTER,
            justify=CENTER,
        )
        self.empty_label.grid(row=1, column=0, sticky="nsew")
        self.scroll_area = ScrollArea(queue, "#000000")
        self.scroll_area.grid(row=1, column=0, sticky="nsew")
        self.scroll_area.inner.grid_columnconfigure(0, weight=1)

    # ------------------------------------------------------------------ footer

    def _build_footer(self) -> None:
        card = ttk.Frame(self.container, style="dl.card.TFrame", padding=(16, 12, 16, 12))
        card.grid(row=3, column=0, sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        left = ttk.Frame(card, style="dl.card.TFrame")
        left.grid(row=0, column=0, sticky=W)
        self.start_btn = ttk.Button(
            left, text="Iniciar descargas", bootstyle="success", width=20, command=self._on_start
        )
        self.start_btn.grid(row=0, column=0)
        self.pause_btn = ttk.Button(left, text="Pausar", bootstyle="warning", width=10, command=self._on_pause)
        self.pause_btn.grid(row=0, column=1, padx=(8, 0))
        self.cancel_btn = ttk.Button(left, text="Cancelar", bootstyle="danger", width=10, command=self._on_cancel)
        self.cancel_btn.grid(row=0, column=2, padx=(8, 0))

        right = ttk.Frame(card, style="dl.card.TFrame")
        right.grid(row=0, column=2, sticky="e")
        self.prune_btn = ttk.Button(
            right, text="Quitar terminados", bootstyle="secondary-outline", command=self._on_prune
        )
        self.prune_btn.grid(row=0, column=0)
        ttk.Button(
            right, text="Abrir carpeta", bootstyle="secondary", command=lambda: open_path(self.folder_var.get())
        ).grid(row=0, column=1, padx=(8, 0))

        progress = ttk.Frame(card, style="dl.card.TFrame")
        progress.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        progress.grid_columnconfigure(1, weight=1)
        self.total_bar = ttk.Progressbar(
            progress,
            style="dl.total.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.total_bar.grid(row=0, column=1, sticky="ew", padx=(12, 12))
        self.total_label = ttk.Label(progress, text="0 de 0", style="dl.muted.TLabel", width=12, anchor="e")
        self.total_label.grid(row=0, column=2, sticky="e")

        self.status_label = ttk.Label(card, text="Listo para empezar.", style="dl.status.TLabel", anchor=W)
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))

    # -------------------------------------------------------------- acciones UI

    def _on_theme_selected(self, _event: tk.Event | None = None) -> None:
        theme = self._theme_keys.get(self.theme_box.get(), "darkly")
        self._prefs["theme"] = theme
        self._apply_theme(theme)

    def _on_format_selected(self) -> None:
        preset = PRESETS_BY_KEY.get(self._format_keys.get(self.format_box.get(), "1080"), PRESETS[0])
        self.hint_label.configure(text=preset.hint)
        if preset.key == CUSTOM:
            self.custom_entry.grid()
        else:
            self.custom_entry.grid_remove()
        self._update_ffmpeg_note()

    def _update_ffmpeg_note(self) -> None:
        if ffmpeg_path():
            self.ffmpeg_note.configure(text="")
            return
        preset = PRESETS_BY_KEY.get(self._format_keys.get(self.format_box.get(), "1080"))
        if preset and (preset.needs_ffmpeg or preset.kind == "video"):
            note = "ffmpeg no está instalado: se necesita para unir el audio y convertir a MP3."
        else:
            note = "ffmpeg no está instalado: la conversión de audio no estará disponible."
        self.ffmpeg_note.configure(text=note)

    def _on_pick_folder(self) -> None:
        from tkinter import filedialog

        folder = filedialog.askdirectory(
            title="Elige la carpeta de destino",
            initialdir=self.folder_var.get() or default_download_dir(),
        )
        if folder:
            self.folder_var.set(folder)

    def _on_paste(self) -> None:
        try:
            content = self.window.clipboard_get()
        except tk.TclError:
            self._set_status("El portapapeles está vacío.")
            return
        if not content.strip():
            return
        current = self.link_text.get("1.0", "end").strip()
        merged = f"{current}\n{content.strip()}" if current else content.strip()
        self._set_links(merged)
        self._set_status("Enlaces pegados desde el portapapeles.")

    def _on_clear_links(self) -> None:
        self._set_links("")
        self._set_status("Lista de enlaces vaciada.")

    def _set_links(self, content: str) -> None:
        self.link_text.delete("1.0", "end")
        self.link_text.insert("1.0", content)
        self.link_text.edit_modified(False)
        self._sync_links()

    def _on_links_modified(self, _event: tk.Event | None = None) -> None:
        if not self.link_text.edit_modified():
            return
        self.link_text.edit_modified(False)
        self._sync_links()

    def _sync_links(self) -> None:
        urls = extract_urls(self.link_text.get("1.0", "end"))
        lines = [l for l in self.link_text.get("1.0", "end").splitlines() if l.strip()]
        self.link_count.configure(
            text=f"{len(urls)} enlace{'s' if len(urls) != 1 else ''}" if urls else "Sin enlaces"
        )
        self._pending_urls = urls
        if lines and not urls:
            self.link_count.configure(text="No se detectaron enlaces")
        self._sync_buttons()

    def _on_workers_changed(self) -> None:
        try:
            value = int(self.workers_var.get())
        except (tk.TclError, ValueError):
            value = 2
        self.workers_var.set(max(1, min(8, value)))

    def _on_start(self) -> None:
        from ttkbootstrap.dialogs import Messagebox

        if self.manager.busy:
            self._set_status("Espera a que terminen las descargas en marcha.")
            return
        self.manager.wind_down()

        urls = getattr(self, "_pending_urls", []) or extract_urls(self.link_text.get("1.0", "end"))
        if not urls and not self.manager.jobs:
            self._set_status("Pega al menos un enlace de YouTube para empezar.")
            Messagebox.show_info_message("Faltan enlaces", "Pega al menos un enlace de YouTube.", self.window)
            return

        if urls != [job.url for job in self.manager.jobs]:
            self.manager.reset()
            self.manager.add(urls)
        else:
            self.manager.reset_pending()
        if not self.manager.jobs:
            self._set_status("No hay nada que descargar.")
            return

        folder = self.folder_var.get().strip()
        if not folder:
            folder = default_download_dir()
            self.folder_var.set(folder)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            Messagebox.show_error_message("Carpeta no válida", str(exc), self.window)
            return

        self._config = DownloadConfig(
            output_dir=folder,
            preset=self._format_keys.get(self.format_box.get(), "1080"),
            custom_format=self.custom_var.get(),
            include_playlists=bool(self.playlists_var.get()),
            write_subtitles=bool(self.subs_var.get()),
            cookies_from_browser=self._cookie_keys.get(self.cookies_box.get(), ""),
            max_workers=self._workers_var(),
        )
        self._paused = False
        self.manager.start(self._config)
        self._set_status("Descargando…")
        self._sync_buttons()

    def _workers_var(self) -> int:
        try:
            return max(1, min(8, int(self.workers_var.get())))
        except (tk.TclError, ValueError):
            return 2

    def _on_pause(self) -> None:
        if self._paused:
            self.manager.resume()
            self._paused = False
            self._set_status("Reanudando descargas…")
        else:
            self.manager.pause()
            self._paused = True
            self._set_status("En pausa: las descargas actuales terminan y la cola espera.")
        self._sync_buttons()

    def _on_cancel(self) -> None:
        if not self.manager.running:
            return
        self.manager.cancel()
        self._paused = False
        self._set_status("Descargas canceladas.")

    def _on_prune(self) -> None:
        for key in self.manager.clear_finished():
            row = self.rows.pop(key, None)
            if row is not None:
                row.destroy()
        self._restripe()
        self._sync_empty()
        self._sync_buttons()
        self._set_status("Se han quitado las descargas terminadas.")

    def _restripe(self) -> None:
        for index, key in enumerate(self.rows):
            row = self.rows[key]
            row.configure(style="dl.row2.TFrame" if index % 2 else "dl.row.TFrame")

    # ---------------------------------------------------------------- eventos

    def _poll(self) -> None:
        try:
            for kind, payload in self.manager.drain():
                handler = getattr(self, f"_on_event_{kind}", None)
                if handler is not None:
                    handler(payload)
        except Exception as exc:  # pragma: no cover
            self._set_status(f"Error inesperado: {exc}")
        self._after = self.window.after(90, self._poll)

    def _on_event_added(self, job: Job) -> None:
        row = DownloadRow(self.scroll_area.inner, job, self.palette, len(self.rows) % 2 == 1)
        row.grid(row=len(self.rows), column=0, sticky="ew", pady=(0, 8))
        self.rows[job.key] = row
        self._sync_empty()
        self._sync_buttons()

    def _on_event_reset(self, _payload: Any) -> None:
        for row in self.rows.values():
            row.destroy()
        self.rows.clear()
        if self.scroll_area is not None:
            self.scroll_area.scroll_to_top()
        self._sync_empty()
        self._sync_buttons()

    def _on_event_info(self, job: Job) -> None:
        row = self.rows.get(job.key)
        if row is not None:
            row.refresh(job)
            self._sync_total()

    def _on_event_progress(self, job: Job) -> None:
        row = self.rows.get(job.key)
        if row is not None:
            row.refresh(job)

    def _on_event_state(self, payload: tuple) -> None:
        job, state, _message = payload
        row = self.rows.get(job.key)
        if row is not None:
            row.refresh(job)
        if state is JobState.DONE:
            self._set_status(f"Listo: {shorten(job.label, 60)}")
        elif state is JobState.ERROR:
            self._set_status(f"Error en {shorten(job.label, 45)}: {shorten(job.message, 80)}")
        self._sync_total()
        self._sync_buttons()

    def _on_event_log(self, payload: tuple) -> None:
        level, message = payload
        if level in ("error", "warning"):
            self._set_status(message)

    def _on_event_finished(self, _payload: Any) -> None:
        self._paused = False
        done, total = self.manager.batch
        self._set_status(f"Listo. {done} de {total} descargas completadas.")
        self._sync_buttons()

    # ------------------------------------------------------------- indicadores

    def _set_status(self, message: str) -> None:
        self.status_label.configure(text=shorten(message, 150))
        self.status_label.configure(foreground=self.palette["muted"])

    def _sync_empty(self) -> None:
        empty = not self.rows
        if empty:
            self.empty_label.grid()
            if self.scroll_area is not None:
                self.scroll_area.grid_remove()
        else:
            self.empty_label.grid_remove()
            if self.scroll_area is not None:
                self.scroll_area.grid()

    def _sync_total(self) -> None:
        done, total = self.manager.batch
        jobs = self.manager.jobs
        self.total_label.configure(text=f"{done} de {total}")
        value = (done / total * 100) if total else 0
        self.total_bar.configure(value=value)
        pending = sum(1 for job in jobs if not job.state.finished)
        if not jobs:
            self.queue_summary.configure(text="Vacía")
        else:
            self.queue_summary.configure(text=f"{len(jobs)} en la lista · {pending} pendientes")

    def _sync_buttons(self) -> None:
        busy = self.manager.busy
        jobs = self.manager.jobs
        pending = [job for job in jobs if job.state is JobState.PENDING]
        finished = [job for job in jobs if job.state.finished]
        has_links = bool(getattr(self, "_pending_urls", []))

        if busy:
            label, start_state = "Descargando…", "disabled"
        elif pending:
            label, start_state = "Continuar descargas", "normal"
        elif jobs:
            label, start_state = "Volver a descargar", "normal"
        elif has_links:
            label, start_state = "Iniciar descargas", "normal"
        else:
            label, start_state = "Iniciar descargas", "disabled"
        self.start_btn.configure(text=label, state=start_state)
        self.pause_btn.configure(
            state="normal" if busy else "disabled", text="Reanudar" if self._paused else "Pausar"
        )
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.prune_btn.configure(state="normal" if finished else "disabled")
        self._sync_total()

    # ------------------------------------------------------------------ cierre

    def _save_prefs(self) -> None:
        data = config_to_prefs(self._config)
        data["theme"] = self._prefs.get("theme", "darkly")
        save_prefs(data)

    def _on_close(self) -> None:
        if self.manager.running:
            from ttkbootstrap.dialogs import Messagebox

            answer = Messagebox.yes_no(
                "Todavía hay descargas en marcha",
                "Si sales ahora se cancelarán. ¿Quieres cerrar de todas formas?",
                buttons=["primary:Salir y cancelar", "secondary:Seguir descargando"],
                parent=self.window,
            )
            if str(answer).lower() != "salir y cancelar":
                return
            self.manager.shutdown()
        self._save_prefs()
        if self._after is not None:
            try:
                self.window.after_cancel(self._after)
            except tk.TclError:
                pass
        self.window.destroy()


def launch() -> None:
    window = ttk.Window(
        themename="darkly",
        title="Descargador de YouTube",
        size=(1240, 840),
        minsize=(1000, 660),
    )
    BatchDownloaderApp(window)
    window.mainloop()
