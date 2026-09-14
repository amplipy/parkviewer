//! Port of the background-subtraction functions from `park_tiff_core.py`.
//! Data layout: row-major, `w` columns × `h` rows, f32, NaN = invalid pixel.

/// Background subtraction methods (labels match the Python app exactly).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub enum BgMethod {
    None,
    Plane,
    LineMean,
    LineMedian,
    LinePoly,
    Poly2D,
    FourierHighPass,
}

impl BgMethod {
    pub const ALL: [(BgMethod, &'static str); 7] = [
        (BgMethod::None, "None"),
        (BgMethod::Plane, "Plane"),
        (BgMethod::LineMean, "Line (Mean)"),
        (BgMethod::LineMedian, "Line (Median)"),
        (BgMethod::LinePoly, "Line (Polynomial)"),
        (BgMethod::Poly2D, "Polynomial 2D"),
        (BgMethod::FourierHighPass, "Fourier High-Pass"),
    ];

    pub fn from_label(label: &str) -> BgMethod {
        BgMethod::ALL
            .iter()
            .find(|(_, l)| *l == label)
            .map(|(m, _)| *m)
            .unwrap_or(BgMethod::None)
    }

    pub fn label(&self) -> &'static str {
        BgMethod::ALL
            .iter()
            .find(|(m, _)| m == self)
            .map(|(_, l)| *l)
            .unwrap_or("None")
    }
}

/// Shared parameters (mirrors bg_params: degree + cutoff_fraction).
#[derive(Debug, Clone, Copy, serde::Serialize, serde::Deserialize)]
pub struct BgParams {
    pub degree: usize,        // polynomial methods (default 2)
    pub cutoff_fraction: f32, // Fourier (default 0.05)
}

impl Default for BgParams {
    fn default() -> Self {
        BgParams {
            degree: 2,
            cutoff_fraction: 0.05,
        }
    }
}

/// Apply the given method in place (mirrors apply_background_subtraction).
pub fn apply(data: &mut [f32], w: usize, h: usize, method: BgMethod, params: &BgParams) {
    match method {
        BgMethod::None => {}
        BgMethod::Plane => subtract_plane(data, w, h),
        BgMethod::LineMean => subtract_line_mean(data, w, h),
        BgMethod::LineMedian => subtract_line_median(data, w, h),
        BgMethod::LinePoly => subtract_line_polynomial(data, w, h, params.degree),
        BgMethod::Poly2D => subtract_polynomial_2d(data, w, h, params.degree),
        BgMethod::FourierHighPass => {
            subtract_fourier_highpass(data, w, h, params.cutoff_fraction)
        }
    }
}

// ---------------------------------------------------------------- stats

/// numpy nanpercentile with linear interpolation.
pub fn nanpercentile(data: &[f32], p: f32) -> f32 {
    let mut vals: Vec<f32> = data.iter().copied().filter(|v| !v.is_nan()).collect();
    if vals.is_empty() {
        return f32::NAN;
    }
    vals.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = vals.len();
    let idx = (p / 100.0).clamp(0.0, 1.0) * (n - 1) as f32;
    let lo = idx.floor() as usize;
    let hi = (lo + 1).min(n - 1);
    let frac = idx - lo as f32;
    vals[lo] + frac * (vals[hi] - vals[lo])
}

pub fn nanmin(data: &[f32]) -> f32 {
    data.iter().copied().filter(|v| !v.is_nan()).fold(f32::INFINITY, f32::min)
}
pub fn nanmax(data: &[f32]) -> f32 {
    data.iter().copied().filter(|v| !v.is_nan()).fold(f32::NEG_INFINITY, f32::max)
}
pub fn nanmean(data: &[f32]) -> f32 {
    let mut sum = 0f64;
    let mut n = 0usize;
    for &v in data {
        if !v.is_nan() {
            sum += v as f64;
            n += 1;
        }
    }
    if n == 0 {
        f32::NAN
    } else {
        (sum / n as f64) as f32
    }
}

fn nanmedian_slice(vals: &mut Vec<f32>) -> f32 {
    if vals.is_empty() {
        return f32::NAN;
    }
    vals.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = vals.len();
    if n % 2 == 1 {
        vals[n / 2]
    } else {
        (vals[n / 2 - 1] + vals[n / 2]) / 2.0
    }
}

/// Solve A x = b in place (Gaussian elimination, partial pivoting). n×n.
fn solve_linear(a: &mut [f64], b: &mut [f64], n: usize) -> Option<()> {
    for col in 0..n {
        // pivot
        let mut best = col;
        for r in col..n {
            if a[r * n + col].abs() > a[best * n + col].abs() {
                best = r;
            }
        }
        if a[best * n + col].abs() < 1e-12 {
            return None;
        }
        if best != col {
            for c in 0..n {
                a.swap(col * n + c, best * n + c);
            }
            b.swap(col, best);
        }
        let pivot = a[col * n + col];
        for r in (col + 1)..n {
            let f = a[r * n + col] / pivot;
            if f != 0.0 {
                for c in col..n {
                    a[r * n + c] -= f * a[col * n + c];
                }
                b[r] -= f * b[col];
            }
        }
    }
    // back-substitute
    let x = b; // reuse
    for r in (0..n).rev() {
        let mut s = x[r];
        for c in (r + 1)..n {
            s -= a[r * n + c] * x[c];
        }
        x[r] = s / a[r * n + r];
    }
    Some(())
}

/// Subtract best-fit plane z = a·x + b·y + c.
pub fn subtract_plane(data: &mut [f32], w: usize, h: usize) {
    if data.len() != w * h || w == 0 || h == 0 {
        return;
    }
    // normal equations for [x, y, 1]
    let mut ata = [0f64; 9];
    let mut atb = [0f64; 3];
    let mut n = 0usize;
    for r in 0..h {
        for c in 0..w {
            let z = data[r * w + c];
            if z.is_nan() {
                continue;
            }
            let x = c as f64;
            let y = r as f64;
            ata[0] += x * x;
            ata[1] += x * y;
            ata[2] += x;
            ata[4] += y * y;
            ata[5] += y;
            ata[8] += 1.0;
            atb[0] += x * z as f64;
            atb[1] += y * z as f64;
            atb[2] += z as f64;
            n += 1;
        }
    }
    ata[3] = ata[1];
    ata[6] = ata[2];
    ata[7] = ata[5];
    if n < 3 || solve_linear(&mut ata, &mut atb, 3).is_none() {
        return;
    }
    let (ca, cb, cc) = (atb[0], atb[1], atb[2]);
    for r in 0..h {
        for c in 0..w {
            let z = data[r * w + c];
            if z.is_nan() {
                continue;
            }
            let plane = (ca * c as f64 + cb * r as f64 + cc) as f32;
            data[r * w + c] = z - plane;
        }
    }
}

/// Subtract per-row mean (NaN-aware).
pub fn subtract_line_mean(data: &mut [f32], w: usize, h: usize) {
    if data.len() != w * h {
        return;
    }
    for r in 0..h {
        let row = &mut data[r * w..(r + 1) * w];
        let m = nanmean(row);
        if m.is_nan() {
            continue;
        }
        for v in row.iter_mut() {
            if !v.is_nan() {
                *v -= m;
            }
        }
    }
}

/// Subtract per-row median (NaN-aware).
pub fn subtract_line_median(data: &mut [f32], w: usize, h: usize) {
    if data.len() != w * h {
        return;
    }
    for r in 0..h {
        let mut vals: Vec<f32> = data[r * w..(r + 1) * w]
            .iter()
            .copied()
            .filter(|v| !v.is_nan())
            .collect();
        let m = nanmedian_slice(&mut vals);
        if m.is_nan() {
            continue;
        }
        for v in data[r * w..(r + 1) * w].iter_mut() {
            if !v.is_nan() {
                *v -= m;
            }
        }
    }
}

/// NaN-aware median of a slice (helper for line-median).
#[allow(dead_code)]
fn _nanmedian_alias(vals: &mut Vec<f32>) -> f32 {
    nanmedian_slice(vals)
}

/// Fit+subtract a per-row polynomial of the given degree.
pub fn subtract_line_polynomial(data: &mut [f32], w: usize, h: usize, degree: usize) {
    if data.len() != w * h || w == 0 {
        return;
    }
    let deg = degree.min(4);
    let ncols = w;
    // normalized x to [0,1] for conditioning (same fit space, affine transform)
    let denom = (ncols.saturating_sub(1)).max(1) as f64;
    let ncoef = deg + 1;
    for r in 0..h {
        // accumulate normal equations for this row
        let mut ata = vec![0f64; ncoef * ncoef];
        let mut atb = vec![0f64; ncoef];
        let mut n = 0usize;
        for c in 0..ncols {
            let z = data[r * ncols + c];
            if z.is_nan() {
                continue;
            }
            let x = c as f64 / denom as f64;
            // powers of x
            let mut p = [0f64; 8];
            p[0] = 1.0;
            for k in 1..ncoef {
                p[k] = p[k - 1] * x;
            }
            for i in 0..ncoef {
                for j in 0..ncoef {
                    ata[i * ncoef + j] += p[i] * p[j];
                }
                atb[i] += p[i] * z as f64;
            }
            n += 1;
        }
        if n <= deg || solve_linear(&mut ata, &mut atb, ncoef).is_none() {
            continue;
        }
        for c in 0..ncols {
            let z = data[r * ncols + c];
            if z.is_nan() {
                continue;
            }
            let x = c as f64 / denom as f64;
            let mut fit = 0f64;
            let mut pw = 1f64;
            for k in 0..ncoef {
                fit += atb[k] * pw;
                pw *= x;
            }
            data[r * ncols + c] = (z as f64 - fit) as f32;
        }
    }
}

/// Fit+subtract a 2D polynomial surface (default degree 2: [1,x,y,x²,xy,y²]).
pub fn subtract_polynomial_2d(data: &mut [f32], w: usize, h: usize, degree: usize) {
    if data.len() != w * h || w == 0 || h == 0 {
        return;
    }
    let deg = degree.min(3);
    let ncoef = (deg + 1) * (deg + 2) / 2;
    let denom_x = (w.saturating_sub(1)).max(1) as f64;
    let denom_y = (h.saturating_sub(1)).max(1) as f64;

    // term exponents in order: (i, j) with i+j <= deg, i = x-power, j = y-power
    let mut terms: Vec<(usize, usize)> = vec![];
    for i in 0..=deg {
        for j in 0..=(deg - i) {
            terms.push((i, j));
        }
    }
    debug_assert_eq!(terms.len(), ncoef);

    let mut ata = vec![0f64; ncoef * ncoef];
    let mut atb = vec![0f64; ncoef];
    let mut n = 0usize;
    for r in 0..h {
        for c in 0..w {
            let z = data[r * w + c];
            if z.is_nan() {
                continue;
            }
            let x = c as f64 / denom_x;
            let y = r as f64 / denom_y;
            let mut tv = vec![0f64; ncoef];
            for (k, (i, j)) in terms.iter().enumerate() {
                let mut xi = 1f64;
                for _ in 0..*i {
                    xi *= x;
                }
                let mut yj = 1f64;
                for _ in 0..*j {
                    yj *= y;
                }
                tv[k] = xi * yj;
            }
            for i in 0..ncoef {
                for j in 0..ncoef {
                    ata[i * ncoef + j] += tv[i] * tv[j];
                }
                atb[i] += tv[i] * z as f64;
            }
            n += 1;
        }
    }
    if n < ncoef || solve_linear(&mut ata, &mut atb, ncoef).is_none() {
        return;
    }
    for r in 0..h {
        for c in 0..w {
            let z = data[r * w + c];
            if z.is_nan() {
                continue;
            }
            let x = c as f64 / denom_x;
            let y = r as f64 / denom_y;
            let mut surface = 0f64;
            for (k, (i, j)) in terms.iter().enumerate() {
                let mut pw = 1f64;
                let mut t = 1f64;
                for _ in 0..*i {
                    t *= x;
                }
                for _ in 0..*j {
                    t *= y;
                }
                surface += atb[k] * t;
                let _ = pw;
            }
            data[r * w + c] = (z as f64 - surface) as f32;
        }
    }
}

/// Fourier high-pass with Gaussian roll-off (mirrors subtract_fourier_highpass).
pub fn subtract_fourier_highpass(data: &mut [f32], w: usize, h: usize, cutoff_fraction: f32) {
    if data.len() != w * h || w < 2 || h < 2 {
        return;
    }
    use rustfft::{num_complex::Complex, Fft, FftPlanner};

    let mean = nanmean(data);
    if mean.is_nan() {
        return;
    }
    let nan_mask: Vec<bool> = data.iter().map(|v| v.is_nan()).collect();
    for (v, m) in data.iter_mut().zip(&nan_mask) {
        if *m {
            *v = mean;
        }
    }

    let mut planner = FftPlanner::<f32>::new();
    let row_fft = planner.plan_fft_forward(w);
    let col_fft = planner.plan_fft_forward(h);
    let row_ifft = planner.plan_fft_inverse(w);
    let col_ifft = planner.plan_fft_inverse(h);

    let mut plane: Vec<Complex<f32>> = data.iter().map(|&v| Complex::new(v, 0.0)).collect();

    // forward: rows then cols (matches fft2 = 1D ffts along each axis)
    for r in 0..h {
        let mut buf: Vec<Complex<f32>> = plane[r * w..(r + 1) * w].to_vec();
        row_fft.process(&mut buf);
        plane[r * w..(r + 1) * w].copy_from_slice(&buf);
    }
    let mut colbuf = vec![Complex { re: 0.0, im: 0.0 }; h];
    for c in 0..w {
        for r in 0..h {
            colbuf[r] = plane[r * w + c];
        }
        col_fft.process(&mut colbuf);
        for r in 0..h {
            plane[r * w + c] = colbuf[r];
        }
    }

    // fftshift: out[i] = in[(i + n/2) % n]
    let hw = w / 2;
    let hh = h / 2;
    let mut shifted: Vec<Complex<f32>> = Vec::with_capacity(w * h);
    for r in 0..h {
        for c in 0..w {
            shifted.push(plane[((r + hh) % h) * w + ((c + hw) % w)]);
        }
    }

    // Gaussian high-pass
    let center_row = h / 2;
    let center_col = w / 2;
    let max_distance = ((center_row * center_row + center_col * center_col) as f32).sqrt();
    let sigma = (cutoff_fraction * max_distance).max(1e-6);
    let mut filtered = shifted;
    for r in 0..h {
        for c in 0..w {
            let dr = (r as f32 - center_row as f32).powi(2);
            let dc = (c as f32 - center_col as f32).powi(2);
            let dist2 = dr + dc;
            let hp = 1.0 - (-dist2 / (2.0 * sigma * sigma + 1e-10)).exp();
            filtered[r * w + c] *= hp;
        }
    }

    // ifftshift: out[i] = in[(i + n - n/2) % n]
    let irows = h - hh;
    let icols = w - hw;
    let mut plane2: Vec<Complex<f32>> = Vec::with_capacity(w * h);
    for r in 0..h {
        for c in 0..w {
            plane2.push(filtered[((r + irows) % h) * w + ((c + icols) % w)]);
        }
    }

    // inverse: cols then rows (reverse order of the forward pass)
    for c in 0..w {
        for r in 0..h {
            colbuf[r] = plane2[r * w + c];
        }
        col_ifft.process(&mut colbuf);
        for r in 0..h {
            plane2[r * w + c] = colbuf[r];
        }
    }
    for r in 0..h {
        let mut buf = plane2[r * w..(r + 1) * w].to_vec();
        row_ifft.process(&mut buf);
        plane2[r * w..(r + 1) * w].copy_from_slice(&buf);
    }

    let scale = 1.0 / (w * h) as f32;
    for (i, z) in plane2.iter().enumerate() {
        let mut v = z.re * scale;
        if nan_mask[i] {
            v = f32::NAN;
        }
        data[i] = v;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32, eps: f32) -> bool {
        (a - b).abs() <= eps * (1.0 + a.abs().max(b.abs()))
    }

    #[test]
    fn test_line_mean() {
        // 2 rows: [1,2,3] and [10,20,30]
        let mut d = vec![1.0, 2.0, 3.0, 10.0, 20.0, 30.0];
        subtract_line_mean(&mut d, 3, 2);
        assert!(approx_vec(&d, &[-1.0, 0.0, 1.0, -10.0, 0.0, 10.0]));
    }

    fn approx_vec(a: &[f32], b: &[f32]) -> bool {
        a.iter().zip(b).all(|(x, y)| approx(*x, *y, 1e-5))
    }

    #[test]
    fn test_line_median() {
        let mut d = vec![1.0, 2.0, 100.0, 5.0, 6.0, 7.0];
        subtract_line_median(&mut d, 3, 2);
        assert!(approx_vec(&d, &[-1.0, 0.0, 98.0, -1.0, 0.0, 1.0]));
    }

    #[test]
    fn test_plane() {
        // z = 2x + 3y + 1 exactly → plane subtraction zeroes it
        let w = 4usize;
        let h = 3usize;
        let mut d: Vec<f32> = (0..h)
            .flat_map(|r| (0..w).map(move |c| (2.0 * c as f32 + 3.0 * r as f32 + 1.0)))
            .collect();
        subtract_plane(&mut d, w, h);
        for v in d {
            assert!(v.abs() < 1e-3, "got {v}");
        }
    }

    #[test]
    fn test_poly2d() {
        // z = x² + 0.5xy - y (degree-2 polynomial) → fully removed
        let w = 5usize;
        let h = 4usize;
        let mut d: Vec<f32> = (0..h)
            .flat_map(|r| {
                (0..w).map(move |c| {
                    let x = c as f32;
                    let y = r as f32;
                    x * x + 0.5 * x * y - y
                })
            })
            .collect();
        subtract_polynomial_2d(&mut d, w, h, 2);
        for v in d {
            assert!(v.abs() < 1e-3, "got {v}");
        }
    }

    #[test]
    fn test_row_poly() {
        // each row is exactly linear in x → removed with degree 1
        let w = 5usize;
        let h = 2usize;
        let mut d: Vec<f32> = (0..h)
            .flat_map(|r| (0..w).map(move |c| (0.5 * c as f32 + r as f32 * 7.0)))
            .collect();
        subtract_line_polynomial(&mut d, w, h, 1);
        for v in d {
            assert!(v.abs() < 1e-4, "got {v}");
        }
    }

    #[test]
    fn test_fourier_preserves_high_freq() {
        // pure high-frequency checkerboard should be (nearly) unchanged
        let w = 16usize;
        let h = 16usize;
        let mut d: Vec<f32> = (0..h * w)
            .map(|i| if (i / w + i % w) % 2 == 0 { 1.0 } else { -1.0 })
            .collect();
        let before = d.clone();
        subtract_fourier_highpass(&mut d, w, h, 0.05);
        let maxdiff = max_diff(&before, &d);
        assert!(maxdiff < 1e-3, "max err {maxdiff}");
    }

    fn max_diff(a: &[f32], b: &[f32]) -> f32 {
        a.iter()
            .zip(b)
            .map(|(x, y)| (x - y).abs())
            .fold(0.0f32, f32::max)
    }

    #[test]
    fn test_percentile() {
        let d: Vec<f32> = (1..=100).map(|i| i as f32).collect();
        assert!((nanpercentile(&d, 1.0) - 1.99).abs() < 1e-4);
        assert!((nanpercentile(&d, 99.0) - 99.01).abs() < 1e-4);
        assert!((nanpercentile(&d, 50.0) - 50.5).abs() < 1e-4);
    }
}