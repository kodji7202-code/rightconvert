# RightConvert 0.1.0 — exact supported format matrix

**Last verified:** 2026-10-13 on Windows 11 Pro x64, build 26200, Python 3.12.10, Pillow 11.2.1 and separately installed FFmpeg/FFprobe 8.0.1. The application's actual source of truth is `rightconvert/formats.py`, not the larger ConvT format catalogue.

The following **14 directed conversions** passed a separate release acceptance run (`python scripts/validate_stage4.py`) using real media fixtures, preserved-original SHA-256 hashes, collision testing, Pillow decoder and FFprobe/FFmpeg decoding. Machine-readable proof: `build/stage4/format-matrix.json`.

| Source detected from file contents | Available output formats | Proven directed routes | Engine |
| --- | --- | ---: | --- |
| JPEG/JPG | PNG, WebP | 2 | Pillow, packaged |
| PNG | JPG, WebP | 2 | Pillow, packaged |
| WebP (single frame) | JPG, PNG | 2 | Pillow, packaged |
| MP3 | WAV, FLAC | 2 | FFmpeg + FFprobe, **external/local** |
| WAV | MP3, FLAC | 2 | FFmpeg + FFprobe, **external/local** |
| FLAC | MP3, WAV | 2 | FFmpeg + FFprobe, **external/local** |
| MP4 | WebM | 1 | FFmpeg + FFprobe, **external/local** |
| WebM | MP4 | 1 | FFmpeg + FFprobe, **external/local** |
| **Total** | 8 input/output formats | **14/14 passed** | Codecs depend on local encoder inventory |

The menu lists a target only when the source's content is recognized, its stream structure is supported, and a suitable local decoder/encoder is available. Unsupported or corrupt inputs receive no fabricated conversion option. With no local FFmpeg and FFprobe installation, audio/video inputs are unavailable; image conversions still work without Internet access.

**Conversion fidelity:** JPG and lossy WebP may change image quality; PNG alpha is composited on white for JPEG; EXIF orientation is applied and some metadata is not carried over. Animated WebP and other multi-frame images are intentionally rejected. MP3 transcodes are lossy and converting MP3 to FLAC does not restore lost quality. Video streams are transcoded; subtitle/data/attachment streams are refused rather than silently lost; attached cover art, unsafe stream shapes and some high-bit-depth/multiple-audio conditions are unsupported. FFmpeg's chosen encoder depends on local availability. A fragmented MP4 output is used to support safe pipe-based encoding.

**Not implemented and therefore not advertised:** GIF/BMP/TIFF/AVIF/HEIC/SVG input, MOV/MKV/AVI, AAC/M4A/OGG/Opus, PDF-to-image, DOCX/PPTX/XLSX conversion, video-to-audio extraction, folders/recursive conversion, direct Explorer target-format submenus, presets, cloud conversion, and multi-step routes. Directories can be chosen for **output**, but input queues consist of files.

## Filename and safety guarantees tested

- Source files remain in place. A separate local destination folder is selectable.
- If `file.webp` exists, the application uses `file (1).webp`, `file (2).webp`, etc., selecting a free name without replacing any existing file.
- Windows temporary output is written to an exclusively held file handle in a private directory and published atomically without replacement. Cancellation deletes only the owned temporary handle. Collisions and hostile hardlink replacement were regression-tested.
- Unicode, whitespace and special characters are handled as paths, not shell commands.
- This is an engineering guarantee for tested threat cases, not a claim of immunity against a malicious administrator or third-party process with arbitrary access to process memory and handles.

For fresh verification:

```powershell
python -m pytest -q
python scripts/validate_stage4.py
Get-Content -LiteralPath '.\build\stage4\format-matrix.json' -Raw -Encoding UTF8
```
