"""Runtime environment facts: is this process running inside an MSIX package?

The Microsoft Store build ships RightConvert as a *full* MSIX package. Inside a
package the per-user installer (HKCU registry verbs, Setup EXE, sparse MSIX)
does not apply: the package itself declares the Explorer integration, and
Windows removes it cleanly on uninstall. Code that would install, register or
self-update must check :func:`is_packaged` first and do nothing when it is true.
"""

from __future__ import annotations

import os

_ERROR_INSUFFICIENT_BUFFER = 122
# APPMODEL_ERROR_NO_PACKAGE (15700) is what an unpackaged process receives.


def _package_string(function_name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        function = getattr(ctypes.windll.kernel32, function_name)
    except (AttributeError, OSError, ImportError):
        return None  # Windows 7 / unusual hosts: never packaged.
    function.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    function.restype = wintypes.LONG
    length = wintypes.UINT(0)
    if function(ctypes.byref(length), None) != _ERROR_INSUFFICIENT_BUFFER or not length.value:
        return None
    buffer = ctypes.create_unicode_buffer(length.value)
    if function(ctypes.byref(length), buffer) != 0:
        return None
    return buffer.value or None


def package_full_name() -> str | None:
    """Return e.g. ``Kodji.RightConvert_1.0.0.0_x64__h9nm9abegh7ny`` or None."""
    return _package_string("GetCurrentPackageFullName")


def package_family_name() -> str | None:
    """Return e.g. ``Kodji.RightConvert_h9nm9abegh7ny`` or None."""
    return _package_string("GetCurrentPackageFamilyName")


def is_packaged() -> bool:
    """True when this process has MSIX package identity (Store/full package)."""
    return package_full_name() is not None
