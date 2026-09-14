//! Port of `park_tiff_core.py` — core logic for loading and processing
//! Park AFM TIFF data files, independent of any UI framework.

use anyhow::{Context, Result};
use image::DynamicImage;
use rayon::prelude::*;
use std::path::{Path, PathBuf};

use crate::metadata;

/// Recommended colormap per channel (mirrors CHANNEL_COLORS).
pub fn colormap_for_channel(channel: Option<&str>) -> &'static str {
    match channel.unwrap_or("") {
        "Z Height" => "terrain",
        "EFM Phase" => "twilight",
        "EFM Amplitude" => "viridis",
        "KPFM Potential" => "rdbu_r",
        "Lock-In4 Phase" => "hsv",
        "Lock-In4 Amplitude" => "plasma",
        "NCM Amplitude" => "magma",
        "Aux1 Out" => "gray",
        _ => "viridis",
    }
}

/// Parse a Park AFM filename: `<experiment>_<date?>_<Channel>_<Direction>_<NNN>` (loose).
pub fn parse_park_filename(name: &str) -> ParkFileMeta {
    let stem = Path::new(name)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(name);

    // frame number: last 1-3 digits at end
    let digits_rev: String = stem
        .chars()
        .rev()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    let digits: String = digits_rev.chars().rev().collect();
    let frame = if digits.len() <= 3 && !digits.is_empty() {
        digits.parse::<u32>().ok()
    } else {
        None
    };

    let direction = if stem.contains("_Forward_") || stem.ends_with("_Forward") {
        Some("Forward")
    } else if stem.contains("_Backward_") || stem.ends_with("_Backward") {
        Some("Backward")
    } else {
        None
    };

    const KNOWN: [&str; 9] = [
        "Z Height",
        "EFM Phase",
        "EFM Amplitude",
        "KPFM Potential",
        "Lock-In4 Phase",
        "Lock-In4 Amplitude",
        "NCM Amplitude",
        "Aux1 Out",
        "Vision",
    ];
    let channel = KNOWN.iter().find(|c| stem.contains(**c)).copied();

    let experiment = stem.split('_').next().unwrap_or("Unknown").to_string();

    ParkFileMeta {
        filename: name.to_string(),
        experiment,
        channel: channel.map(|s| s.to_string()),
        direction: direction.map(|s| s.to_string()),
        frame,
        full_name: stem.to_string(),
        path: PathBuf::new(),
        tiff_meta: None,
    }
}

/// Metadata for one TIFF file, as parsed from the filename.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ParkFileMeta {
    pub filename: String,
    pub experiment: String,
    pub channel: Option<String>,
    pub direction: Option<String>,
    pub frame: Option<u32>,
    pub full_name: String,
    // runtime-only fields (not part of the python dict parity)
    #[serde(default)]
    pub path: PathBuf,
    #[serde(default)]
    pub tiff_meta: Option<metadata::TiffMetadata>,
}

/// Scan `folder` for *.tif(f) files and parse each.
pub fn get_tiff_files(folder: &Path) -> Vec<ParkFileMeta> {
    if !folder.exists() {
        return vec![];
    }
    let mut paths: Vec<PathBuf> = vec![];
    if let Ok(rd) = std::fs::read_dir(folder) {
        for e in rd.flatten() {
            let p = e.path();
            let ext = p
                .extension()
                .and_then(|e| e.to_str())
                .map(|s| s.to_ascii_lowercase());
            if matches!(ext.as_deref(), Some("tiff") | Some("tif")) {
                paths.push(p);
            }
        }
    }
    paths.sort();
    paths
        .par_iter()
        .map(|p| {
            let name = p
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or_default()
                .to_string();
            let mut m = parse_park_filename(&name);
            m.path = p.clone();
            m.tiff_meta = metadata::extract_tiff_metadata(p).ok();
            m
        })
        .collect()
}

/// Load a TIFF as f32 grayscale, preserving 16-bit precision
/// (mirrors python: np.array(img, dtype=np.float32)).
pub fn load_tiff_f32(path: &Path) -> Result<Vec<f32>> {
    let img = image::open(path).with_context(|| format!("open {:?}", path))?;
    let vec: Vec<f32> = match img {
        image::DynamicImage::ImageLuma8(g) => g.into_raw().into_iter().map(|v| v as f32).collect(),
        image::DynamicImage::ImageLuma16(g) => {
            g.into_raw().into_iter().map(|v| v as f32).collect()
        }
        image::DynamicImage::ImageLumaA16(g) => g
            .into_raw()
            .iter()
            .step_by(2)
            .map(|&v| v as f32)
            .collect(),
        other => other
            .to_luma32f()
            .into_raw()
            .into_iter()
            .map(|v| (v * 255.0))
            .collect(),
    };
    Ok(vec)
}

/// (width, height) — thin helper kept for parity with earlier API.
pub fn load_tiff(path: &Path) -> Result<image::GrayImage> {
    let img = image::open(path).with_context(|| format!("open {:?}", path))?;
    Ok(img.to_luma8())
}

/// (width, height)
pub fn tiff_dimensions(path: &Path) -> Result<(u32, u32)> {
    let img = image::image_dimensions(path).with_context(|| format!("dims {:?}", path))?;
    Ok((img.0, img.1))
}

/// Check if an image is complete (mirrors check_image_complete).
pub fn check_image_complete(data: &[f32], w: usize, h: usize, threshold_ratio: f32) -> (bool, f32) {
    if data.is_empty() || w == 0 || h == 0 || data.len() != w * h {
        return (false, 0.0);
    }
    let mut incomplete_rows = 0usize;
    for row in (0..h).rev() {
        let row_slice = &data[row * w..(row + 1) * w];
        let first = row_slice[0];
        let all_same = row_slice.iter().all(|&v| v == first);
        let mean = row_slice.iter().sum::<f32>() / w as f32;
        let var = row_slice.iter().map(|v| (v - mean).powi(2)).sum::<f32>() / w as f32;
        if all_same || var.sqrt() < 1e-10 {
            incomplete_rows += 1;
        } else {
            break;
        }
    }
    let completion_percent = 100.0 * (h - incomplete_rows) as f32 / h as f32;
    let is_complete = (incomplete_rows as f32 / h as f32) < threshold_ratio;
    (is_complete, completion_percent)
}

pub fn check_file_complete(path: &Path, threshold_ratio: f32) -> Result<(bool, f32)> {
    let (w, h) = tiff_dimensions(path)?;
    let data = load_tiff_f32(path)?;
    let d = if data.len() == w as usize * h as usize {
        (data, w as usize, h as usize)
    } else {
        (data, 0, 0)
    };
    let (c, pct) = check_image_complete(&d.0, d.1, d.2, threshold_ratio);
    Ok((c, pct))
}

pub fn filter_files(
    files: &[ParkFileMeta],
    channels: Option<&[String]>,
    directions: Option<&[String]>,
    experiments: Option<&[String]>,
) -> Vec<ParkFileMeta> {
    files
        .iter()
        .filter(|f| {
            let keep = |sel: Option<&[String]>, field: &Option<String>| match sel {
                None => true,
                Some(list) => match field {
                    // python: if field is None it is kept even when filter is set
                    None => true,
                    Some(v) => list.contains(v),
                },
            };
            keep(channels, &f.channel)
                && keep(directions, &f.direction)
                && keep(experiments, &Some(f.experiment.clone()))
        })
        .cloned()
        .collect()
}

pub fn get_unique_values(files: &[ParkFileMeta]) -> (Vec<String>, Vec<String>, Vec<String>) {
    let mut ch: Vec<String> = files
        .iter()
        .filter_map(|f| f.channel.clone())
        .collect::<Vec<_>>();
    let mut dir: Vec<String> = files
        .iter()
        .filter_map(|f| f.direction.clone())
        .collect::<Vec<_>>();
    let mut exp: Vec<String> = files.iter().map(|f| f.experiment.clone()).collect();
    ch.sort();
    ch.dedup();
    dir.sort();
    dir.dedup();
    exp.sort();
    exp.dedup();
    (ch, dir, exp)
}

pub fn get_frames_info(files: &[ParkFileMeta]) -> Vec<(String, u32)> {
    let mut keys: Vec<(String, u32)> = files
        .iter()
        .filter_map(|f| f.frame.map(|fr| (f.experiment.clone(), fr)))
        .collect();
    keys.sort();
    keys.dedup();
    keys
}

/// All channel files for a specific (experiment, frame), sorted by channel.
pub fn get_all_channels_for_frame(
    files: &[ParkFileMeta],
    experiment: &str,
    frame: u32,
    direction: Option<&str>,
) -> Vec<ParkFileMeta> {
    let mut matching: Vec<ParkFileMeta> = files
        .iter()
        .filter(|f| {
            f.experiment == experiment
                && f.frame == Some(frame)
                && f.channel.is_some()
                && direction.map_or(true, |d| f.direction.as_deref() == Some(d))
        })
        .cloned()
        .collect();
    matching.sort_by(|a, b| match (&a.channel, &b.channel) {
        (Some(x), Some(y)) => x.cmp(y),
        _ => std::cmp::Ordering::Equal,
    });
    matching
}

/// Locate a Gwyddion install for the current OS (mirrors find_gwyddion_executable).
pub fn find_gwyddion_executable() -> Option<PathBuf> {
    let candidates: &[&str] = match std::env::consts::OS {
        "macos" => &["/Applications/Gwyddion.app"],
        "windows" => &[
            r"C:\Program Files (x86)\Gwyddion\bin\gwyddion.exe",
            r"C:\Program Files\Gwyddion\bin\gwyddion.exe",
        ],
        _ => &["/usr/bin/gwyddion", "/usr/local/bin/gwyddion"],
    };
    for c in candidates {
        let p = PathBuf::from(c);
        if p.exists() {
            return Some(p);
        }
    }
    which("gwyddion")
}

/// Minimal PATH lookup (avoids a dependency on the `which` crate).
pub fn which(prog: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(prog);
        #[cfg(target_os = "windows")]
        {
            for ext in [".exe", ".bat", ""] {
                let cand = dir.join(format!("{prog}{ext}"));
                if cand.is_file() {
                    return Some(cand);
                }
            }
        }
        #[cfg(not(target_os = "windows"))]
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

/// Open a file in Gwyddion (mirrors open_in_gwyddion).
pub fn open_in_gwyddion(file_path: &Path, gwyddion_path: Option<&Path>) -> Result<String> {
    if !file_path.is_file() {
        anyhow::bail!("File not found: {}", file_path.display());
    }
    let gwy = gwyddion_path
        .map(Path::to_path_buf)
        .or_else(|| find_gwyddion_executable())
        .context("Could not find Gwyddion. Install it or set the path manually.")?;

    #[cfg(target_os = "macos")]
    let ok = {
        if gwy.extension().map(|e| e == "app").unwrap_or(false) {
            std::process::Command::new("open")
                .arg("-a")
                .arg(&gwy)
                .arg(file_path)
                .spawn()
                .is_ok()
        } else {
            std::process::Command::new(&gwy).arg(file_path).spawn().is_ok()
        }
    };
    #[cfg(not(target_os = "macos"))]
    let ok = std::process::Command::new(&gwy).arg(file_path).spawn().is_ok();

    if ok {
        Ok(format!(
            "Opened {} in Gwyddion",
            file_path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("file")
        ))
    } else {
        anyhow::bail!("Failed to launch Gwyddion")
    }
}

/// Copy text to clipboard (mirrors copy_to_clipboard).
pub fn copy_to_clipboard(text: &str) -> Result<String> {
    use arboard::Clipboard;
    let mut cb = arboard::Clipboard::new().context("clipboard unavailable")?;
    cb.set_text(text.to_string())
        .context("failed to set clipboard text")?;
    Ok("Copied to clipboard".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_filename() {
        let m = parse_park_filename("efm_after_writing_260116_EFM Phase_Forward_007.tiff");
        assert_eq!(m.experiment, "efm");
        assert_eq!(m.channel.as_deref(), Some("EFM Phase"));
        assert_eq!(m.direction.as_deref(), Some("Forward"));
        assert_eq!(m.frame, Some(7));
    }

    #[test]
    fn test_parse_filename_backward() {
        let m = parse_park_filename("scan_20260101_Z Height_Backward_003.tiff");
        assert_eq!(m.channel.as_deref(), Some("Z Height"));
        assert_eq!(m.direction.as_deref(), Some("Backward"));
        assert_eq!(m.frame, Some(3));
    }

    #[test]
    fn test_parse_no_frame() {
        let m = parse_park_filename("weird_name.tiff");
        assert_eq!(m.experiment, "weird");
        assert!(m.frame.is_none());
        assert_eq!(m.channel, None);
    }
}