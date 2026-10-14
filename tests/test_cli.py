"""CLI and context-menu argument contracts without launching Tk."""

from pathlib import Path
import sys
from types import ModuleType

import pytest

from rightconvert.cli import main, parser


def test_classic_explorer_entry_preserves_default_gui_contract(monkeypatch):
    calls = []
    fake = ModuleType("rightconvert.app")
    fake.run = lambda paths, **kwargs: calls.append((paths, kwargs))
    monkeypatch.setitem(sys.modules, "rightconvert.app", fake)
    assert main(["--files", "C:/original/poza Șă.png", "C:/original/hello.jpg"]) == 0
    assert calls == [([Path("C:/original/poza Șă.png"), Path("C:/original/hello.jpg")], {})]


def test_quick_format_invocation_has_explicit_autostart(monkeypatch):
    calls = []
    fake = ModuleType("rightconvert.app")
    fake.run = lambda paths, **kwargs: calls.append((paths, kwargs))
    monkeypatch.setitem(sys.modules, "rightconvert.app", fake)
    assert main(["--files", "C:/in/東京.png", "--target", "WEBP", "--auto-start"]) == 0
    assert calls == [([Path("C:/in/東京.png")],
                      {"preferred_target": "webp", "auto_start": True})]


@pytest.mark.parametrize("args", [
    ["--files", "x.png", "--auto-start"],
    ["--target", "webp", "--auto-start"],
    ["--files", "x.png", "--target", "unrecognized"],
    ["--target", "webp"],
])
def test_quick_format_rejects_invalid_or_incomplete_invocations(args):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


def test_parser_accepts_multiple_paths_with_trailing_options():
    parsed = parser().parse_args(["--files", "one file.png", "second.webp", "--target", "png"])
    assert parsed.files == ["one file.png", "second.webp"]
    assert parsed.target == "png"
    assert parsed.auto_start is False


def test_direct_conversion_never_imports_tk_and_keeps_original(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "poza-東京.png"
    Image.new("RGB", (45, 31), (22, 72, 100)).save(source)
    original = source.read_bytes()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "private-appdata"))
    fake = ModuleType("rightconvert.app")
    fake.run = lambda *a, **kw: pytest.fail("Direct conversions must not open Tk")
    monkeypatch.setitem(sys.modules, "rightconvert.app", fake)
    assert main(["--files", str(source), "--target", "webp", "--direct"]) == 0
    assert source.read_bytes() == original
    output = tmp_path / "poza-東京.webp"
    assert output.is_file()
    with Image.open(output) as image:
        assert image.format == "WEBP"


def test_direct_rejects_incompatible_batch_before_writing_any_output(tmp_path, monkeypatch):
    from PIL import Image

    image = tmp_path / "image.png"
    incompatible = tmp_path / "not-a-file.bin"
    Image.new("RGB", (30, 20)).save(image)
    incompatible.write_bytes(b"not a valid format")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "private-appdata"))
    assert main(["--files", str(image), str(incompatible), "--target", "webp", "--direct"]) == 2
    assert not (tmp_path / "image.webp").exists()


def test_direct_requires_explicit_source_target_and_excludes_autostart():
    for args in (["--direct"], ["--files", "x.png", "--direct"],
                 ["--files", "x.png", "--direct", "--auto-start", "--target", "png"]):
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2
