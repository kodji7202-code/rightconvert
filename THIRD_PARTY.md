# Third-party software and licenses

The top-level MIT LICENSE grants rights only in **original RightConvert work
owned or licensable by its author**. It does not replace the licenses of
dependencies, system components or separately installed applications.

- **Python / Tcl/Tk**: PSF and corresponding Tcl/Tk notices. https://docs.python.org/3/license.html
- **Pillow**: HPND/MIT-CMU style Pillow license, with additional wheel-specific
  bundled libraries and notices. https://github.com/python-pillow/Pillow/blob/main/LICENSE
- **pypdfium2 / PDFium**: Python bindings under Apache-2.0 OR BSD-3-Clause;
  PDFium core and bundled binary components have distinct licenses. If
  distributing a frozen build, collect complete notices from the **exact**
  installed wheel version and the matching PDFium binary provider.
  https://github.com/pypdfium2-team/pypdfium2
- **cryptography / OpenSSL**: cryptography Apache-2.0 OR BSD-3-Clause plus
  applicable third-party OpenSSL and native runtime notices.
  https://github.com/pyca/cryptography/blob/main/LICENSE
- **PyInstaller**: GPL-2.0-or-later with bootloader exception; check complete
  notices for a distributed binary. https://pyinstaller.org/en/stable/license.html
- **FFmpeg / FFprobe**: external programs supplied separately by the user.
  FFmpeg builds can have LGPL or GPL obligations depending on configure flags.
  This repository does NOT bundle these executables. https://ffmpeg.org/legal.html
- **LibreOffice**: external local program, not bundled, generally MPL-2.0
  alongside its dependency obligations. https://www.libreoffice.org/licenses/
- **Windows / Microsoft Visual C++ Runtime**: Microsoft licensed components
  (if installed or redistributed); Windows itself is not MIT-licensed.

Windows binary distributors must run a build-specific dependency inventory,
ship applicable full upstream notices in `ThirdPartyNotices/`, and independently
check any requirements for Microsoft redistributables, fonts and codecs.
There is no claim that a random external FFmpeg executable is safe to repackage.
