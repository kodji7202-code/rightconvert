"""Self-contained, per-user RightConvert installer.

PyInstaller embeds the complete one-dir application and the existing PowerShell
installer scripts inside one ZIP. The GUI delegates registration to install.ps1,
without elevating privileges or changing certificate trust.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import zipfile


APP_NAME = "RightConvert"
PAYLOAD_FILENAME = "RightConvertPayload.zip"
HASH_FILENAME = "RightConvertPayload.sha256"
REQUIRED = {
    "app/RightConvert.exe",
    "installer/common.ps1",
    "installer/install.ps1",
    "installer/uninstall.ps1",
}
MAX_FILES = 20_000
MAX_UNCOMPRESSED = 5 * 1024**3
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class LocalMediaTools:
    """Presence-only offline status. No executables are ever run by Setup."""

    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    source: str = "PATH"

    @property
    def complete(self) -> bool:
        return self.ffmpeg is not None and self.ffprobe is not None


def _local_executable(candidate: Path) -> Path | None:
    """Accept absolute local files only; don't resolve network/relative PATHs."""
    if not candidate.is_absolute() or str(candidate).startswith(("\\\\", "//")):
        return None
    try:
        if not candidate.is_file():
            return None
        resolved = candidate.resolve(strict=True)
        if str(resolved).startswith(("\\\\", "//")):
            return None
        return resolved
    except (OSError, RuntimeError):
        return None


def detect_local_media_tools(path_value: str | None = None) -> LocalMediaTools:
    """Read the local PATH like RightConvert, without Windows CWD lookup.

    No subprocess, download, configuration write, or filesystem mutation.
    A present executable is *not* claimed to be working or licensed.
    """
    if path_value is None:
        path_value = os.environ.get("PATH", "")
    try:
        cwd = Path.cwd().resolve(strict=True)
    except OSError:
        cwd = None
    result: dict[str, Path | None] = {"ffmpeg": None, "ffprobe": None}
    for raw in path_value.split(os.pathsep):
        entry = raw.strip().strip('"')
        if not entry or entry.startswith(("\\\\", "//")):
            continue
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        try:
            directory = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if directory == cwd or str(directory).startswith(("\\\\", "//")):
            continue
        for name in result:
            if result[name] is None:
                result[name] = _local_executable(directory / f"{name}.exe")
        if all(result.values()):
            break
    return LocalMediaTools(result["ffmpeg"], result["ffprobe"])


def check_selected_ffmpeg(file_name: str) -> LocalMediaTools:
    """Validate a user-chosen file and matching sibling FFprobe, read-only."""
    chosen = Path(file_name)
    if chosen.name.casefold() != "ffmpeg.exe":
        raise ValueError("Alege fișierul ffmpeg.exe, nu un alt executabil.")
    ffmpeg = _local_executable(chosen)
    if ffmpeg is None:
        raise ValueError("FFmpeg trebuie să fie un fișier local existent, cu o cale absolută.")
    ffprobe = _local_executable(ffmpeg.parent / "ffprobe.exe")
    if ffprobe is None:
        raise ValueError("Nu există ffprobe.exe lângă ffmpeg.exe. Alege un pachet local complet.")
    return LocalMediaTools(ffmpeg, ffprobe, "selectat")


def media_status_text(tools: LocalMediaTools) -> str:
    if tools.source == "selectat" and tools.complete:
        return "Pereche locală găsită. Copiază calea și alege-o în aplicație: ⚙ Motor FFmpeg."
    if tools.complete:
        return "FFmpeg și FFprobe găsite în PATH (prezență detectată, funcționarea neverificată)."
    missing = [name for name in ("FFmpeg", "FFprobe")
               if getattr(tools, name.lower()) is None]
    return (f"Lipsește din PATH: {', '.join(missing)}. "
            "Poți folosi imagini fără ele; pentru audio/video alege o pereche locală.")


def resource_dir() -> Path:
    """PyInstaller onefile extracts add-data resources under sys._MEIPASS."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_archive_name(raw: str) -> PurePosixPath:
    """Reject ZIP names that could escape an extraction directory on Windows."""
    if not raw or "\\" in raw or raw.startswith("/") or "\x00" in raw:
        raise ValueError(f"Unsafe archive path: {raw!r}")
    path = PurePosixPath(raw)
    parts = path.parts
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe archive path: {raw!r}")
    if not parts or parts[0] not in {"app", "installer"}:
        raise ValueError(f"Unexpected archive entry: {raw!r}")
    # Windows treats a colon as a drive / alternate data stream separator.
    if any(":" in part or part.endswith((".", " ")) for part in parts):
        raise ValueError(f"Unsafe Windows archive filename: {raw!r}")
    return path


def verify_archive(zipped: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], int]:
    infos = zipped.infolist()
    if len(infos) > MAX_FILES:
        raise ValueError("Installer payload contains too many files.")
    seen: set[str] = set()
    found: set[str] = set()
    total = 0
    for info in infos:
        raw = info.filename.rstrip("/") if info.is_dir() else info.filename
        name = safe_archive_name(raw)
        canonical = str(name).casefold()
        if canonical in seen:
            raise ValueError(f"Duplicate archive filename: {raw!r}")
        seen.add(canonical)
        # Unix-origin symlinks and special files must not be extracted.
        if info.create_system == 3:
            kind = (info.external_attr >> 16) & 0o170000
            if kind not in (0, 0o040000, 0o100000):
                raise ValueError(f"Unsafe archive file type: {raw!r}")
        if info.is_dir():
            continue
        if info.file_size < 0:
            raise ValueError(f"Invalid archive file length: {raw!r}")
        total += info.file_size
        if total > MAX_UNCOMPRESSED:
            raise ValueError("Installer payload exceeds allowed size.")
        found.add(str(name))
    if missing := sorted(REQUIRED - found):
        raise ValueError(f"Installer payload is incomplete: {', '.join(missing)}")
    return infos, total


def unpack_payload(destination: Path, progress=None) -> None:
    """Verify payload digest, then extract while validating each destination."""
    resources = resource_dir()
    payload = resources / PAYLOAD_FILENAME
    expected = (resources / HASH_FILENAME).read_text(encoding="ascii").strip().lower()
    if len(expected) != 64 or not all(ch in "0123456789abcdef" for ch in expected):
        raise ValueError("Installer checksum metadata is invalid.")
    if progress:
        progress(2, "Verific integritatea pachetului...")
    if sha256_file(payload) != expected:
        raise ValueError("Verificarea SHA-256 a eșuat. Installerul este incomplet sau deteriorat.")

    destination.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(payload, "r") as zipped:
        infos, total = verify_archive(zipped)
        for info in infos:
            raw = info.filename.rstrip("/") if info.is_dir() else info.filename
            path = safe_archive_name(raw)
            target = destination.joinpath(*path.parts)
            # No path in the ZIP can create symlinks; ensure no parent is a
            # pre-existing reparse point either. Destination is a new temp dir.
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise ValueError(f"Installer archive contains a conflicting path: {raw!r}")
            with zipped.open(info, "r") as source, target.open("xb") as output:
                while block := source.read(CHUNK):
                    output.write(block)
                    extracted += len(block)
                    if progress:
                        percent = 8 + int(77 * extracted / max(1, total))
                        progress(percent, f"Pregătesc fișierele... {percent}%")
    if progress:
        progress(85, "Fișierele sunt pregătite.")


def powershell_install(extracted: Path, progress=None) -> str:
    """Run the existing per-user installation script with safely quoted argv."""
    source = extracted / "app"
    script = extracted / "installer" / "install.ps1"
    if not source.joinpath("RightConvert.exe").is_file() or not script.is_file():
        raise FileNotFoundError("Lipsește executabilul sau scriptul de instalare.")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise FileNotFoundError("Windows PowerShell 5.1 nu este disponibil.")
    if progress:
        progress(90, "Instalez aplicația și meniul contextual...")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-PackageDir",
            str(source),
        ],
        cwd=str(extracted),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        creationflags=flags,
    )
    # PowerShell 5.1 may use the system OEM or ANSI code page for output.
    decoder = "mbcs" if os.name == "nt" else "utf-8"
    stdout = result.stdout.decode(decoder, errors="replace").strip()
    stderr = result.stderr.decode(decoder, errors="replace").strip()
    if result.returncode:
        detail = "\n".join(text for text in (stderr, stdout) if text)
        raise RuntimeError(f"Instalarea a eșuat (cod {result.returncode}).\n{detail}")
    if progress:
        progress(100, "RightConvert este instalat.")
    return stdout


def smoke_test(report: Path) -> int:
    """CLI-only validation: no registry, no install, no GUI."""
    data: dict[str, object] = {"ok": False, "mode": "read-only-smoke"}
    try:
        with tempfile.TemporaryDirectory(prefix="RightConvertSetup-Ș-") as temporary:
            target = Path(temporary) / "bundle"
            unpack_payload(target)
            with zipfile.ZipFile(resource_dir() / PAYLOAD_FILENAME) as archive:
                infos, total = verify_archive(archive)
            data.update(
                {
                    "ok": True,
                    "files": sum(not info.is_dir() for info in infos),
                    "bytes": total,
                    "executable": (target / "app" / "RightConvert.exe").is_file(),
                    "scripts": all((target / name).is_file() for name in REQUIRED),
                }
            )
    except Exception as error:
        data["error"] = str(error)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if data["ok"] else 1


class SetupWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("RightConvert Setup")
        # Leave enough vertical space for 125%-scaled Windows fonts and
        # the fixed bottom action row on high-DPI desktops.
        self.root.geometry("680x640")
        self.root.minsize(630, 600)
        self.root.configure(background="#f6f8fb")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self.success = False

        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Installer.Horizontal.TProgressbar", thickness=14)
        style.configure("Installer.TButton", font=("Segoe UI", 10), padding=(15, 8))

        main = tk.Frame(self.root, bg="#f6f8fb", padx=30, pady=22)
        main.pack(fill="both", expand=True)
        header = tk.Frame(main, bg="#f6f8fb")
        header.pack(fill="x", pady=(0, 10))
        tk.Label(
            header, text="RIGHTCONVERT", bg="#f6f8fb", fg="#24334b",
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w")
        tk.Label(
            header, text="Convertor local de fișiere  •  Windows 10 / 11",
            bg="#f6f8fb", fg="#62738b", font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 5))

        tk.Label(
            main,
            text="Instalare pentru contul Windows curent",
            bg="#f6f8fb", fg="#24334b", font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", pady=(10, 6))
        tk.Label(
            main,
            text="Aplicația funcționează offline. Instalarea adaugă opțiunea "
                 "„Convert with RightConvert” în meniul contextual clasic.",
            bg="#f6f8fb", fg="#40516b", wraplength=530, justify="left",
            font=("Segoe UI", 10),
        ).pack(anchor="w")
        tk.Label(
            main,
            text="Pe Windows 11, meniul modern necesită separat un pachet "
                 "MSIX semnat. Niciun certificat nu este modificat.",
            bg="#f6f8fb", fg="#65758b", wraplength=530, justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(9, 7))

        tk.Label(
            main, text="Motor audio/video (opțional, exclusiv local)",
            bg="#f6f8fb", fg="#24334b", font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        self.media_status = tk.StringVar(value="Verificare locală în curs...")
        tk.Label(
            main, textvariable=self.media_status, bg="#f6f8fb", fg="#52637a",
            wraplength=540, justify="left", font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(3, 5))
        self.media_selection: Path | None = None
        media_actions = tk.Frame(main, bg="#f6f8fb")
        media_actions.pack(fill="x", pady=(0, 7))
        ttk.Button(
            media_actions, text="Verifică PATH", command=self.refresh_media_status,
        ).pack(side="left")
        ttk.Button(
            media_actions, text="Alege ffmpeg.exe…", command=self.choose_media_file,
        ).pack(side="left", padx=8)
        self.media_copy_button = ttk.Button(
            media_actions, text="Copiază calea", command=self.copy_media_path,
            state="disabled",
        )
        self.media_copy_button.pack(side="left")

        self.progress = ttk.Progressbar(
            main, style="Installer.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", maximum=100,
        )
        self.progress.pack(fill="x", pady=(4, 7))
        self.status = tk.StringVar(value="Pregătit pentru instalare.")
        tk.Label(
            main, textvariable=self.status, bg="#f6f8fb", fg="#40516b",
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        log_frame = tk.Frame(main, bg="#f6f8fb")
        log_frame.pack(fill="both", expand=True, pady=(14, 10))
        self.log = tk.Text(
            log_frame, bg="#fff", fg="#233044", wrap="word",
            height=5, relief="solid", borderwidth=1,
            font=("Consolas", 9), state="disabled",
        )
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)

        buttons = tk.Frame(main, bg="#f6f8fb")
        buttons.pack(fill="x")
        self.install_button = ttk.Button(
            buttons, text="Instalează", command=self.start_install,
            style="Installer.TButton",
        )
        self.install_button.pack(side="left")
        self.open_button = ttk.Button(
            buttons, text="Deschide RightConvert",
            command=self.open_application, style="Installer.TButton",
            state="disabled",
        )
        self.open_button.pack(side="left", padx=9)
        self.close_button = ttk.Button(
            buttons, text="Închide", command=self.close,
            style="Installer.TButton",
        )
        self.close_button.pack(side="right")
        self.root.after(100, self.poll_events)
        self.refresh_media_status()

    def refresh_media_status(self) -> None:
        """PATH discovery only; never writes RightConvert preferences."""
        self.media_selection = None
        self.media_copy_button.configure(state="disabled")
        self.media_status.set(media_status_text(detect_local_media_tools()))

    def choose_media_file(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root, title="Alege ffmpeg.exe existent pe acest PC",
            filetypes=[("FFmpeg", "ffmpeg.exe"), ("Executabile", "*.exe")],
        )
        if not chosen:
            return
        try:
            tools = check_selected_ffmpeg(chosen)
        except ValueError as error:
            messagebox.showwarning("FFmpeg local incomplet", str(error), parent=self.root)
            return
        self.media_selection = tools.ffmpeg
        self.media_copy_button.configure(state="normal")
        self.media_status.set(media_status_text(tools))

    def copy_media_path(self) -> None:
        """Explicit clipboard action, never automatic settings modification."""
        if self.media_selection is None:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(str(self.media_selection))
        except tk.TclError as error:
            messagebox.showerror("Clipboard indisponibil", str(error), parent=self.root)
            return
        self.media_status.set(
            "Calea ffmpeg.exe a fost copiată. În RightConvert, apasă "
            "«⚙ Motor FFmpeg» și alege același fișier."
        )

    def append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        """Begin each attempt with only the progress and errors from that attempt."""
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def update_progress(self, value: int, text: str) -> None:
        self.events.put(("progress", (value, text)))

    def start_install(self) -> None:
        if self.running:
            return
        self.running = True
        self.success = False
        self.progress.configure(value=0)
        self.status.set("Pregătesc instalarea locală...")
        self.clear_log()
        self.install_button.configure(state="disabled", text="Se instalează...")
        self.open_button.configure(state="disabled")
        self.append_log("Pregătesc instalarea locală...")
        threading.Thread(target=self.install_in_worker, daemon=True).start()

    def install_in_worker(self) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="RightConvertSetup-") as temp:
                extracted = Path(temp) / "payload"
                unpack_payload(extracted, self.update_progress)
                output = powershell_install(extracted, self.update_progress)
            self.events.put(("finished", output))
        except Exception as error:
            self.events.put(("error", str(error)))

    def poll_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "progress":
                    progress, status = value
                    self.progress.configure(value=progress)
                    self.status.set(status)
                elif event == "finished":
                    self.running = False
                    self.success = True
                    self.progress.configure(value=100)
                    self.status.set("RightConvert a fost instalat cu succes.")
                    self.append_log(str(value) or "Instalare finalizată.")
                    self.append_log("Folosește click dreapta > Convert with RightConvert.")
                    self.install_button.configure(state="normal", text="Reinstalează")
                    self.open_button.configure(state="normal")
                elif event == "error":
                    self.running = False
                    self.success = False
                    self.progress.configure(value=0)
                    self.status.set("Instalarea a eșuat. Verifică detaliile de mai jos.")
                    self.append_log(str(value))
                    self.install_button.configure(state="normal", text="Reîncearcă")
                    messagebox.showerror("RightConvert Setup", str(value), parent=self.root)
                    # Return focus to the retry action for keyboard users.
                    self.install_button.focus_set()
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def open_application(self) -> None:
        if not self.success:
            return
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            messagebox.showerror(APP_NAME, "LOCALAPPDATA nu este disponibil.", parent=self.root)
            return
        exe = Path(local) / "RightConvert" / "app" / "RightConvert.exe"
        try:
            if not exe.is_file():
                raise FileNotFoundError(str(exe))
            subprocess.Popen([str(exe)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Nu pot deschide aplicația:\n{error}", parent=self.root)

    def close(self) -> None:
        if self.running:
            messagebox.showinfo(
                "RightConvert Setup", "Instalarea este în curs. Închide după finalizare.",
                parent=self.root,
            )
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-user RightConvert graphical setup")
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="Verify and extract bundled payload without installing or opening a GUI",
    )
    parser.add_argument("--smoke-report", type=Path, help="Write read-only smoke result JSON")
    options = parser.parse_args(argv)
    if options.smoke_test:
        report = options.smoke_report or Path(tempfile.gettempdir()) / "RightConvertSetup-smoke.json"
        return smoke_test(report)
    SetupWindow().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
