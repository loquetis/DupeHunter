# DupeHunter 1.0.0

Finds duplicate files and near-duplicate photos on Windows and sends the ones you pick to the **Recycle Bin**. Nothing is ever permanently deleted and nothing is deleted without your explicit confirmation.

- **How it works:** Python localhost-only server (127.0.0.1:8768, env `DUPEHUNTER_PORT`) plus a web UI in a private Edge app window (own profile, extensions/sync/sign-in off). A random per-launch token is required on every change request, strict CSP, DOM built with textContent only.
- **Data:** `%APPDATA%\DupeHunter` (settings.json, window profile). Override with `DUPEHUNTER_DATA`.
- **Scan:** group by size, then a head+tail partial hash, then a full SHA-256. Near-duplicate photos use a perceptual (dHash) comparison with a Similarity setting (strict/normal/loose). HEIC/HEIF is read via pillow-heif (bundled); if a HEIC cannot be decoded it is skipped and listed under "Skipped", not fatal.
- **Skips by default:** Windows, Program Files, ProgramData, AppData, `$Recycle.Bin`, System Volume Information, junctions, and OneDrive online-only (not downloaded) files so scanning never triggers a download. "Include system folders" is an explicit opt-in. Hard links to the same file are not reported as duplicates.
- **Review:** sets with thumbnails, size, path, modified date; helpers Keep newest / oldest / largest; Open folder (Explorer select); a set can never have all its files selected; a confirm dialog lists exactly what goes to the Recycle Bin. CSV export of all sets.
- **Robustness:** long paths (`\\?\`), permission errors counted and listed, cancellable scan with live progress.
- **Run:** `DupeHunter.exe` (add `--no-window` for server only). Build: `build.ps1` (PyInstaller one-file, windowed, icon + version info).
- **Tests:** `python tests/test_engine.py`, `python tests/uitest.py` (Playwright), `python tests/test_should_exit.py`.

## Bugs / edge cases caught in review
1. Settings were saved before validation, so one bad folder path poisoned the saved list. Now validated first.
2. Partial hash skipped the tail of 64–128 KB files, so files differing only at the end looked equal until the full hash. Now head+tail; regression fixture.
3. The same file could appear in an exact set and a similar-photo set under two ids, letting a delete remove "every" copy. One record per path, and every set must keep at least one member server-side.
4. `DirEntry.stat()` has no inode on Windows, so hard-link detection silently did nothing. Uses a real `os.stat` on same-size candidates.
5. Hashing OneDrive online-only files would download them. Now skipped by attribute. Junction skip was too broad (any reparse point) and hid real folders.
6. Delete requires `confirm == "recycle"` plus the current scan id, so a stale tab cannot delete from an old scan.

## Limitations
Windows only for the Recycle Bin move (on other OSes tests use a fake trash). Near-duplicate matching is for photos (jpg/png/webp/bmp/gif/tiff/HEIC), not video. Not code-signed, so SmartScreen may warn.
