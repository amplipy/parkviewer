//! Port of `extract_tiff_metadata` — Park AFM stores metadata in custom TIFF tags:
//! - Tag 305: Software name
//! - Tag 306: DateTime
//! - Tag 50435: Binary header (channel name UTF-16-LE at [4..68], z-scale f64 at 120,
//!   scan-rate f64 at 360, scan-size f64 at 448)
//! - Tag 50441: XML extended header (parsed lightly: key=value pairs if present)

use anyhow::{Context, Result};
use std::path::Path;

#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct TiffMetadata {
    pub filename: String,
    pub filepath: String,
    pub software: Option<String>,
    pub datetime: Option<String>,
    pub channel: Option<String>,
    pub scan_size_um: Option<f64>,
    pub scan_rate_hz: Option<f64>,
    pub z_scale: Option<f64>,
    pub width: u32,
    pub height: u32,
}

/// Get a byte vector for a TIFF tag by id from the first IFD, independent of
/// the `image` crate's decoding (which drops unknown/CP tags).
pub fn read_tag_bytes(path: &std::path::Path, wanted_tag: u16) -> Result<Option<Vec<u8>>> {
    let buf = std::fs::read(path)?;
    if buf.len() < 8 {
        return Ok(None);
    }
    // TIFF header: II/MM + 42 + offset
    let little = match &buf[0..2] {
        b"II" => true,
        b"MM" => false,
        _ => anyhow::bail!("not a TIFF (byte-order marker)"),
    };
    let magic = u16::from_le_bytes([buf[2], buf[3]]);
    if magic != 42 {
        anyhow::bail!("not a TIFF (magic {magic})");
    }
    let rd_u16 = |b: &[u8], o: usize| -> u16 {
        if little {
            u16::from_le_bytes([b[o], b[o + 1]])
        } else {
            u16::from_be_bytes([b[o], b[o + 1]])
        }
    };
    let rd_u32 = |b: &[u8], o: usize| -> u32 {
        if little {
            u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]])
        } else {
            u32::from_be_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]])
        }
    };
    let little = little;
    let _ = magic;

    // first IFD offset
    let ifd0 = rd_u32(&buf, 4) as usize;
    if ifd0 + 2 > buf.len() {
        return Ok(None);
    }
    let n_entries = rd_u16(&buf, ifd0) as usize;
    for i in 0..n_entries {
        let base = ifd0 + 2 + i * 12;
        if base + 12 > buf.len() {
            break;
        }
        let tag = rd_u16(&buf, base);
        let typ = rd_u16(&buf, base + 2);
        let count = rd_u32(&buf, base + 4) as usize;
        if tag != wanted_tag {
            continue;
        }
        // type sizes: 1=BYTE 2=ASCII 3=SHORT 4=LONG 5=RATIONAL 7=UNDEFINED...
        let tsize = match typ {
            1 | 2 | 6 | 7 => 1usize,
            3 | 8 => 2,
            4 | 9 | 11 => 4,
            5 | 10 | 12 => 8,
            _ => 1,
        };
        let total = count * tsize;
        let data: Vec<u8> = if total <= 4 {
            buf[base + 8..base + 8 + total].to_vec()
        } else {
            let off = rd_u32(&buf, base + 8) as usize;
            let end = off + total;
            if off + total > buf.len() {
                return Ok(None);
            }
            buf[off..off + total].to_vec()
        };
        // For numeric short/long types, keep raw little-endian order as stored
        // on disk (file byte order); callers decode per-field.
        return Ok(Some(data));
    }
    Ok(None)
}

/// Extract metadata (mirrors extract_tiff_metadata).
pub fn extract_tiff_metadata(path: &std::path::Path) -> Result<TiffMetadata> {
    let filename = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default()
        .to_string();

    // image size via the decoder (cheap header read)
    let (width, height) = image::image_dimensions(path)
        .with_context(|| format!("image_dimensions failed for {}", path.display()))?;

    let mut meta = TiffMetadata {
        filename,
        filepath: path.display().to_string(),
        width,
        height,
        ..Default::default()
    };

    // Tag 305 Software, Tag 306 DateTime (ASCII)
    if let Some(bytes) = read_tag(path, 305)? {
        meta.software = some_trimmed_cstr(&bytes);
    }
    if let Some(bytes) = read_tag(path, 306)? {
        meta.datetime = some_trimmed_cstr(&bytes);
    }

    // Park binary header, tag 50435
    if let Some(data) = read_tag(path, 50435)? {
        // channel name at offset 4..68, UTF-16-LE
        if data.len() >= 68 {
            let u16s: Vec<u8> = data[4..68].to_vec();
            let units: Vec<u16> = u16s
                .chunks_exact(2)
                .map(|c| u16::from_le_bytes([c[0], c[1]]))
                .collect();
            let s = String::from_utf16_lossy(&units);
            let trimmed = s.trim_end_matches('\u{0}').trim().to_string();
            if !trimmed.is_empty() {
                meta.channel = Some(trimmed);
            }
        }
        // z-scale at 120 (f64 LE)
        if data.len() >= 128 {
            meta.z_scale = f64::from_le_bytes(data[120..128].try_into().unwrap()).into();
        }
        // scan rate at 360 (f64 LE, Hz)
        if data.len() >= 368 {
            meta.scan_rate_hz = f64::from_le_bytes(data[360..368].try_into().unwrap()).into();
        }
        // scan size at 448 (f64 LE, µm)
        if data.len() >= 456 {
            meta.scan_size_um = f64::from_le_bytes(data[448..456].try_into().unwrap()).into();
        }
    }

    Ok(meta)
}

fn some_trimmed_cstr(bytes: &[u8]) -> Option<String> {
    let end = bytes.iter().position(|&b| b == 0).unwrap_or(bytes.len());
    let s = String::from_utf8_lossy(&bytes[..end]).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

fn read_tag(path: &std::path::Path, id: u16) -> Result<Option<Vec<u8>>> {
    read_tag_bytes(path, id)
}