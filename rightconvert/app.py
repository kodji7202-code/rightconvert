"""Native Tk/ttk desktop UI. File data never leaves the local machine."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import queue
import stat
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import read_settings, save_settings, technical_logger
from .engine import combine_images_to_pdf, convert_file
from .formats import available_encoders, inspect_source, media_tools, supported_targets
from .office_provider import find_libreoffice


DARK_COLORS = {
    "bg": "#12151c", "surface": "#1b202a", "surface_alt": "#242b37",
    "fg": "#f2f5f9", "muted": "#b6c2d2", "line": "#343d49",
    "accent": "#80aaff", "button_text": "#0c1831", "danger": "#ff9292",
    "selection": "#303d53",
}
LIGHT_COLORS = {
    "bg": "#f5f7fa", "surface": "#ffffff", "surface_alt": "#eef2f7",
    "fg": "#1b2432", "muted": "#526072", "line": "#dfe5ed",
    "accent": "#266fe0", "button_text": "#ffffff", "danger": "#b33b45",
    "selection": "#dfeafa",
}

# Explicitly bounded folder scans: untrusted directory trees must not trigger
# recursive stack exhaustion, unbounded queue growth, or junction traversal.
FOLDER_MAX_FILES = 1000
FOLDER_MAX_ENTRIES = 10000
FOLDER_MAX_DEPTH = 12

# These are output-format choices only. Codec parameters remain in the engine.
PRESET_TARGETS = {
    "Automat": ("", ""),
    "Imagini → WebP": ("image", "webp"),
    "Imagini → PNG": ("image", "png"),
    "Imagini → PDF": ("image", "pdf"),
    "PDF → PNG ZIP (pagini)": ("pdf", "pngzip"),
    "PDF → TIFF": ("pdf", "tiff"),
    "Office → PDF": ("office", "pdf"),
    "Audio → MP3": ("audio", "mp3"),
    "Audio → FLAC": ("audio", "flac"),
    "Video → WebM": ("video", "webm"),
    "Video → MP4": ("video", "mp4"),
}
IMAGE_SOURCES = frozenset(("png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "gif"))
AUDIO_SOURCES = frozenset(("wav", "flac", "mp3", "ogg", "opus"))
VIDEO_SOURCES = frozenset(("mp4", "webm", "mov", "mkv", "avi"))
OFFICE_SOURCES = frozenset(("docx", "xlsx", "pptx", "odt", "ods", "odp"))
TARGET_LABELS = {"pngzip": "PNG ZIP (pagini)"}
MAX_COMBINED_PDF_IMAGES = 64  # PDF provider's MAX_PDF_PAGES guard


def _target_label(target: str) -> str:
    return TARGET_LABELS.get(target, target.upper())


def _target_value(label: str) -> str:
    return next((target for target, title in TARGET_LABELS.items()
                 if title.casefold() == label.casefold()), label.lower())


def _source_family(source_format: str) -> str:
    if source_format in IMAGE_SOURCES:
        return "image"
    if source_format in AUDIO_SOURCES:
        return "audio"
    if source_format in VIDEO_SOURCES:
        return "video"
    if source_format == "pdf":
        return "pdf"
    if source_format in OFFICE_SOURCES:
        return "office"
    return ""


@dataclass
class FolderScanStats:
    root: Path | None = None
    entries: int = 0
    files: int = 0
    skipped_links: int = 0
    errors: int = 0
    truncated: bool = False


def _is_link_or_reparse(info: os.stat_result) -> bool:
    """Reject POSIX symlinks and all Windows reparse points, incl. junctions."""
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _walk_folder_files(
    root: Path,
    cancelled: threading.Event,
    stats: FolderScanStats,
    *,
    max_files: int = FOLDER_MAX_FILES,
    max_entries: int = FOLDER_MAX_ENTRIES,
    max_depth: int = FOLDER_MAX_DEPTH,
):
    """Yield regular in-root files, without following links or reparse points.

    All scanning runs on the importer thread. Skips inaccessible descendants,
    tracks visited directory identities, and stops at explicit resource caps.
    """
    if not root.is_dir() or _is_link_or_reparse(root.lstat()):
        raise ValueError("Alege un folder real, nu un link sau junction")
    resolved_root = root.resolve(strict=True)
    stack = [(root, 0)]
    visited: set[tuple[int, int]] = set()
    while stack and not cancelled.is_set():
        folder, depth = stack.pop()
        try:
            info = folder.lstat()
            if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                stats.skipped_links += 1
                continue
            if not folder.resolve(strict=True).is_relative_to(resolved_root):
                stats.skipped_links += 1
                continue
            key = (info.st_dev, info.st_ino)
            if key in visited:
                continue
            visited.add(key)
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    if cancelled.is_set():
                        return
                    if stats.entries >= max_entries:
                        stats.truncated = True
                        return
                    stats.entries += 1
                    try:
                        details = entry.stat(follow_symlinks=False)
                        if _is_link_or_reparse(details):
                            stats.skipped_links += 1
                            continue
                        path = Path(entry.path)
                        if stat.S_ISDIR(details.st_mode):
                            if depth >= max_depth:
                                stats.truncated = True
                            else:
                                stack.append((path, depth + 1))
                        elif stat.S_ISREG(details.st_mode):
                            if not path.resolve(strict=True).is_relative_to(resolved_root):
                                stats.skipped_links += 1
                                continue
                            if stats.files >= max_files:
                                stats.truncated = True
                                return
                            stats.files += 1
                            yield path
                    except (OSError, RuntimeError, ValueError):
                        stats.errors += 1
        except (OSError, RuntimeError, ValueError):
            stats.errors += 1


@dataclass
class SelectedFile:
    file: Path
    target: str
    options: list[str]
    status: str = "Pregătit"
    source_format: str = ""
    error_detail: str = ""
    display_name: str = ""


def _inspect_candidate(path: Path, ffmpeg_path: str | None,
                       office_path: str | None = None) -> tuple[list[str], str]:
    """Sniff once for both real source format and compatible output targets."""
    if not path.is_file():
        return [], ""
    info = inspect_source(path, ffmpeg_path)
    if info is None:
        return [], ""
    return supported_targets(info, ffmpeg_path, office_path), info.format


def _identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def windows_dark_mode() -> bool:
    """Read the Windows application theme; use light theme on other platforms."""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except (OSError, ValueError):
        return False


class RightConvertApp:
    def __init__(self, files: list[Path] | None = None, preferred_target: str | None = None,
                 auto_start: bool = False):
        self.settings = read_settings()
        self.log = technical_logger()
        self.dark = windows_dark_mode()
        self.colors = DARK_COLORS.copy() if self.dark else LIGHT_COLORS.copy()
        self.root = tk.Tk()
        self.root.title("RightConvert  ·  Conversii locale")
        self.root.geometry("960x640")
        # At 560px height the footer progress bar was clipped by Windows
        # caption/DPI rounding. Keep the entire action/status area visible.
        self.root.minsize(780, 600)
        self.root.configure(background=self.colors["bg"])
        self._set_window_icon()
        self._set_native_titlebar()
        self.files: dict[str, SelectedFile] = {}
        self.next_id = 1
        self.events: queue.Queue[tuple] = queue.Queue()
        self.import_events: queue.Queue[tuple] = queue.Queue(maxsize=64)
        self.import_cancel = threading.Event()
        self.importing = False
        self.cancel_signal = threading.Event()
        self.running = False
        self.close_requested = False
        self.preferred_target = preferred_target.lower().lstrip(".") if isinstance(preferred_target, str) else None
        self.auto_start_pending = bool(auto_start and files)
        self.auto_excluded = 0
        self.same_folder_var = tk.BooleanVar(value=bool(self.settings.get("same_folder", True)))
        self.folder_var = tk.StringVar(value=self.settings.get("output_dir", ""))
        self.folder_hint_var = tk.StringVar(value="Rezultatele se salvează lângă fiecare fișier original")
        self.bulk_var = tk.StringVar(value="Format pentru toate…")
        self.preset_var = tk.StringVar(value="Automat")
        self.summary_var = tk.StringVar(value="Adaugă fișiere pentru a începe.")
        self.progress_var = tk.DoubleVar(value=0)
        self._theme_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(90, self._consume_events)
        if files:
            # A single Explorer file can spend up to 20 seconds in FFprobe.
            # All CLI selections, including one file, use the bounded importer
            # so Tk can enter its event loop with a visible loading state.
            self.queue_files(files)

    def _set_native_titlebar(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            value = ctypes.c_int(1 if self.dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(self.root.winfo_id(), 20, ctypes.byref(value), ctypes.sizeof(value))
        except (OSError, AttributeError):
            pass

    def _set_window_icon(self) -> None:
        try:
            from PIL import Image, ImageDraw, ImageTk

            icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            pen = ImageDraw.Draw(icon)
            pen.rounded_rectangle((1, 1, 63, 63), radius=15, fill="#266FE0")
            pen.line([(14, 23), (50, 23), (42, 15)], fill="white", width=5)
            pen.line([(50, 23), (42, 31)], fill="white", width=5)
            pen.line([(50, 41), (14, 41), (22, 33)], fill="white", width=5)
            pen.line([(14, 41), (22, 49)], fill="white", width=5)
            self.window_icon = ImageTk.PhotoImage(icon)
            self.root.iconphoto(True, self.window_icon)
        except (ImportError, tk.TclError):
            pass

    def _theme_style(self) -> None:
        c = self.colors
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("RC.Treeview", font=("Segoe UI", 10), rowheight=42,
                        background=c["surface"], foreground=c["fg"], fieldbackground=c["surface"],
                        borderwidth=0, relief="flat")
        style.map("RC.Treeview", background=[("selected", c["selection"])], foreground=[("selected", c["fg"])])
        style.configure("RC.Treeview.Heading", font=("Segoe UI Semibold", 10),
                        foreground=c["muted"], background=c["surface_alt"], padding=(12, 11), borderwidth=0)
        style.map("RC.Treeview.Heading", background=[("active", c["surface_alt"])])
        style.configure("RC.Horizontal.TProgressbar", background=c["accent"], troughcolor=c["surface_alt"], borderwidth=0)
        style.configure("RC.TCombobox", foreground=c["fg"], fieldbackground=c["surface_alt"], background=c["surface_alt"],
                        arrowcolor=c["fg"], bordercolor=c["line"], padding=8)
        style.map("RC.TCombobox", fieldbackground=[("readonly", c["surface_alt"])],
                  foreground=[("readonly", c["fg"])])

    def _button(self, parent: tk.Misc, title: str, command, *, accent=False, danger=False, width=None) -> tk.Button:
        c = self.colors
        background = c["accent"] if accent else c["surface_alt"]
        fg = c["button_text"] if accent else c["danger"] if danger else c["fg"]
        return tk.Button(parent, text=title, command=command, bg=background, fg=fg,
                         activebackground=c["accent"] if accent else c["line"], activeforeground=fg,
                         font=("Segoe UI Semibold", 10), bd=0, relief="flat", cursor="hand2",
                         padx=17, pady=11, width=width, disabledforeground=c["muted"], highlightthickness=0)

    def _label(self, parent, text: str, *, size=10, color="fg", bold=False, background="surface") -> tk.Label:
        return tk.Label(parent, text=text, bg=self.colors[background], fg=self.colors[color],
                        font=("Segoe UI Semibold" if bold else "Segoe UI", size), anchor="w")

    def _build_ui(self) -> None:
        c = self.colors
        body = tk.Frame(self.root, bg=c["bg"], padx=28, pady=23)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1, minsize=180)

        header = tk.Frame(body, bg=c["bg"])
        header.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        brand = tk.Frame(header, bg=c["bg"])
        brand.pack(side="left")
        emblem = tk.Canvas(brand, width=44, height=44, highlightthickness=0, bg=c["bg"])
        emblem.pack(side="left", padx=(0, 13))
        emblem.create_rectangle(2, 2, 42, 42, outline="", fill=c["accent"])
        emblem.create_text(22, 22, text="⇄", fill=c["button_text"], font=("Segoe UI Symbol", 21, "bold"))
        brand_text = tk.Frame(brand, bg=c["bg"])
        brand_text.pack(side="left")
        tk.Label(brand_text, text="RightConvert", bg=c["bg"], fg=c["fg"],
                 font=("Segoe UI Semibold", 20)).pack(anchor="w")
        tk.Label(brand_text, text="Convertește rapid. Fișierele rămân pe calculator.", bg=c["bg"],
                 fg=c["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
        engine_tools = tk.Frame(header, bg=c["bg"])
        engine_tools.pack(side="right")
        tk.Label(engine_tools, text="OFFLINE  ·  WINDOWS", bg=c["surface_alt"], fg=c["muted"],
                 font=("Segoe UI Semibold", 9), padx=12, pady=7).pack(anchor="e")
        engines = tk.Frame(engine_tools, bg=c["bg"])
        engines.pack(anchor="e", pady=(8, 0))
        self.ffmpeg_button = self._button(engines, "⚙  Motor FFmpeg", self._choose_ffmpeg)
        self.ffmpeg_button.pack(side="right")
        self.office_button = self._button(engines, "⚙  Motor Office", self._choose_office)
        self.office_button.pack(side="right", padx=(0, 10))

        section = tk.Frame(body, bg=c["surface"], padx=18, pady=17)
        section.grid(row=1, column=0, sticky="nsew")
        section.columnconfigure(0, weight=1)
        section.rowconfigure(1, weight=1, minsize=110)
        actions = tk.Frame(section, bg=c["surface"])
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        self._label(actions, "Fișiere selectate", size=13, bold=True).pack(side="left")
        self.add_button = self._button(actions, "+  Adaugă fișiere", self._browse, accent=True)
        self.add_button.pack(side="right", padx=(9, 0))
        self.add_folder_button = self._button(actions, "+  Adaugă folder", self._browse_folder)
        self.add_folder_button.pack(side="right", padx=(9, 0))
        self.remove_button = self._button(actions, "Elimină selectate", self._remove_selected)
        self.remove_button.pack(side="right")

        tree_holder = tk.Frame(section, bg=c["surface"], highlightbackground=c["line"], highlightthickness=1)
        tree_holder.grid(row=1, column=0, sticky="nsew")
        self.tree = ttk.Treeview(tree_holder, columns=("file", "type", "target", "status"), show="headings",
                                 selectmode="extended", style="RC.Treeview")
        self.tree.heading("file", text="FIȘIER")
        self.tree.heading("type", text="SURSA")
        self.tree.heading("target", text="FORMAT DESTINAȚIE  ✎")
        self.tree.heading("status", text="STARE")
        self.tree.column("file", width=405, minwidth=205, stretch=True)
        self.tree.column("type", width=110, minwidth=80, stretch=False, anchor="center")
        self.tree.column("target", width=170, minwidth=125, stretch=False, anchor="center")
        self.tree.column("status", width=167, minwidth=110, stretch=False)
        scroll = ttk.Scrollbar(tree_holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._edit_format)
        for key in ("<Return>", "<F2>", "<space>"):
            self.tree.bind(key, self._edit_focused_format)
        self.tree.bind("<Control-i>", self._show_focused_error)
        self.tree.bind("<Delete>", lambda _: self._remove_selected())
        self.tree.bind("<Control-a>", lambda _: self.tree.selection_set(self.tree.get_children()))
        self.tree.bind("<<TreeviewSelect>>", self._refresh_combine_button)
        self.tree.tag_configure("error", foreground=c["danger"])
        self.tree.tag_configure("success", foreground=c["accent"])
        self.tree.tag_configure("disabled", foreground=c["muted"])

        target_row = tk.Frame(section, bg=c["surface"])
        target_row.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._label(target_row, "Format comun", color="muted").pack(side="left", padx=(0, 9))
        self.bulk = ttk.Combobox(target_row, textvariable=self.bulk_var, state="readonly", width=23,
                                 style="RC.TCombobox", values=["Format pentru toate…"])
        self.bulk.pack(side="left")
        self.bulk.bind("<<ComboboxSelected>>", self._apply_bulk)
        self._label(target_row, "Preset", color="muted").pack(side="left", padx=(18, 9))
        self.preset = ttk.Combobox(target_row, textvariable=self.preset_var, state="readonly", width=20,
                                   style="RC.TCombobox", values=list(PRESET_TARGETS))
        self.preset.pack(side="left")
        self.preset.bind("<<ComboboxSelected>>", self._apply_preset)
        self.combine_button = self._button(target_row, "Combină în PDF", self._combine_selected)
        self.combine_button.pack(side="right")
        self._refresh_combine_button()
        self._label(section, "Format: dublu click/F2 · Eroare: Ctrl+I · Presetul se aplică doar formatelor compatibile",
                    color="muted").grid(row=3, column=0, sticky="w", pady=(7, 0))

        output = tk.Frame(body, bg=c["surface"], padx=20, pady=18)
        output.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        self._label(output, "Destinația conversiilor", bold=True, size=11).pack(anchor="w", pady=(0, 12))
        control = tk.Frame(output, bg=c["surface"])
        control.pack(fill="x")
        self.same_folder = tk.Checkbutton(control, text="În folderul fișierului original", variable=self.same_folder_var,
                                          command=self._folder_mode_changed, bg=c["surface"], fg=c["fg"],
                                          activebackground=c["surface"], activeforeground=c["fg"],
                                          selectcolor=c["surface_alt"], font=("Segoe UI", 10), borderwidth=0)
        self.same_folder.pack(side="left")
        self.choose_folder_btn = self._button(control, "Alege folder…", self._choose_folder)
        self.choose_folder_btn.pack(side="right")
        self.folder_entry = tk.Entry(output, textvariable=self.folder_var, bg=c["surface_alt"], fg=c["fg"],
                                     insertbackground=c["fg"], disabledbackground=c["surface_alt"],
                                     disabledforeground=c["muted"], relief="flat", font=("Segoe UI", 10), bd=0)
        self.folder_entry.pack(fill="x", ipady=9, pady=(12, 0))
        self._folder_mode_changed()

        footer = tk.Frame(body, bg=c["bg"])
        footer.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self._label(footer, "Pregătit", color="muted", background="bg").pack_forget()
        self.status_label = tk.Label(footer, textvariable=self.summary_var, bg=c["bg"], fg=c["muted"],
                                     font=("Segoe UI", 10), anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.cancel_button = self._button(footer, "Anulează", self._cancel, danger=True)
        self.cancel_button.pack(side="right", padx=(10, 0))
        self.start_button = self._button(footer, "Convertește  →", self._start, accent=True)
        self.start_button.pack(side="right")
        self.cancel_button.configure(state="disabled")
        self.progress_bar = ttk.Progressbar(body, style="RC.Horizontal.TProgressbar", variable=self.progress_var,
                                            mode="determinate", maximum=100)
        self.progress_bar.grid(row=4, column=0, sticky="ew", pady=(11, 0))

    def _folder_mode_changed(self):
        self.folder_entry.configure(textvariable=self.folder_hint_var if self.same_folder_var.get() else self.folder_var,
                                    state="disabled" if self.same_folder_var.get() or self._busy() else "normal")
        self.choose_folder_btn.configure(state="disabled" if self.same_folder_var.get() or self._busy() else "normal")
        self._save_preferences()

    def _busy(self) -> bool:
        return self.running or self.importing

    def _set_busy_controls(self) -> None:
        """Keep all mutable queue, format and destination controls frozen."""
        busy = self._busy()
        for control in (self.add_button, self.add_folder_button, self.remove_button,
                        self.ffmpeg_button, getattr(self, "office_button", None)):
            if control is None:
                continue
            control.configure(state="disabled" if busy else "normal")
        self.start_button.configure(state="disabled" if busy else "normal")
        # Tk classic buttons retain their original background when disabled.
        # On a blue accent that would leave muted text below AA contrast.
        for control in (self.add_button, self.start_button):
            control.configure(bg=self.colors["surface_alt"] if busy else self.colors["accent"])
        self.bulk.configure(state="disabled" if busy else "readonly")
        self.preset.configure(state="disabled" if busy else "readonly")
        self.same_folder.configure(state="disabled" if busy else "normal")
        self.folder_entry.configure(state="disabled" if busy or self.same_folder_var.get() else "normal")
        self.choose_folder_btn.configure(state="disabled" if busy or self.same_folder_var.get() else "normal")
        self._refresh_combine_button()

    def _save_preferences(self):
        self.settings["output_dir"] = self.folder_var.get()
        self.settings["same_folder"] = bool(self.same_folder_var.get())
        try:
            save_settings(self.settings)
        except OSError:
            self.log.warning("Could not save settings")

    def _choose_folder(self):
        if self._busy():
            return
        destination = filedialog.askdirectory(title="Selectează folderul rezultat")
        if destination:
            self.folder_var.set(destination)
            self.same_folder_var.set(False)
            self._folder_mode_changed()

    def _choose_ffmpeg(self):
        if self._busy():
            return
        executable = filedialog.askopenfilename(title="Alege ffmpeg.exe", filetypes=[("FFmpeg", "ffmpeg.exe"), ("Executabile", "*.exe")])
        if not executable:
            return
        if Path(executable).name.lower() != "ffmpeg.exe":
            messagebox.showerror("Motor invalid", "Alege executabilul ffmpeg.exe.")
            return
        # A basename-only check previously accepted an inert ffmpeg.exe, then
        # silently discarded all media during the next import. Both actual
        # executables must work; never treat filename alone as installed codec.
        tools = media_tools(executable)
        if tools is None:
            messagebox.showerror("Motor FFmpeg indisponibil",
                                 "Ai nevoie de ffmpeg.exe ȘI ffprobe.exe în același folder.")
            return
        try:
            result = subprocess.run(
                [tools[1], "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0 or not available_encoders(tools[0]):
                raise ValueError("No available FFmpeg encoders or invalid FFprobe")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            messagebox.showerror("Motor FFmpeg indisponibil",
                                 "FFmpeg/FFprobe nu pornesc corect sau nu oferă encodere compatibile.")
            return
        self.settings["ffmpeg_path"] = tools[0]
        self._save_preferences()
        if self.files:
            self._begin_import([(iid, row.file) for iid, row in self.files.items()])
        else:
            self.summary_var.set("Motor FFmpeg configurat.")

    def _choose_office(self):
        if self._busy():
            return
        executable = filedialog.askopenfilename(
            title="Alege soffice.exe sau soffice.com (LibreOffice instalat)",
            filetypes=[("LibreOffice", "soffice.exe soffice.com"), ("Executabile", "*.exe *.com")],
        )
        if not executable:
            return
        found = find_libreoffice(executable)
        if not found:
            messagebox.showerror("Motor Office invalid",
                                 "Alege un executabil existent soffice.exe sau soffice.com.")
            return
        self.settings["office_path"] = found
        self._save_preferences()
        if self.files:
            # Recompute the offered routes on the importer thread.
            self._begin_import([(iid, row.file) for iid, row in self.files.items()])
        else:
            self.summary_var.set("Motor Office configurat. Adaugă documente pentru conversie.")

    def _browse(self):
        if self._busy():
            return
        selected = filedialog.askopenfilenames(title="Adaugă fișiere pentru conversie")
        if selected:
            self.queue_files([Path(path) for path in selected])

    def _browse_folder(self):
        if self._busy():
            return
        selected = filedialog.askdirectory(title="Alege folderul de scanat recursiv")
        if selected:
            self.queue_folder(Path(selected))

    @staticmethod
    def _initial_target(path: Path, available: list[str], source_format: str = "") -> str:
        preference = {
            "png": "webp", "jpg": "webp", "jpeg": "webp", "webp": "png",
            "mp3": "flac", "wav": "mp3", "flac": "mp3",
            "mp4": "webm", "webm": "mp4",
            "pdf": "pngzip", "docx": "pdf", "xlsx": "pdf", "pptx": "pdf",
            "odt": "pdf", "ods": "pdf", "odp": "pdf",
        }.get(source_format or path.suffix.lower().lstrip("."), "")
        return preference if preference in available else available[0]

    def _target_for_source(self, path: Path, options: list[str], source_format: str) -> str:
        """CLI target wins; explicit GUI preset only affects compatible sources."""
        if self.preferred_target in options:
            return self.preferred_target
        family, chosen = PRESET_TARGETS.get(self.preset_var.get(), ("", ""))
        if family and family == _source_family(source_format) and chosen in options:
            return chosen
        return self._initial_target(path, options, source_format)

    def _apply_preset(self, _event=None):
        if self._busy():
            return
        family, chosen = PRESET_TARGETS.get(self.preset_var.get(), ("", ""))
        for iid, row in self.files.items():
            if family and (family != _source_family(row.source_format) or chosen not in row.options):
                continue
            target = (self._initial_target(row.file, row.options, row.source_format)
                      if not family else chosen)
            if target in row.options:
                self._set_row_target(iid, target)
        self.summary_var.set("Preset aplicat formatelor compatibile; fișierele originale rămân intacte.")

    def add_files(self, paths: list[Path]) -> None:
        """Synchronous explicit API helper; CLI startup uses queue_files()."""
        if self._busy():
            return
        existing = {_identity(row.file) for row in self.files.values()}
        rejected = 0
        for path in paths:
            try:
                identity = _identity(path)
                if identity in existing:
                    continue
                ffmpeg_path = self.settings.get("ffmpeg_path") or None
                office_path = self.settings.get("office_path") or None
                options, source_format = (
                    _inspect_candidate(path, ffmpeg_path, office_path)
                    if office_path else _inspect_candidate(path, ffmpeg_path)
                )
            except (OSError, ValueError, TypeError):
                rejected += 1
                continue
            if not options:
                rejected += 1
                continue
            self._add_ready_row(path, options, source_format)
            existing.add(identity)
        self._refresh_formats()
        if rejected:
            self.summary_var.set(f"{len(self.files)} fișiere pregătite  ·  {rejected} fără conversii compatibile")

    def _add_ready_row(self, path: Path, options: list[str], source_format: str,
                       display_name: str = "") -> None:
        iid = str(self.next_id)
        self.next_id += 1
        self.files[iid] = SelectedFile(
            path, self._target_for_source(path, options, source_format), options,
            source_format=source_format, display_name=display_name,
        )
        self._update_row(iid)

    def queue_files(self, paths: list[Path]) -> None:
        """Queue selections on a background worker; no Tk calls on that thread."""
        if self._busy() or not paths:
            return
        self._begin_import([(None, Path(path)) for path in paths])

    def queue_folder(self, folder: Path) -> None:
        """Import a folder incrementally without scanning on the Tk thread."""
        if self._busy():
            return
        stats = FolderScanStats(root=Path(folder))
        paths = ((None, path) for path in _walk_folder_files(
            Path(folder), self.import_cancel, stats,
        ))
        self._begin_import(paths, folder_stats=stats)

    def _begin_import(self, items, folder_stats: FolderScanStats | None = None) -> None:
        if self._busy() or (isinstance(items, list) and not items):
            return
        self.importing = True
        self.import_cancel.clear()
        self._set_busy_controls()
        self.cancel_button.configure(state="normal")
        total = str(len(items)) if isinstance(items, list) else "?"
        self.summary_var.set(f"Se verifică fișierele… 0/{total}")
        thread = threading.Thread(
            target=self._import_worker,
            args=(items, {_identity(row.file) for row in self.files.values()},
                  self.settings.get("ffmpeg_path") or None, folder_stats,
                  self.settings.get("office_path") or None),
            daemon=True,
        )
        thread.start()

    def _import_worker(
        self, items, existing: set[str], ffmpeg_path: str | None,
        folder_stats: FolderScanStats | None = None,
        office_path: str | None = None,
    ) -> None:
        """Bounded producer: slow probes do not freeze the Tk event loop."""
        rejected = 0
        inspected = 0
        office_without_backend = 0

        def send(message: tuple, *, terminal=False) -> bool:
            attempts = 0
            while terminal or not self.import_cancel.is_set():
                try:
                    self.import_events.put(message, timeout=0.1)
                    return True
                except queue.Full:
                    attempts += 1
                    if terminal and attempts >= 30:
                        return False
                    continue
            return False

        try:
            for iid, path in items:
                if self.import_cancel.is_set():
                    break
                inspected += 1
                try:
                    key = _identity(path)
                    if iid is None and key in existing:
                        continue
                    options, source_format = (
                        _inspect_candidate(path, ffmpeg_path, office_path)
                        if office_path else _inspect_candidate(path, ffmpeg_path)
                    )
                except (OSError, ValueError, TypeError):
                    options, source_format = [], ""
                except Exception as exc:
                    self.log.warning("File inspection failed: %s", type(exc).__name__)
                    options, source_format = [], ""
                if not options:
                    rejected += 1
                    if source_format in OFFICE_SOURCES and not find_libreoffice(office_path):
                        office_without_backend += 1
                elif iid is None:
                    existing.add(key)
                if not send(("import_row", iid, path, options, source_format, inspected, rejected, folder_stats)):
                    break
        except (OSError, ValueError, RuntimeError) as exc:
            if folder_stats is not None:
                folder_stats.errors += 1
            self.log.warning("Folder scan failed: %s", type(exc).__name__)
        total = len(items) if isinstance(items, list) else inspected
        send(("import_done", inspected, rejected, total, folder_stats,
              office_without_backend), terminal=True)

    def _consume_import_events(self) -> None:
        # Only a bounded batch per GUI pulse; the producer is back-pressured.
        for _ in range(24):
            try:
                event = self.import_events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "import_row":
                _, iid, path, options, source_format, inspected, rejected, *extra = event
                folder_stats = extra[0] if extra else None
                if self.auto_start_pending and iid is None and options and self.preferred_target not in options:
                    # A targeted Explorer action must NEVER quietly fall back
                    # to a different target for files it cannot convert.
                    self.auto_excluded += 1
                    options = []
                if iid is None:
                    if options and _identity(path) not in {_identity(row.file) for row in self.files.values()}:
                        display_name = ""
                        if folder_stats is not None and folder_stats.root is not None:
                            try:
                                display_name = str(path.relative_to(folder_stats.root))
                            except ValueError:
                                pass
                        self._add_ready_row(path, options, source_format, display_name)
                elif iid in self.files:
                    if not options:
                        del self.files[iid]
                        self.tree.delete(iid)
                    else:
                        row = self.files[iid]
                        row.options = options
                        row.source_format = source_format
                        if row.target not in options:
                            self._set_row_target(iid, self._target_for_source(path, options, source_format))
                        self._update_row(iid)
                self.summary_var.set(f"Se verifică fișierele… {inspected} · respinse {rejected}")
            elif kind == "import_done":
                _, inspected, rejected, total, *extra = event
                folder_stats = extra[0] if extra else None
                office_without_backend = extra[1] if len(extra) > 1 else 0
                cancelled = self.import_cancel.is_set()
                self.importing = False
                self._refresh_formats()
                self._set_busy_controls()
                self.cancel_button.configure(state="disabled")
                summary = (
                    f"{len(self.files)} fișiere în listă · {rejected} incompatibile"
                    + (f" · {total - inspected} omise" if inspected < total else "")
                )
                if folder_stats is not None:
                    if folder_stats.truncated:
                        summary += " · limită de scanare atinsă"
                    if folder_stats.skipped_links:
                        summary += f" · {folder_stats.skipped_links} linkuri ignorate"
                    if folder_stats.errors:
                        summary += f" · {folder_stats.errors} erori de acces/scanare"
                if cancelled:
                    summary += " · import anulat"
                if self.auto_excluded:
                    summary += f" · {self.auto_excluded} excluse (țintă incompatibilă)"
                if office_without_backend:
                    summary += (f" · {office_without_backend} documente Office fără LibreOffice: "
                                "alege ⚙ Motor Office")
                self.summary_var.set(summary)
                if self.auto_start_pending:
                    self.auto_start_pending = False
                    if cancelled:
                        continue
                    if not self.preferred_target:
                        self.summary_var.set(summary + " · lipsește formatul pentru pornire automată")
                    elif self._pending_jobs():
                        self._start()
                    else:
                        self.summary_var.set(summary + " · niciun fișier compatibil pentru conversie automată")

    def _update_row(self, iid: str) -> None:
        row = self.files[iid]
        status = row.status
        values = (row.display_name or row.file.name, (row.source_format or "?").upper(),
                  _target_label(row.target), status)
        tag = "error" if status.startswith("Eroare") else "success" if status == "Finalizat" else ""
        if self.tree.exists(iid):
            self.tree.item(iid, values=values, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=iid, values=values, tags=(tag,))

    def _refresh_formats(self) -> None:
        common = set.intersection(*(set(row.options) for row in self.files.values())) if self.files else set()
        choices = ["Format pentru toate…"] + [_target_label(fmt) for fmt in sorted(common)]
        self.bulk.configure(values=choices)
        self.bulk_var.set(choices[0])
        self._refresh_combine_button()
        if not self._busy():
            self.summary_var.set(f"{len(self.files)} fișiere pregătite  ·  {len(common)} formate comune" if self.files
                                 else "Adaugă fișiere pentru a începe.")

    def _set_row_target(self, iid: str, target: str) -> None:
        row = self.files[iid]
        if target not in row.options or target == row.target:
            return
        row.target = target
        # An explicitly changed output format is the user's reconvert action.
        row.status = "Pregătit"
        row.error_detail = ""
        self._update_row(iid)

    def _apply_bulk(self, _event):
        if self._busy():
            return
        selection = _target_value(self.bulk_var.get())
        if selection not in ("", "format pentru toate…"):
            for iid, row in self.files.items():
                if selection in row.options:
                    self._set_row_target(iid, selection)

    def _edit_format(self, event):
        if self._busy():
            return
        column = self.tree.identify_column(event.x)
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.files:
            return
        if column == "#4":
            self._show_row_error(iid)
            return
        if column != "#3":
            return
        self._open_format_editor(iid)

    def _edit_focused_format(self, _event=None):
        if self._busy():
            return "break"
        iid = self.tree.focus()
        if iid in self.files:
            self._open_format_editor(iid)
        return "break"

    def _show_focused_error(self, _event=None):
        iid = self.tree.focus()
        if iid in self.files:
            self._show_row_error(iid)
        return "break"

    def _show_row_error(self, iid: str) -> None:
        row = self.files.get(iid)
        if row is not None and row.error_detail:
            # Full local exception details are deliberately not written to logs
            # or telemetry. Show them only at the user's explicit request.
            messagebox.showerror("Detalii conversie", f"{row.file.name}\n\n{row.error_detail}", parent=self.root)

    def _open_format_editor(self, iid: str) -> None:
        rect = self.tree.bbox(iid, "target")
        if not rect:
            return
        x, y, width, height = rect
        editor = ttk.Combobox(self.tree, state="readonly",
                              values=[_target_label(v) for v in self.files[iid].options])
        editor.set(_target_label(self.files[iid].target))
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        def done(_=None):
            value = _target_value(editor.get())
            if value in self.files[iid].options:
                self._set_row_target(iid, value)
            editor.destroy()

        editor.bind("<<ComboboxSelected>>", done)
        editor.bind("<Return>", done)
        editor.bind("<Escape>", lambda _: editor.destroy())
        editor.bind("<FocusOut>", lambda _: editor.destroy() if editor.winfo_exists() else None)

    def _remove_selected(self):
        if self._busy():
            return
        for iid in self.tree.selection():
            self.files.pop(iid, None)
            self.tree.delete(iid)
        self._refresh_formats()

    def _selected_pdf_images(self) -> list[tuple[str, Path]]:
        """Preserve the visible Treeview order, including nonadjacent rows."""
        if not hasattr(self.tree, "get_children"):
            return []
        selected = set(self.tree.selection())
        result: list[tuple[str, Path]] = []
        for iid in self.tree.get_children():
            if iid not in selected:
                continue
            row = self.files.get(iid)
            if row is None or _source_family(row.source_format) != "image" or "pdf" not in row.options:
                return []
            result.append((iid, row.file))
        return result if 2 <= len(result) <= MAX_COMBINED_PDF_IMAGES and len(result) == len(selected) else []

    def _refresh_combine_button(self, _event=None):
        button = getattr(self, "combine_button", None)
        if button is not None:
            button.configure(state="disabled" if self._busy() or not self._selected_pdf_images() else "normal")

    def _combine_selected(self):
        if self._busy():
            return
        chosen = self._selected_pdf_images()
        if not chosen:
            messagebox.showinfo(
                "Combină în PDF",
                f"Selectează între 2 și {MAX_COMBINED_PDF_IMAGES} imagini compatibile (fără transparență) în listă.",
            )
            return
        if not self.same_folder_var.get():
            folder = Path(self.folder_var.get())
            if not self.folder_var.get() or not folder.is_dir():
                messagebox.showerror("Folder invalid", "Selectează un director de ieșire existent.")
                return
        destination = chosen[0][1].parent if self.same_folder_var.get() else Path(self.folder_var.get())
        self._save_preferences()
        self.cancel_signal.clear()
        self.progress_var.set(0)
        # The engine reports start/finish for PDF merges, not per-page
        # percentages. Show activity without inventing numeric progress.
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(15)
        self.running = True
        self._set_busy_controls()
        self.cancel_button.configure(state="normal")
        self.summary_var.set(f"Se combină {len(chosen)} imagini în PDF…")
        for iid, _ in chosen:
            self.files[iid].status = "Se combină…"
            self.files[iid].error_detail = ""
            self._update_row(iid)
        thread = threading.Thread(target=self._combine_worker,
                                  args=(chosen, destination), daemon=True)
        thread.start()

    def _combine_worker(self, chosen: list[tuple[str, Path]], destination: Path):
        def on_progress(value):
            self.events.put(("combine_progress", max(0.0, min(100.0, float(value) * 100))))

        try:
            output = combine_images_to_pdf(
                [path for _, path in chosen], destination,
                cancel=self.cancel_signal, progress=on_progress,
            )
        except Exception as exc:
            cancelled = self.cancel_signal.is_set()
            if not cancelled:
                self.log.warning("PDF combination failed: %s", type(exc).__name__)
            self.events.put(("combine_finished", [iid for iid, _ in chosen],
                             None, f"{type(exc).__name__}: {exc}", cancelled))
        else:
            self.events.put(("combine_finished", [iid for iid, _ in chosen],
                             output.name, "", False))

    def _start(self):
        if self._busy():
            return
        if not self.files:
            messagebox.showinfo("RightConvert", "Adaugă cel puțin un fișier.")
            return
        plan = self._pending_jobs()
        if not plan:
            messagebox.showinfo(
                "RightConvert",
                "Toate fișierele sunt finalizate. Pentru a reconverti, schimbă formatul destinație.",
            )
            return
        if not self.same_folder_var.get():
            folder = Path(self.folder_var.get())
            if not self.folder_var.get() or not folder.is_dir():
                messagebox.showerror("Folder invalid", "Selectează un director de ieșire existent.")
                return
        self._save_preferences()
        self.cancel_signal.clear()
        self.progress_var.set(0)
        self.running = True
        self._set_busy_controls()
        self.cancel_button.configure(state="normal")
        for iid, _, _ in plan:
            self.files[iid].status = "În așteptare"
            self.files[iid].error_detail = ""
            self._update_row(iid)
        thread = threading.Thread(target=self._worker, args=(plan, self.same_folder_var.get(), self.folder_var.get()), daemon=True)
        thread.start()

    def _pending_jobs(self) -> list[tuple[str, Path, str]]:
        """Previously completed rows are never silently converted again."""
        return [
            (iid, row.file, row.target)
            for iid, row in self.files.items()
            if row.status != "Finalizat"
        ]

    def _worker(self, jobs, same_folder: bool, folder_name: str):
        successes = 0
        failures = 0
        cancelled = False
        for index, (iid, path, fmt) in enumerate(jobs):
            if self.cancel_signal.is_set():
                cancelled = True
                break
            self.events.put(("status", iid, "Se convertește…"))

            def on_progress(value, pos=index, row=iid):
                self.events.put(("progress", row, (pos + max(0, min(1, float(value)))) / len(jobs) * 100))

            try:
                destination = path.parent if same_folder else Path(folder_name)
                kwargs = {
                    "cancel": self.cancel_signal,
                    "progress": on_progress,
                    "ffmpeg_path": self.settings.get("ffmpeg_path") or None,
                }
                if self.settings.get("office_path"):
                    kwargs["office_path"] = self.settings["office_path"]
                convert_file(path, fmt, destination, **kwargs)
            except Exception as exc:
                if self.cancel_signal.is_set():
                    cancelled = True
                    self.events.put(("status", iid, "Anulat"))
                    break
                failures += 1
                self.log.warning("Conversion failed: %s", type(exc).__name__)
                self.events.put(("error", iid, f"{type(exc).__name__}: {exc}"))
            else:
                successes += 1
                self.events.put(("status", iid, "Finalizat"))
            self.events.put(("progress", iid, (index + 1) / len(jobs) * 100))
        self.events.put(("finished", successes, failures, cancelled))

    def _consume_events(self):
        self._consume_import_events()
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    iid, status = event[1:]
                    if iid in self.files:
                        self.files[iid].status = status
                        self._update_row(iid)
                elif kind == "error":
                    iid, detail = event[1:]
                    if iid in self.files:
                        row = self.files[iid]
                        row.error_detail = detail
                        row.status = "Eroare · detalii…"
                        self._update_row(iid)
                elif kind == "progress":
                    self.progress_var.set(event[2])
                    self.summary_var.set(f"Convertește… {event[2]:.0f}%")
                elif kind == "combine_progress":
                    if event[1] >= 100:
                        self.summary_var.set("Se finalizează documentul PDF…")
                elif kind == "combine_finished":
                    _, iids, filename, detail, cancelled = event
                    self.running = False
                    self.progress_bar.stop()
                    self.progress_bar.configure(mode="determinate")
                    if self.close_requested:
                        self.root.destroy()
                        return
                    for iid in iids:
                        if iid in self.files:
                            row = self.files[iid]
                            row.status = ("Anulat" if cancelled else "Eroare · detalii…"
                                          if detail else "Combinat în PDF")
                            row.error_detail = "" if cancelled else detail
                            self._update_row(iid)
                    if cancelled:
                        self.progress_var.set(0)
                        self.summary_var.set("Combinare PDF anulată.")
                    elif detail:
                        self.progress_var.set(0)
                        self.summary_var.set("Combinare PDF eșuată · selectează o imagine și Ctrl+I pentru detalii")
                    else:
                        self.progress_var.set(100)
                        self.summary_var.set(f"PDF creat: {filename} · {len(iids)} imagini")
                    self.cancel_button.configure(state="disabled")
                    self._set_busy_controls()
                elif kind == "finished":
                    self.running = False
                    success, failed, cancelled = event[1:]
                    if self.close_requested:
                        self.root.destroy()
                        return
                    if cancelled:
                        # Terminal cancellation must not leave the earlier
                        # partial progress looking like an active conversion.
                        self.progress_var.set(0)
                    self.summary_var.set(f"{success} reușite · {failed} erori" + (" · operație anulată" if cancelled else "")
                                         + (f" · {self.auto_excluded} incompatibile excluse" if self.auto_excluded else ""))
                    for iid, row in self.files.items():
                        if row.status == "În așteptare":
                            row.status = "Anulat" if cancelled else "Neprocesat"
                            self._update_row(iid)
                    self.cancel_button.configure(state="disabled")
                    self._set_busy_controls()
        except queue.Empty:
            pass
        self.root.after(100, self._consume_events)

    def _cancel(self):
        if self.running:
            self.cancel_signal.set()
            self.summary_var.set("Se anulează conversia…")
            self.cancel_button.configure(state="disabled")
        elif self.importing:
            self.import_cancel.set()
            self.summary_var.set("Se oprește scanarea folderului…")
            self.cancel_button.configure(state="disabled")

    def _on_close(self):
        if self.importing:
            self.import_cancel.set()
            self.root.destroy()
            return
        if self.running:
            if not messagebox.askyesno("RightConvert", "Conversia este în curs. Anulezi operația și închizi?"):
                return
            self.close_requested = True
            self.cancel_signal.set()
            self.root.title("RightConvert  ·  Închidere după anulare…")
            self.summary_var.set("Se anulează conversia înainte de închidere…")
            return
        self._save_preferences()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def run(files: list[Path] | None = None, preferred_target: str | None = None,
        auto_start: bool = False) -> None:
    RightConvertApp(files, preferred_target=preferred_target, auto_start=auto_start).run()
