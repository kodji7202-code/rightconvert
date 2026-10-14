from pathlib import Path

from rightconvert import config
from rightconvert.cli import parser


def test_settings_roundtrip(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = {"same_folder": False, "output_dir": str(tmp_path / "Test Folder"), "ffmpeg_path": "C:/a/ffmpeg.exe"}
    config.save_settings(settings)
    assert config.read_settings() == settings
    assert list((tmp_path / "RightConvert").glob("settings-*.tmp")) == []


def test_corrupt_settings_are_ignored(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = config.data_dir() / "settings.json"
    path.parent.mkdir()
    path.write_text("not json", encoding="utf-8")
    assert config.read_settings()["same_folder"] is True


def test_cli_accepts_unicode_and_spaces():
    args = parser().parse_args(["--files", r"C:\Fotografii\poza nouă.png", r"D:\音乐\video 1.mp4"])
    assert len(args.files) == 2
    assert "nouă" in args.files[0]
