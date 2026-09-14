# Parkviewer

Standalone desktop app for browsing and previewing Park AFM TIFF data files
(data summary generator for Park AFM data).

Built on the original Streamlit viewer (`park_tiff_viewer.py` +
`park_tiff_core.py`), packaged with PyInstaller so it runs as a normal
desktop app on **macOS, Windows, and Linux** — no Python install required.

## Download prebuilt apps

CI builds installers for all three platforms on every push to `main`:

1. Open the repo's **Actions → Build desktop app** page, or the rolling
   [releases page](../../releases) (`desktop` prerelease).
2. Grab the artifact for your OS:
   - `ParkViewer-macos.zip` → unzip → `ParkViewer.app` (first launch:
     right-click → **Open** to bypass Gatekeeper)
   - `ParkViewer-windows.zip` → unzip → run `ParkViewer\ParkViewer.exe`
   - `ParkViewer-linux.tar.gz` → `tar xzf` → run `./ParkViewer/ParkViewer`

## Run from source

```bash
python -m venv .venv
./.venv/bin/pip install -r requirements.txt
streamlit run park_tiff_viewer.py        # classic browser mode
./.venv/bin/python app_entry.py          # desktop-window mode
```

## How the desktop wrapper works

Streamlit serves a web page only, so `app_entry.py`:

1. spawns the same executable as a child process running the Streamlit
   server on `127.0.0.1` (Streamlit must own the main thread — it installs
   signal handlers);
2. waits for the `/_stcore/health` endpoint;
3. opens a native window via pywebview (WebKit on macOS, WebView2 on
   Windows, Qt WebEngine on Linux) and falls back to the system browser
   automatically if no native backend is available;
4. terminates the server child when the window closes.

`PARKVIEWER_SERVE_ONLY=1 <binary>` runs just the server (used by the CI
smoke test).

## Building yourself

| OS | Command |
|---|---|
| macOS | `./scripts/build_macos.sh` → `dist/ParkViewer.app` |
| Windows | `scripts\build_windows.bat` → `dist\ParkViewer\` |
| Linux | `./scripts/build_linux.sh` → `dist/ParkViewer/` |

All three share `packaging/pyinstaller.spec`. Icons come from
`scripts/make_icon.py` (PIL-drawn, deterministic).

## Notes

- "Open with Gwyddion" locates Gwyddion automatically on each OS
  (`/Applications/Gwyddion.app`, `C:\Program Files*\Gwyddion\bin\gwyddion.exe`,
  `/usr/bin/gwyddion`).
- The default data folder preset was written for macOS (Box path); on other
  OSes just type your data folder in the sidebar.