# RightConvert Privacy Policy

**Effective date:** 14 October 2026
**Applies to:** the RightConvert desktop app for Windows (Microsoft Store package
`Kodji.RightConvert`, and the non-Store Setup build), published by Kodji.

## Summary

RightConvert converts files **locally, on your own computer, offline**.
It does not collect, sell or share personal data, has no telemetry or analytics,
requires no account, and never uploads your files or anything else over the network.

## What the app does with your files

* Files you add (through the window, drag and drop, or the File Explorer
  "Convert with RightConvert" command) are read and converted **on your device only**.
* Converted files are saved next to the originals or in the output folder you choose.
  Originals are never overwritten or deleted.
* Short-lived temporary working folders may be created on your device during a
  conversion and are removed afterwards.
* The app contains no networking code: it makes no HTTP requests, opens no
  network connections, does not check for updates on its own and does not
  contact any server. License verification, where used, is fully offline.
* Office documents that reference external/remote resources are rejected rather
  than fetched.

## Data stored on your device

RightConvert keeps two small things, **only on your computer**, in
`%LOCALAPPDATA%\RightConvert`:

* `settings.json` – your preferences: the chosen output folder, whether to save next
  to the original, and the locations of FFmpeg / LibreOffice if you selected them.
* `logs\technical.log` – a size-limited technical log (rotated at about 512 KB)
  with timestamps and short error messages such as an error type or a file
  extension. It does not contain file contents and is meant for troubleshooting.

When installed from the Microsoft Store, Windows automatically stores these files
under `%LOCALAPPDATA%\Packages\Kodji.RightConvert_h9nm9abegh7ny\LocalCache\Local\RightConvert`
and removes them when you uninstall the app. You can delete them at any time.
Nothing in them is ever sent anywhere by RightConvert.

The app also reads (never writes) a few Windows settings locally: the light/dark
app theme, and the standard LibreOffice install location in the registry /
Program Files, so it can find an installed LibreOffice.

## Optional external tools

RightConvert does not bundle LibreOffice or FFmpeg. If you have them installed
or point the app to them, RightConvert runs them **locally as separate programs**
on your computer to perform Office→PDF and audio/video conversions. Files are passed
to them on your device; nothing is uploaded. Those tools are third-party software
governed by their own licences and policies.

## Data we collect

**None.** No personal data, usage statistics, crash reports, device identifiers,
advertising identifiers or location are collected by RightConvert, and nothing is
shared with third parties. No account or sign-in is required.

The Microsoft Store itself (downloads, purchases, ratings) is operated by Microsoft
under the [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement);
the developer does not receive personal data from it beyond the aggregated reports
Microsoft provides to all publishers.

## Children

RightConvert does not collect data from anyone, including children.

## Changes

If this policy changes, the updated version will be published at this same address
with a new effective date.

## Contact

Questions or requests: open an issue at
<https://github.com/kodji7202-code/rightconvert/issues>.

---

## Politica de confidențialitate (Română)

RightConvert convertește fișierele **local, pe calculatorul tău, fără internet**.
Aplicația **nu colectează date personale**, nu are telemetrie sau analiză de utilizare,
nu necesită cont și **nu trimite niciun fișier sau altă informație prin rețea**.

* Fișierele sunt citite și convertite doar pe dispozitivul tău; rezultatele se salvează
  lângă original sau în folderul ales. Originalele nu sunt șterse sau suprascrise.
* Setările (`settings.json`: folderul de ieșire, căile către FFmpeg / LibreOffice) și
  jurnalul tehnic (`logs\technical.log`, fără conținutul fișierelor) rămân pe
  dispozitiv, în `%LOCALAPPDATA%\RightConvert` (pentru versiunea din Microsoft Store,
  Windows le păstrează în folderul pachetului și le șterge la dezinstalare).
* Instrumentele externe opționale (LibreOffice, FFmpeg) rulează local, pe calculatorul tău.
* Contact: deschide un issue pe <https://github.com/kodji7202-code/rightconvert/issues>.
