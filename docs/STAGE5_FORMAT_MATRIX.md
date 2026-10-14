# RightConvert v0.2 — independent complete format-matrix acceptance

**Host:** Windows 11 Pro build 26200 (x64), Python 3.12.10, Pillow 11.2.1, locally installed FFmpeg/FFprobe 8.0.1.

**Final outcome: 57/57 real advertised directed conversions PASS across 16/16 source families.** The independent test tool, `python scripts/validate_stage5.py`, builds a disposable source file for every declared family, enumerates the current output formats from the actual source registry and confirms that `get_targets()` and `supported_targets()` agree. It converts each advertised target, independently opens images with Pillow or analyzes and strictly decodes media with FFprobe and FFmpeg `-xerror`, and verifies SHA-256 integrity of every original and cleanup of every private staging directory.

Machine-readable evidence, with **57 individual PASS records**, original file hashes, output metadata, decoder results and cleanup fields: [STAGE5_FORMAT_MATRIX_RESULTS.json](STAGE5_FORMAT_MATRIX_RESULTS.json).

| Input content | Output formats exercised | Passing directed routes |
| --- | --- | ---: |
| BMP (static) | JPG, PNG, WebP, TIFF | 4 |
| GIF (single frame) | JPG, PNG, WebP, BMP, TIFF | 5 |
| JPG | PNG, WebP, BMP, TIFF | 4 |
| PNG (RGB) | JPG, WebP, BMP, TIFF | 4 |
| TIFF (single page) | JPG, PNG, WebP, BMP | 4 |
| WebP (static) | JPG, PNG, BMP, TIFF | 4 |
| **Images** | **6 source families** | **25/25** |
| FLAC | MP3, WAV, Ogg/Vorbis, Opus | 4 |
| MP3 | WAV, FLAC, Ogg/Vorbis, Opus | 4 |
| Ogg/Vorbis | MP3, WAV, FLAC, Opus | 4 |
| Ogg/Opus | MP3, WAV, FLAC, Ogg/Vorbis | 4 |
| WAV | MP3, FLAC, Ogg/Vorbis, Opus | 4 |
| **Audio** | **5 source families** | **20/20** |
| AVI | MP4, WebM, MKV | 3 |
| MKV | MP4, WebM | 2 |
| MOV | MP4, WebM, MKV | 3 |
| MP4 | WebM, MKV | 2 |
| WebM | MP4, MKV | 2 |
| **Video** | **5 source families** | **12/12** |
| **Total** | **16 input families** | **57/57** |

## Regression fixed before acceptance: streamed WAV headers

The **first two independent runs** found 53/57 PASS: MP3→WAV, FLAC→WAV, Ogg/Vorbis→WAV and Ogg/Opus→WAV contained PCM audio but retained FFmpeg's `0xFFFFFFFF` placeholder RIFF/data lengths from writing to an unseekable stdout pipe. A strict independent decoder flagged corrupted packet lengths. This was a real defect and was **fixed in `rightconvert/engine.py`** by validating the RIFF chunk layout and filling the final RIFF/data lengths on the original held, seekable staging handle after FFmpeg exits, before the existing Windows no-replace publication. The patch refuses invalid chunks and RIFF outputs exceeding the format's 4 GiB size limit. Dedicated regression tests independently open the result with Python `wave` and decode with `ffmpeg -xerror`. The full 57-route script then passed with zero failures.

## Scope and limitations

- **Images:** supports only single-frame raster input. Animated GIF, animated WebP and multipage TIFF are rejected instead of silently losing frames. BMP output is restricted when alpha or bit depth would be lost; other high-depth and CMYK conversions depend on the declared mode matrix.
- **Audio:** Ogg/Vorbis and Ogg/Opus inputs are inspected by content and codec. Lossy MP3, Vorbis and Opus conversions can lose quality; FLAC does not recover samples lost by a lossy encoder. WAV output uses classic RIFF size fields and is therefore limited to files smaller than 4 GiB. Media encoders are conditional on locally installed FFmpeg.
- **Video:** three output families, MP4/WebM/MKV, all use local FFmpeg; MOV and AVI are supported as inputs only. Rich media stream variants (subtitles, data attachments, HDR, multi-track corner cases) require their own compatibility checks; unsupported streams are rejected rather than silently discarded.
- **No PDF/Office output:** no usable audited LibreOffice/PDFium runtime was installed on this host. See `PDF_OFFICE_PLAN.md` for a future optional, local provider architecture.
- The acceptance uses ~0.6-second synthetic media (160×90 video), 100×60 images and valid small inputs. It verifies every *advertised route on these fixtures*, not every possible codec or extreme file size. Actual Windows Explorer navigation, installer tests, disk-full fault injection and offline protocol negative tests have independent evidence in Stage3/Stage5 reports.

Run:

```powershell
python scripts/validate_stage5.py
python -m pytest -q
```

The independent validator writes its JSON report even if a route fails, exits nonzero for failures, and does not alter the user's installed application or registry.
