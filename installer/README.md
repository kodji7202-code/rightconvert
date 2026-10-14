# RightConvert: Windows integration

RightConvert installs per user, works offline and does not need administrator rights for the Windows 10 classic context menu. Windows 11 compact right-click integration requires a native DLL and a signed sparse MSIX package.

## Standalone graphical installer (recommended)

After building the latest application in dist\RightConvert, create the single
downloadable Windows installer:

~~~powershell
.\installer\build-classic.ps1 -Architecture x64 -PackageDir '.\dist\RightConvert'
python scripts/build_setup.py
~~~

The result is dist\RightConvertSetup.exe. Run this executable on Windows 10/11,
click **Instalează**, and follow its progress. RightConvertSetup.exe contains the
entire PyInstaller one-dir app and the existing install.ps1, common.ps1, and
uninstall.ps1. It needs no Internet access and invokes the same per-user
PowerShell installer documented below. The executable verifies its embedded
payload with SHA-256 before extracting it. No certificate trust or execution
policy is changed.

Read-only package smoke check (does not install or edit the registry):

~~~powershell
.\dist\RightConvertSetup.exe --smoke-test --smoke-report '.\build\setup\smoke.json'
Get-Content -LiteralPath '.\build\setup\smoke.json'
~~~

Modern Windows 11 integration remains optional because it requires a separately
signed, trusted MSIX; the default graphical installation enables the classic
right-click menu, available via **Show more options** in Windows 11.

Audio/video conversions require local FFmpeg and FFprobe binaries. Install both
or select ffmpeg.exe in the application; the installer does not fetch engines
from the Internet.

### Offline FFmpeg / FFprobe setup assistant (v0.2)

The graphical Setup offers **Verifică PATH**, **Alege ffmpeg.exe…**, and
**Copiază calea**. These are optional and do not block installing RightConvert
for images. Detection searches absolute, local PATH directories for the files
ffmpeg.exe and ffprobe.exe, avoiding relative PATH entries and the current
working directory. It checks file presence only: it does **not** run unknown
executables, perform codec tests, alter environment variables, read/write
RightConvert settings, contact network services, or download anything.

If Setup reports the tools missing, use a local FFmpeg distribution that you
obtained separately, for example from your own removable storage. Ensure its
bin directory contains **both** ffmpeg.exe and ffprobe.exe. If they are not
on PATH, click **Alege ffmpeg.exe…** and choose ffmpeg.exe from that directory.
The assistant verifies that a sibling ffprobe.exe exists, then enables
**Copiază calea**. Copying requires a click and changes only your clipboard.

After installation, open RightConvert and click **⚙ Motor FFmpeg**. In the file
picker select that same ffmpeg.exe (you can paste the copied path). Only the
main application saves this setting, on your explicit selection. Setup never
edits settings.json or any other preference. If FFmpeg and FFprobe are already
on PATH and usable, manual selection may not be needed.

The file-presence check does not guarantee the selected tools will run or have
the codecs you require. Verify media conversion in RightConvert using your own
local test file. The installer does not bundle third-party FFmpeg executables,
and therefore neither redistributes GPL/LGPL FFmpeg builds nor modifies their
license terms. Anyone distributing FFmpeg separately must review the license
and third-party component obligations of the specific build.

## Prerequisites

- Windows 10/11 x64 and PowerShell 5.1.
- Python 3.12 with PyInstaller: build dist\RightConvert\RightConvert.exe and keep its one-dir dependencies together.
- Modern Windows 11 only: CMake, Visual Studio C++ Build Tools, Windows 10/11 SDK (MakeAppx.exe and SignTool.exe), and a trusted MSIX signing certificate in Cert:\CurrentUser\My with its private key.

## Classic Explorer integration

From the repository root:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\install.ps1
~~~

Pass -PackageDir to install a different PyInstaller output location, including paths with spaces and Unicode:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\install.ps1 -PackageDir 'D:\Output Școală\RightConvert'
~~~

The script copies the one-dir app to %LOCALAPPDATA%\RightConvert\app and creates per-user registry keys:

- HKCU\Software\Classes\*\shell\RightConvert: **Convert with RightConvert**.
- When RightConvertClassic.dll is packaged, the verb uses MultiSelectModel=Player, with DelegateExecute pointing to a separate COM CLSID under HKCU\Software\Classes\CLSID. Explorer passes an IShellItemArray to IExecuteCommand + IObjectWithSelection; the DLL invokes RightConvert.exe --files with all selected paths **in one process**.
- If RightConvertClassic.dll is absent, the original static Document command using a quoted single path remains the compatibility fallback.
- Stage 7 private candidate only: when `RightConvertClassic.dll` and the `rightconvert-direct-cli-v2.marker` are both present, the installer additionally registers **HKCU\Software\Classes\*\shellex\ContextMenuHandlers\RightConvertQuick** and a separate native COM class. The class adds **flat quick actions** (e.g. `Convert to webp`, `Convert to pdf`) directly to the classic Explorer menu. These use `RightConvert.exe --files ... --target ... --direct --notify` and independently preflight actual file contents and local encoders. The nested `Convert to...` cascade was abandoned after native Explorer showed an empty child menu. The main GUI verb remains independent. The optional `-DisableQuickMenu` switch suppresses these direct actions if a particular host requires the GUI fallback; older bundles without the marker do not advertise unsupported CLI behavior.
- HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\RightConvert: uninstall entry in Windows Settings and Programs and Features.

The Player/COM verb avoids the Document model's default 15-item limit. Microsoft's table gives the classic COM Player model no fixed selection-count limit. Windows process command lines still have a practical 32,767-character limit; if an unusually large selection exceeds it, the native DLL reports an error without launching a partial batch. Non-filesystem Shell objects are rejected. The standalone GUI requires a new build after packaging the classic DLL.

For developer validation:

~~~powershell
.\installer\build-classic.ps1 -Architecture x64 -PackageDir '.\dist\RightConvert'
ctest --test-dir .\shell\classic\out\x64 -C Release --output-on-failure
.\installer\test-install-root.ps1
.\installer\test-deferred-uninstall.ps1
.\installer\test-classic-registry.ps1
.\installer\test-stage7-full-install.ps1
~~~

ClassicMultiSelect verifies a **single process with 21 Unicode filenames**,
including spaces and Shell metacharacters, through real COM interfaces and
Windows argv parsing. The Windows 11 modern DLL in shell\windows11 remains
independent and unchanged. The deferred-uninstall regression opens the actual
native DLL in a private test process, verifies that uninstall schedules
RunOnce rather than deleting the loaded module, then releases the DLL and
replays cleanup, including the optional settings/logs purge.

Uninstall via Windows Settings or directly:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\uninstall.ps1
~~~

**Windows Explorer can keep RightConvertClassic.dll loaded after a context-menu
action.** The uninstaller never stops or kills Explorer. When the DLL is locked,
it unregisters the RightConvert menu and COM class, then registers a per-user
HKCU RunOnce value named !RightConvert.PendingCleanup. The app directory and
install-marker remain until the next Windows **sign-out and sign-in** (or
reboot), when the installed uninstaller retries cleanup automatically.
Until then, files on disk may remain even though the Explorer menu is gone.
If another process still holds the DLL on the next sign-in, run the installed
uninstaller again after the process has released it.

Inspect pending cleanup without changing any settings:

~~~powershell
Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name '!RightConvert.PendingCleanup' -ErrorAction SilentlyContinue
~~~

The deferred command runs PowerShell in the current user account; it does not
elevate, change certificate trust, or delete unrelated files. Running the new
uninstaller from this repository also updates a previously installed old
uninstaller, so an interrupted, partially unregistered installation can resume
safely on the next sign-in.

Settings and logs are preserved by default to support reinstalling. To
**completely remove RightConvert user settings and logs**, explicitly run:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\uninstall.ps1 -PurgeUserData
~~~

The purge deletes only RightConvert's settings.json and logs tree after
checking for nested junctions/reparse points; other files in the user data
directory remain untouched. The same -PurgeUserData flag can be used on the
installed uninstaller in %LOCALAPPDATA%\RightConvert\uninstall.ps1. If the
DLL is locked, the requested data purge happens at the deferred cleanup.

The installer stages updates and rolls back the app directory on failure. Ownership markers prevent replacing unrelated registry entries or installation folders. The uninstaller removes the tracked modern MSIX, owned registry keys, and application files. Unknown files at the installation root remain untouched. The -ExecutionPolicy Bypass option applies only to the launched PowerShell process and never changes execution policy settings.

If a portable RightConvert run has already created settings.json and logs inside %LOCALAPPDATA%\RightConvert, installation still works and preserves both. Uninstallation also preserves these personal files, so reinstall remains possible. Before recursive cleanup, the scripts scan the entire application tree and refuse junctions/reparse points, including nested ones, to avoid deleting files outside the installed app.

Interrupted installs can leave .stage-GUID directories. The installer creates
a marker at the start of staging and holds an exclusive lock while copying.
On restart it removes only marker-verified, unlocked stages whose trees have
been scanned for reparse points; unknown or active stages stop the operation.

### Reinstall when Explorer still has the previous classic DLL loaded

The installer can now finish upgrading while Windows keeps a DLL from the old
application version loaded in Explorer. The previous application is moved into
a .backup-GUID directory with an independently stored, installer-owned
.backup-GUID.owner marker. While the old DLL is loaded, the entire backup is
left untouched; the new app is registered and the installation succeeds.
Once the new app and menu are registered, any earlier
!RightConvert.PendingCleanup RunOnce entry for an unfinished **uninstall** is
removed after verifying ownership. This prevents it from deleting the newly
installed version at the next sign-in.

Any locked previous-version backup is handled by a **separate**, per-user
!RightConvert.BackupCleanup RunOnce entry invoking the installed uninstaller
with -CleanupBackupsOnly. This internal command scans only marked
.backup-GUID directories; it never deletes the current app, settings, or
registered context menu. It can complete on the next sign-in after the old DLL
is unloaded. You can safely run the backup-only retry manually:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\RightConvert\uninstall.ps1" -CleanupBackupsOnly
~~~

Backups produced by an **older installer** before the owner-marker fix are
not adopted or deleted automatically. For example, an existing
.backup-GUID without its exact corresponding .owner file is preserved and
reported as an unmarked legacy backup. A separate, conservative read-only
inspection tool is available for the specific early Stage3 case where a backup
contains **only** RightConvertClassic.dll and RightConvertExplorer.dll:

~~~powershell
# Read-only audit of every .backup-GUID candidate in your RightConvert root
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\inspect-legacy-backup.ps1

# Read-only audit of one explicitly selected candidate
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\inspect-legacy-backup.ps1 -BackupPath "$env:LOCALAPPDATA\RightConvert\.backup-0123456789abcdef0123456789abcdef"
~~~

An eligible candidate must be an immediate .backup-GUID child of the known
per-user RightConvert root. The root must either have a valid
install-marker.txt identifying this installer, **or** have the strict historical
**data-only** shape left by older uninstalls: only settings.json, logs and direct
.backup-GUID directories (no app directory or unknown root files). This lets
the auditor handle the actual Stage3 case where uninstall had already deleted
install-marker.txt, without treating unrelated data as installer-owned. In
either case, the selected backup must have **exactly** the two DLLs listed
above, with no
additional files/directories, symlinks, junctions, reparse points, or existing
.owner marker. The tool checks for x64 Windows PE DLL headers, both expected
COM export names, the distinct RightConvert COM CLSID bytes and reports their
SHA-256 hashes. These content checks are **not** equivalent to an Authenticode
signature or independent proof of provenance. For anything else, cleanup is
refused and the backup is left in place for manual investigation.

If the audit reports **Locked**, Explorer or another process still holds a
DLL. Sign out and back into Windows when convenient, then rerun the audit.
The tool never terminates Explorer, requests elevation, or changes registry,
user settings, credentials, PATH or certificate trust.

Only after reviewing an **Eligible** audit result and the two reported file
hashes, explicitly opt in to removing *that one* backup:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\inspect-legacy-backup.ps1 -BackupPath "$env:LOCALAPPDATA\RightConvert\.backup-0123456789abcdef0123456789abcdef" -Cleanup
~~~

There is no bulk cleanup option. Before deletion, the script repeats its checks
and refuses a changed or locked backup. It deletes only the two validated DLL
files and then attempts to remove the **empty** backup folder, never recursively
deleting unknown content. Backups managed by the newer .owner marker continue
to use the standard -CleanupBackupsOnly flow instead.

An isolated regression for this legacy utility uses only disposable fixture
paths and the locally compiled native DLLs. It also reproduces a data-only
root without install-marker.txt and proves user preferences/logs remain intact:

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\test-legacy-backup.ps1
~~~

Regression (uses a unique local test fixture and isolated HKCU test keys):

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\test-reinstall-locked-backup.ps1
~~~

It executes the real install.ps1 while a native DLL from the old application
is loaded, checks successful upgrade and RunOnce cancellation, then releases
the DLL and verifies that backup cleanup leaves the active app intact.

## Windows 11 modern/compact Explorer menu

The C++ extension in shell\windows11 implements IExplorerCommand and forwards selected paths to RightConvert.exe --files. Its COM CLSID appears in the sparse MSIX manifest (shell\windows11\AppxManifest.xml); a Windows 11 package registers windows.comServer and windows.fileExplorerContextMenus with Type="*" for files.

Build the native DLL and copy it next to the PyInstaller app:

~~~powershell
.\installer\build-native.ps1 -Architecture x64 -PackageDir '.\dist\RightConvert'
~~~

Build and sign the identity-only sparse MSIX. This also creates app logos under dist\RightConvert\Assets, which must remain in the app's external installation folder:

~~~powershell
.\installer\build-sparse.ps1 -ExternalDirectory '.\dist\RightConvert' -Publisher 'CN=Your Trusted Publisher' -CertificateThumbprint 'CERTIFICATE_SHA1_THUMBPRINT'
~~~

The Publisher string must exactly match the certificate subject. The thumbprint selects a certificate in CurrentUser\My; signing uses SHA-256. No password is stored in the repository. Without -CertificateThumbprint the script makes an **unsigned MSIX for build verification only**; Windows will not install it. The scripts do not modify certificate trust, enable Developer Mode, or request elevation.

Then install the copied binaries and register the signed MSIX against the installed external location:

~~~powershell
.\installer\install.ps1 -PackageDir '.\dist\RightConvert'
.\installer\install-modern.ps1 -SignedMsix '.\installer\out\RightConvert.ContextMenu.x64.msix'
~~~

Right-click a normal file in File Explorer. If Windows 11 has not refreshed its menu registration, restart Explorer or sign out and back in. Check registration via Get-AppxPackage -Name RightConvert.ContextMenu.

This manifest targets x64; compiling an ARM64 DLL alone does not create an ARM64 MSIX. Native support still requires testing Explorer activation on Windows 11 with a **trusted signed** package.

## Local validation

~~~powershell
.\installer\build-native.ps1 -Architecture x64
ctest --test-dir .\shell\windows11\out\x64 -C Release --output-on-failure
.\installer\test-install-root.ps1
~~~

The COM smoke test loads the DLL and checks its class factory, IExplorerCommand title/state/CLSID/flags, and unload behavior. The package build can be verified with an unsigned MSIX without trusting any certificate. Neither check alone proves modern Explorer activation; the final signed install must also be checked on a Windows 11 desktop.

Microsoft references:

- [File Explorer commands for packaged desktop apps](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/integrate-packaged-app-with-file-explorer)
- [Sparse package with external location](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/grant-identity-to-nonpackaged-apps)
- [Verb selection models](https://learn.microsoft.com/en-us/windows/win32/shell/how-to-employ-the-verb-selection-model)
- [IExecuteCommand and IObjectWithSelection](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-iexecutecommand)
- [Choosing a context-menu verb method](https://learn.microsoft.com/en-us/windows/win32/shell/shortcut-choose-method)
