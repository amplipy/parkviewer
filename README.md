# Parkviewer

Standalone **native** desktop app for browsing and previewing Park AFM TIFF
data files. Single self-contained binary per OS — no Python, no runtime,
no webview. Written in Rust (egui).

## Download

CI builds binaries for four platforms on every push to `main` and attaches
them to the rolling [`native` release](../../releases/tag/native):

| File | Platform |
|---|---|
| `parkviewer-macos-arm64.tar.gz` | Apple Silicon |
| `parkviewer-macos-x64.tar.gz` | Intel Mac |
| `parkviewer-linux-x64.tar.gz` | Linux x86_64 |
| `parkviewer-windows-x64.zip` | Windows x64 |

Extract and run the single `parkviewer` binary. First launch on macOS:
right-click → **Open** (binary is unsigned).

## Build from source

```bash
cd rust
cargo build --release
# binary: rust/target/release/parkviewer
```

Requires only a Rust toolchain (no system libs — egui renders via GPU).

## Features

- Folder scan of Park AFM TIFFs (`.tif`/`.tiff`), parallel metadata parse
- Custom Park TIFF tags: channel (UTF-16-LE in tag 50435), z-scale,
  scan rate, scan size, software, datetime
- Filename grammar: experiment / channel / direction / frame number
- Filters: channels, directions, experiments; hide-incomplete detection
  (interrupted-scan truncation)
- Three views: Single File, All Channels per frame, Gallery
- Colormaps per channel (viridis/plasma/magma/terrain/hsv/twilight/RdBu_r…,
  matplotlib-parity LUTs), auto or manual selection
- Color scale: percentile (auto) or manual center/scale
- Background subtraction: plane, line mean/median/poly, 2D polynomial,
  Fourier high-pass — all NaN-aware, numpy-parity numerics
- 16-bit TIFF precision preserved end-to-end
- "Open in Gwyddion" handoff (auto-locates Gwyddion per OS) and
  copy-path-to-clipboard
- Folder + Gwyddion paths persisted between runs

## Layout

```
rust/
  src/core.rs        folder scan, filename grammar, completeness, filters
  src/metadata.rs    raw TIFF tag parser (Park tag 50435 + 305/306)
  src/backgrounds.rs the 7 background-subtraction methods
  src/colormaps.rs   deterministic colormap LUTs
  src/app.rs         egui UI
  tests/pipeline.rs  end-to-end tests vs synthetic Park TIFF corpus
.github/workflows/rust.yml   4-OS build matrix -> rolling 'native' release
scripts/make_icon.py         deterministic icon generator (PIL)
```

## Tests

```bash
cd rust && cargo test
```

## History

The original version of this app was a Streamlit script
(`park_tiff_viewer.py` + `park_tiff_core.py`, by NanosparQ Training).
It was ported to Rust so the app ships as one self-contained binary per
platform. The Python core module's behavior is preserved function-for-
function in `rust/src/`; the Streamlit viewer remains in the repo history.