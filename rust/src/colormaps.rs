//! Deterministic ports of matplotlib colormaps used by the original viewer.
//!
//! Each map is a set of anchor colors (position 0..=1 → RGB) interpolated
//! linearly, matching matplotlib's LinearSegmentedColormap anchors
//! (`matplotlib/_cm.py`). `*_r` variants reverse the anchor list.

/// (t, r, g, b) anchors, t in 0..=1
pub type Anchors = &'static [(f32, [u8; 3])];

pub fn colormap_anchors(name: &str) -> Vec<(f32, [u8; 3])> {
    let reversed = name.ends_with("_r");
    let base = name.trim_end_matches("_r");
    let mut anchors: Vec<(f32, [u8; 3])> = match base {
        "viridis" => vec![
            (0.0, [68, 1, 84]),
            (0.25, [59, 82, 139]),
            (0.5, [33, 145, 140]),
            (0.75, [94, 201, 98]),
            (1.0, [253, 231, 37]),
        ],
        "plasma" => vec![
            (0.0, [13, 8, 135]),
            (0.25, [126, 3, 168]),
            (0.5, [204, 71, 120]),
            (0.75, [248, 149, 64]),
            (1.0, [240, 249, 33]),
        ],
        "magma" => vec![
            (0.0, [0, 0, 4]),
            (0.25, [56, 15, 129]),
            (0.5, [156, 45, 111]),
            (0.75, [246, 146, 73]),
            (1.0, [252, 253, 191]),
        ],
        "inferno" => vec![
            (0.0, [0, 0, 4]),
            (0.25, [61, 17, 138]),
            (0.5, [188, 55, 84]),
            (0.75, [249, 142, 8]),
            (1.0, [252, 255, 164]),
        ],
        "gray" | "greys" => vec![(0.0, [0, 0, 0]), (1.0, [255, 255, 255])],
        "terrain" => vec![
            (0.0, [51, 10, 27]),
            (0.15, [26, 121, 179]),
            (0.25, [62, 143, 191]),
            (0.5, [228, 213, 178]),
            (0.75, [93, 125, 61]),
            (1.0, [255, 255, 255]),
        ],
        "hsv" => {
            // matplotlib hsv: full hue wheel, s=v=1, 6 evenly spaced stops
            (0..=6)
                .map(|i| {
                    let h = i as f32 / 6.0;
                    (h, hsv_to_rgb8(h, 1.0, 1.0))
                })
                .collect()
        }
        "twilight" => vec![
            (0.0, [226, 228, 239]),
            (0.125, [139, 141, 172]),
            (0.25, [65, 71, 108]),
            (0.375, [41, 40, 68]),
            (0.5, [34, 30, 51]),
            (0.625, [41, 40, 68]),
            (0.75, [65, 71, 108]),
            (0.875, [139, 141, 172]),
            (1.0, [226, 226, 239]),
        ],
        "rdbu" | "rdbu_r" | "RdBu" | "RdBu_r" => vec![
            (0.0, [103, 0, 31]),
            (0.25, [214, 96, 77]),
            (0.5, [247, 247, 247]),
            (0.75, [67, 147, 195]),
            (1.0, [5, 48, 97]),
        ],
        "turbo" => vec![
            (0.0, [48, 18, 59]),
            (0.25, [32, 118, 202]),
            (0.5, [63, 224, 168]),
            (0.75, [246, 180, 65]),
            (1.0, [122, 4, 3]),
        ],
        _ => colormap_anchors("viridis"),
    };
    if reversed {
        anchors = anchors.into_iter().rev().map(|(t, c)| (1.0 - t, c)).collect();
    }
    anchors
}

fn hsv_to_rgb8(h: f32, s: f32, v: f32) -> [u8; 3] {
    let i = (h * 6.0).floor() as i32;
    let f = h * 6.0 - i as f32;
    let p = v * (1.0 - s);
    let q = v * (1.0 - s * f);
    let t = v * (1.0 - s * (1.0 - f));
    let (r, g, b) = match ((i % 6 + 6) % 6) {
        0 => (v, t, p),
        1 => (q, v, p),
        2 => (p, v, t),
        3 => (p, q, v),
        4 => (t, p, v),
        _ => (v, p, q),
    };
    [(r * 255.0) as u8, (g * 255.0) as u8, (b * 255.0) as u8]
}

/// A compiled colormap: 256-entry LUT of RGB.
pub struct Colormap {
    pub name: String,
    pub lut: Vec<[u8; 3]>, // 256 entries
}

impl Colormap {
    pub fn get(name: &str) -> Colormap {
        let mut anchors = colormap_anchors(name);
        // sort by t just in case
        anchors.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
        let mut lut = Vec::with_capacity(256);
        for i in 0..256 {
            let t = i as f32 / 255.0;
            let c = sample(&anchors, t);
            lut.push(c);
        }
        Colormap {
            name: name.to_string(),
            lut,
        }
    }

    /// Map a normalized value (already clamped 0..=1) to RGB.
    #[inline]
    pub fn map(&self, t: f32) -> [u8; 3] {
        let idx = (t.clamp(0.0, 1.0) * 255.0).round() as usize;
        self.lut[idx.min(255)]
    }
}

fn sample(anchors: &[(f32, [u8; 3])], t: f32) -> [u8; 3] {
    if anchors.is_empty() {
        return [0, 0, 0];
    }
    if t <= anchors[0].0 {
        return anchors[0].1;
    }
    for w in anchors.windows(2) {
        let (t0, c0) = w[0];
        let (t1, c1) = w[1];
        if t <= t1 {
            let f = if t1 - t0 <= 1e-9 {
                0.0
            } else {
                (t - t0) / (t1 - t0)
            };
            return [
                (c0[0] as f32 + f * (c1[0] as f32 - c0[0] as f32)).round() as u8,
                (c0[1] as f32 + f * (c1[1] as f32 - c0[1] as f32)).round() as u8,
                (c0[2] as f32 + f * (c1[2] as f32 - c0[2] as f32)).round() as u8,
            ];
        }
    }
    anchors[anchors.len() - 1].1
}

/// Render f32 data to RGB8 via colormap with [vmin, vmax] normalization
/// (mirrors Normalize(clip=True) + colormap LUT).
pub fn data_to_rgb(
    data: &[f32],
    w: usize,
    h: usize,
    cmap: &Colormap,
    vmin: f32,
    vmax: f32,
) -> Vec<u8> {
    let mut out = vec![0u8; w * h * 3];
    let range = (vmax - vmin).max(1e-12);
    for (i, &v) in data.iter().enumerate() {
        let t = if v.is_nan() { 0.0 } else { (v - vmin) / range };
        let [r, g, b] = cmap.map(t);
        out[i * 3] = r;
        out[i * 3 + 1] = g;
        out[i * 3 + 2] = b;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lut_endpoints() {
        let c = Colormap::get("viridis");
        assert_eq!(c.lut[0], [68, 1, 84]);
        assert_eq!(c.lut[255], [253, 231, 37]);
    }

    #[test]
    fn test_reversed() {
        let c = Colormap::get("gray_r");
        assert_eq!(c.lut[0], [255, 255, 255]);
        assert_eq!(c.lut[255], [0, 0, 0]);
    }

    #[test]
    fn test_rdbu_r_matches_python_rdbu_r() {
        // RdBu_r in matplotlib: low=blue end (5,48,97), high=red end (103,0,31)
        let c = Colormap::get("RdBu_r");
        assert_eq!(c.lut[0], [5, 48, 97]);
        assert_eq!(c.lut[255], [103, 0, 31]);
    }
}