"""End-to-end batch processing through the actual Tk UI event loop."""

from pathlib import Path
import time

from PIL import Image

from rightconvert.app import RightConvertApp


def test_desktop_batch_converts_unicode_filename_and_preserves_original(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "settings"))
    source = tmp_path / "călătorie 2026.png"
    Image.new("RGB", (40, 40), (60, 110, 200)).save(source)
    original = source.read_bytes()
    app = RightConvertApp([source])
    try:
        # CLI startup now imports every selection asynchronously, including
        # a single file. Pump the real event loop until inspection finishes.
        import_deadline = time.monotonic() + 10
        while app.importing and time.monotonic() < import_deadline:
            app.root.update()
            time.sleep(0.02)
        app.root.update()
        assert app.importing is False
        assert len(app.files) == 1
        assert next(iter(app.files.values())).target == "webp"
        assert app.start_button.cget("state") == "normal"
        app._start()
        deadline = time.monotonic() + 10
        while app.running and time.monotonic() < deadline:
            app.root.update()
            time.sleep(0.02)
        app.root.update()
        assert app.running is False
        assert (tmp_path / "călătorie 2026.webp").is_file()
        assert source.read_bytes() == original
        assert next(iter(app.files.values())).status == "Finalizat"
        assert app.progress_var.get() == 100
    finally:
        app.root.destroy()
