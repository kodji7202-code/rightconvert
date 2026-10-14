# Microsoft Store listing — DRAFT (not submitted)

Product: RightConvert · Store ID 9NJLJ335RDFM · Publisher: Kodji.
Nothing here has been entered in Partner Center. Review before use.

Note: the application interface is currently **Romanian only**. The English
listing says so explicitly to avoid a "listing does not match the app"
certification note.

## English (en-US)

**Short description** (≤ 1,000 chars; shown at the top on Windows)

> Convert images, PDFs, audio, video and Office documents locally, straight from the File Explorer right-click menu. No uploads, no account, no telemetry.

**Description**

> RightConvert converts files on your own PC. Right-click one or more files in File Explorer, choose "Convert with RightConvert", pick a format, and the result appears next to the original. Your files are never uploaded.
>
> What it converts
> • Images: JPG, PNG, WebP, BMP, TIFF and single-frame GIF to JPG, PNG, WebP, BMP or TIFF
> • PDF to images (single page) or a PNG ZIP / multipage TIFF for every page; images to PDF, including merging 2–64 images
> • Audio and video (MP3, WAV, FLAC, Ogg, Opus, MP4, WebM, MOV, AVI, MKV) using your own FFmpeg installation
> • DOCX, XLSX, PPTX, ODT, ODS and ODP to PDF using your own LibreOffice installation
>
> Why RightConvert
> • Works offline: no cloud service, no account, no telemetry
> • Batch conversion with a clear queue, cancellation and presets
> • Originals are never overwritten; outputs are written safely
> • Windows 11 right-click menu integration
>
> Requirements and notes
> • Audio/video conversion requires FFmpeg and FFprobe, installed separately. Office-to-PDF requires LibreOffice, installed separately. Image and PDF conversion work out of the box.
> • Documents with macros, external links, embedded objects or spreadsheet formulas are refused for safety.
> • The app interface is in Romanian.

**Features (bullet list field)**
- Right-click conversion in File Explorer
- Offline, private: files never leave your PC
- Images, PDF, audio/video (FFmpeg), Office→PDF (LibreOffice)
- Batch conversion, originals preserved

**Search terms**: file converter, image converter, pdf to png, webp, convert offline

## Română (ro-RO)

**Descriere scurtă**

> Convertește imagini, PDF-uri, audio, video și documente Office local, direct din meniul de clic dreapta din File Explorer. Fără încărcări, fără cont, fără telemetrie.

**Descriere**

> RightConvert convertește fișierele direct pe calculatorul tău. Dă clic dreapta pe unul sau mai multe fișiere în File Explorer, alege „Convert with RightConvert”, selectează formatul, iar rezultatul apare lângă fișierul original. Fișierele tale nu sunt încărcate nicăieri.
>
> Ce convertește
> • Imagini: JPG, PNG, WebP, BMP, TIFF și GIF cu un singur cadru în JPG, PNG, WebP, BMP sau TIFF
> • PDF în imagini (o pagină) sau arhivă PNG ZIP / TIFF multipagină pentru toate paginile; imagini în PDF, inclusiv combinarea a 2–64 de imagini
> • Audio și video (MP3, WAV, FLAC, Ogg, Opus, MP4, WebM, MOV, AVI, MKV) cu FFmpeg instalat de tine
> • DOCX, XLSX, PPTX, ODT, ODS și ODP în PDF cu LibreOffice instalat de tine
>
> De ce RightConvert
> • Funcționează offline: fără serviciu cloud, fără cont, fără telemetrie
> • Conversie în lot, cu listă clară, anulare și presetări
> • Originalele nu sunt niciodată suprascrise; rezultatele se scriu în siguranță
> • Integrare în meniul de clic dreapta din Windows 11
>
> Cerințe și observații
> • Conversia audio/video necesită FFmpeg și FFprobe, instalate separat. Conversia Office în PDF necesită LibreOffice, instalat separat. Conversia imaginilor și a PDF-urilor funcționează imediat.
> • Documentele cu macrocomenzi, legături externe, obiecte încorporate sau formule de calcul sunt refuzate din motive de siguranță.

**Funcționalități**
- Conversie din meniul de clic dreapta din File Explorer
- Offline și privat: fișierele nu părăsesc calculatorul
- Imagini, PDF, audio/video (FFmpeg), Office→PDF (LibreOffice)
- Conversie în lot, originalele păstrate

## runFullTrust justification (Submission options → restricted capabilities)

> RightConvert is a classic Win32 desktop application (Python, packaged with PyInstaller) distributed as a full MSIX package; it is not a UWP app, so it requires runFullTrust to run at all. Full trust is also needed to: (1) read and write the user's files at arbitrary locations they select in File Explorer and save the converted output next to the originals; (2) host the Windows 11 File Explorer context-menu command (IExplorerCommand COM server declared via windows.comServer and desktop4/desktop5:fileExplorerContextMenus); and (3) launch the user's separately installed, optional converters (FFmpeg/FFprobe and LibreOffice) as local child processes. The app works entirely offline, collects no personal data and has no telemetry.

## Other submission items to prepare

- **Privacy policy URL**: provide one even if Partner Center does not mark it
  mandatory (desktop full-trust apps are commonly asked for it). Content:
  "RightConvert processes files locally; it collects, stores and transmits no
  personal data; no telemetry; settings and a local technical log stay on the
  device." Host it e.g. as a GitHub Pages page or a page on your site.
- **Age rating**: complete the IARC questionnaire as a utility with no user
  interaction, no user-generated content sharing, no purchases, no location →
  expected rating 3+/PEGI 3/Everyone.
- **Category**: Utilities & tools → File managers (or Productivity).
- **Screenshots**: at least one (1366×768 or larger PNG); suggest the main queue
  window and the Windows 11 right-click menu.
- **Additional system requirements**: mention FFmpeg/FFprobe and LibreOffice as
  optional separately installed components.
- **Pricing**: decide free vs paid in Partner Center. The app has no in-app
  license gate today.
