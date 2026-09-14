//! egui desktop app — native port of the Parkviewer Streamlit UI.
//!
//! Sidebar (folder, filters, display, color scale, background subtraction)
//! + three views: Single File / All Channels / Gallery.

use crate::backgrounds::{self, BgMethod, BgParams};
use crate::colormaps::{data_to_rgb, Colormap};
use crate::core::{self, ParkFileMeta};
use anyhow::Result;
use eframe::egui;
use egui::{Color32, RichText, Sense, TextureHandle, Vec2};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

const CMAP_NAMES: [&str; 10] = [
    "viridis", "plasma", "magma", "inferno", "turbo", "terrain", "hsv", "twilight", "RdBu_r",
    "gray",
];

// ---------------------------------------------------------------- state

#[derive(Default, serde::Serialize, serde::Deserialize)]
struct Persisted {
    folder: Option<String>,
    gwyddion: Option<String>,
}

#[derive(Clone, Copy, Default, PartialEq)]
enum View {
    #[default]
    Single,
    AllChannels,
    Gallery,
}

#[derive(Hash, PartialEq, Eq, Clone)]
struct TexKey {
    path: PathBuf,
    bg: BgMethod,
    degree: usize,
    cutoff_bits: u32,
    cmap: String,
    vmin_bits: u32,
    vmax_bits: u32,
}

pub struct ParkViewerApp {
    folder: Option<PathBuf>,
    files: Vec<ParkFileMeta>,
    filtered: Vec<usize>,

    sel_channels: Vec<String>,
    sel_directions: Vec<String>,
    sel_experiments: Vec<String>,
    available: (Vec<String>, Vec<String>, Vec<String>),

    auto_colormap: bool,
    fallback_cmap_name: String,
    color_scale_auto: bool,
    percentile_low: f32,
    percentile_high: f32,
    manual_scale_factor: f32,
    manual_center_offset: f32,

    default_bg: BgMethod,
    per_channel: bool,
    per_channel_bg: HashMap<String, BgMethod>,
    bg_params: BgParams,

    hide_incomplete: bool,
    incomplete_hidden: usize,

    view: View,
    selected: Option<usize>,
    frame_sel: usize,

    tex_cache: HashMap<TexKey, TextureHandle>,
    data_cache: HashMap<PathBuf, Arc<Vec<f32>>>,

    status: String,
    status_good: bool,
    gwyddion: Option<PathBuf>,
}

// ---------------------------------------------------------------- entry

pub fn run_native() -> Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1400.0, 900.0])
            .with_min_inner_size([900.0, 600.0])
            .with_title("Park AFM TIFF Viewer")
            .with_icon(load_icon()),
        ..Default::default()
    };
    eframe::run_native(
        "Park AFM TIFF Viewer",
        options,
        Box::new(|cc| Ok(Box::new(ParkViewerApp::new(cc)))),
    )
    .map_err(|e| anyhow::anyhow!("eframe: {e}"))
}

fn load_icon() -> egui::IconData {
    let icon_path = std::path::PathBuf::from(env!("PARKVIEWER_ICON_PATH"));
    let img = image::open(&icon_path)
        .unwrap_or_else(|e| panic!("bundled icon missing at {}: {e}", icon_path.display()));
    let rgba = img.to_rgba8();
    egui::IconData {
        width: rgba.width(),
        height: rgba.height(),
        rgba: rgba.into_raw(),
    }
}

impl ParkViewerApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let persisted: Persisted = cc
            .storage
            .and_then(|s| s.get_string("parkviewer"))
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();

        let folder = persisted.folder.map(PathBuf::from);
        let gwyddion = persisted
            .gwyddion
            .map(PathBuf::from)
            .or_else(|| core::find_gwyddion_executable());

        let mut app = ParkViewerApp {
            folder: folder.clone(),
            files: vec![],
            filtered: vec![],
            available: (vec![], vec![], vec![]),
            sel_channels: vec![],
            sel_directions: vec![],
            sel_experiments: vec![],
            auto_colormap: true,
            fallback_cmap_name: "viridis".to_string(),
            color_scale_auto: true,
            percentile_low: 1.0,
            percentile_high: 99.0,
            manual_scale_factor: 1.0,
            manual_center_offset: 0.0,
            default_bg: BgMethod::None,
            per_channel: false,
            per_channel_bg: HashMap::new(),
            bg_params: BgParams::default(),
            hide_incomplete: false,
            incomplete_hidden: 0,
            view: View::Single,
            selected: None,
            frame_sel: 0,
            tex_cache: HashMap::new(),
            data_cache: HashMap::new(),
            status: "Open a folder of Park AFM TIFF files to begin.".into(),
            status_good: true,
            gwyddion,
        };
        if let Some(f) = folder {
            app.load_folder(&f);
        }
        app
    }

    // ------------------------------------------------------------ data

    fn load_folder(&mut self, path: &Path) {
        self.files = core::get_tiff_files(path);
        let (ch, di, ex) = core::get_unique_values(&self.files);
        self.available = (ch, di, ex);
        self.sel_channels = self.available.0.clone();
        self.sel_directions = self.available.1.clone();
        self.sel_experiments = self.available.2.clone();
        self.selected = None;
        self.tex_cache.clear();
        self.data_cache.clear();
        self.apply_filters();
        self.set_status(
            true,
            format!("Loaded {} TIFF files", self.files.len()),
        );
    }

    fn apply_filters(&mut self) {
        let files = core::filter_files(
            &self.files,
            Some(&self.sel_channels),
            Some(&self.sel_directions),
            Some(&self.sel_experiments),
        );
        self.filtered = files
            .iter()
            .filter_map(|f| self.files.iter().position(|g| g.path == f.path))
            .collect();
        self.incomplete_hidden = 0;
        if self.hide_incomplete {
            let mut kept = vec![];
            let indices = self.filtered.clone();
            for &i in &indices {
                let (ok, _) = self.completeness_of(i);
                if ok {
                    kept.push(i);
                } else {
                    self.incomplete_hidden += 1;
                }
            }
            self.filtered = kept;
        }
        if !self.filtered.is_empty() && self.selected.is_none() {
            self.selected = Some(0);
        }
        if self.selected.map_or(false, |s| s >= self.filtered.len()) {
            self.selected = self.filtered.first().copied();
        }
    }

    fn completeness_of(&mut self, i: usize) -> (bool, f32) {
        let path = self.files[i].path.clone();
        match self.load_raw(&path) {
            Some((w, h, data)) => core::check_image_complete(&data, w, h, 0.1),
            None => (false, 0.0),
        }
    }

    fn load_raw(&mut self, path: &Path) -> Option<(usize, usize, Arc<Vec<f32>>)> {
        if let Some(d) = self.data_cache.get(path) {
            let (w, h) = image::image_dimensions(path).ok()?;
            return Some((w as usize, h as usize, d.clone()));
        }
        let (w0, h0) = image::image_dimensions(path).ok()?;
        let n = w0 as usize * h0 as usize;
        let data = match core::load_tiff_f32(path) {
            Ok(v) if v.len() == n => v,
            _ => vec![f32::NAN; n],
        };
        let arc = Arc::new(data);
        self.data_cache.insert(path.to_path_buf(), arc.clone());
        Some((w0 as usize, h0 as usize, arc))
    }

    fn bg_for(&self, channel: Option<&str>) -> BgMethod {
        if self.per_channel {
            if let Some(c) = channel {
                if let Some(m) = self.per_channel_bg.get(c) {
                    return *m;
                }
            }
        }
        self.default_bg
    }

    fn cmap_for(&self, channel: Option<&str>) -> Colormap {
        let name = if self.auto_colormap && channel.is_some() {
            core::colormap_for_channel(channel).to_string()
        } else {
            self.fallback_cmap_name.clone()
        };
        Colormap::get(&name)
    }

    fn display_range(&self, data: &[f32]) -> (f32, f32) {
        if data.is_empty() {
            return (0.0, 1.0);
        }
        if self.color_scale_auto {
            (
                backgrounds::nanpercentile(data, self.percentile_low),
                backgrounds::nanpercentile(data, self.percentile_high),
            )
        } else {
            let lo = backgrounds::nanpercentile(data, 1.0);
            let hi = backgrounds::nanpercentile(data, 99.0);
            let range = hi - lo;
            let mut center = (lo + hi) / 2.0;
            center += self.manual_center_offset * range;
            let half = (range / 2.0) * self.manual_scale_factor;
            (center - half, center + half)
        }
    }

    fn processed(&mut self, i: usize) -> Option<(usize, usize, Vec<f32>, BgMethod)> {
        let path = self.files[i].path.clone();
        let (w, h, raw) = self.load_raw(&path)?;
        let bg = self.bg_for(self.files[i].channel.as_deref());
        let mut data = (*raw).clone();
        backgrounds::apply(&mut data, w, h, bg, &self.bg_params);
        Some((w, h, data, bg))
    }

    fn texture_for(&mut self, ctx: &egui::Context, i: usize) -> Option<TextureHandle> {
        let path = self.files[i].path.clone();
        let channel = self.files[i].channel.clone();
        let fname = self.files[i].filename.clone();
        let (w, h, data, bg) = self.processed(i)?;
        let (vmin, vmax) = self.display_range(&data);
        let cmap = self.cmap_for(channel.as_deref());
        let key = TexKey {
            path,
            bg,
            degree: self.bg_params.degree,
            cutoff_bits: self.bg_params.cutoff_fraction.to_bits(),
            cmap: cmap.name.clone(),
            vmin_bits: vmin.to_bits(),
            vmax_bits: vmax.to_bits(),
        };
        if let Some(t) = self.tex_cache.get(&key) {
            return Some(t.clone());
        }
        let rgb = data_to_rgb(&data, w, h, &cmap, vmin, vmax);
        let tex = ctx.load_texture(
            format!("{fname}|{}|{bg:?}", cmap.name),
            egui::ColorImage::from_rgb([w, h], &rgb),
            egui::TextureOptions::NEAREST,
        );
        self.tex_cache.insert(key, tex.clone());
        Some(tex)
    }

    fn set_status(&mut self, good: bool, msg: impl Into<String>) {
        self.status_good = good;
        self.status = msg.into();
    }

    fn try_open_gwyddion(&mut self, path: &Path) {
        let gwy = self.gwyddion.clone();
        match core::open_in_gwyddion(path, gwy.as_deref()) {
            Ok(msg) => self.set_status(true, msg),
            Err(e) => self.set_status(false, e.to_string()),
        }
    }

    fn copy_path(&mut self, path: &Path) {
        match core::copy_to_clipboard(&path.display().to_string()) {
            Ok(_) => self.set_status(true, format!("Copied path — {}", path.display())),
            Err(e) => self.set_status(false, format!("Clipboard failed: {e}")),
        }
    }

    // ------------------------------------------------------------ sidebar

    fn sidebar(&mut self, ui: &mut egui::Ui) {
        egui::ScrollArea::vertical().show(ui, |ui| {
            ui.add_space(4.0);
            ui.heading("📁 Folder");
            ui.horizontal(|ui| {
                if ui.button("📂 Open Folder…").clicked() {
                    if let Some(dir) = rfd::FileDialog::new().pick_folder() {
                        self.folder = Some(dir.clone());
                        self.load_folder(&dir);
                    }
                }
            });
            if let Some(f) = &self.folder {
                ui.monospace(RichText::new(f.display().to_string()).size(10.0))
                    .on_hover_text(f.display().to_string());
            }

            ui.separator();
            ui.heading("🔍 Filters");
            ui.label(RichText::new("Empty selection = show all").weak());

            let (chs, dirs, exps) = self.available.clone();
            multi_select(ui, "Channels", &chs, &mut self.sel_channels);
            multi_select(ui, "Directions", &dirs, &mut self.sel_directions);
            multi_select(ui, "Experiments", &exps, &mut self.sel_experiments);

            let mut hide = self.hide_incomplete;
            if ui.checkbox(&mut hide, "Hide incomplete images").changed() {
                self.hide_incomplete = hide;
                self.apply_filters();
            }
            ui.label(
                RichText::new(format!(
                    "Showing {} of {} files",
                    self.filtered.len(),
                    self.files.len()
                ))
                .weak(),
            );

            ui.separator();
            ui.heading("🎨 Display");
            ui.checkbox(&mut self.auto_colormap, "Auto colormap per channel");
            egui::ComboBox::from_id_salt("fallback_cmap")
                .selected_text(self.fallback_cmap_name.clone())
                .show_ui(ui, |ui| {
                    for c in CMAP_NAMES {
                        ui.selectable_value(&mut self.fallback_cmap_name, c.to_string(), c);
                    }
                });

            ui.separator();
            ui.heading("🌈 Color Scale");
            ui.radio_value(&mut self.color_scale_auto, true, "Auto (Percentile)");
            ui.radio_value(&mut self.color_scale_auto, false, "Manual");
            if self.color_scale_auto {
                ui.add(
                    egui::Slider::new(&mut self.percentile_low, 0.0..=50.0)
                        .text("Low percentile"),
                );
                ui.add(
                    egui::Slider::new(&mut self.percentile_high, 50.0..=100.0)
                        .text("High percentile"),
                );
            } else {
                ui.add(
                    egui::Slider::new(&mut self.manual_scale_factor, 0.1..=4.0)
                        .text("Scale factor"),
                );
                ui.add(
                    egui::Slider::new(&mut self.manual_center_offset, -0.5..=0.5)
                        .text("Center offset"),
                );
            }

            ui.separator();
            ui.heading("🔧 Background Subtraction");
            egui::ComboBox::from_id_salt("default_bg")
                .selected_text(self.default_bg.label())
                .show_ui(ui, |ui| {
                    for (m, label) in BgMethod::ALL {
                        ui.selectable_value(&mut self.default_bg, m, label);
                    }
                });
            ui.checkbox(&mut self.per_channel, "Use per-channel settings");
            if self.per_channel {
                let channels = self.available.0.clone();
                for c in &channels {
                    let mut cur = self
                        .per_channel_bg
                        .get(c)
                        .copied()
                        .unwrap_or(BgMethod::None);
                    egui::ComboBox::from_id_salt(format!("bg_{c}"))
                        .selected_text(cur.label())
                        .show_ui(ui, |ui| {
                            for (m, label) in BgMethod::ALL {
                                ui.selectable_value(&mut cur, m, label);
                            }
                        });
                    self.per_channel_bg.insert(c.clone(), cur);
                }
            }
            ui.horizontal(|ui| {
                ui.label("Poly degree:");
                let mut deg = self.bg_params.degree;
                egui::ComboBox::from_id_salt("poly_degree")
                    .selected_text(format!("{deg}"))
                    .show_ui(ui, |ui| {
                        for d in [1usize, 2, 3, 4] {
                            ui.selectable_value(&mut deg, d, format!("{d}"));
                        }
                    });
                self.bg_params.degree = deg;
                ui.label("Fourier cutoff:");
                ui.add(
                    egui::DragValue::new(&mut self.bg_params.cutoff_fraction)
                        .speed(0.005)
                        .range(0.005..=0.5),
                );
            });

            ui.separator();
            ui.heading("🔬 Gwyddion");
            ui.horizontal(|ui| {
                if ui.button("Locate…").clicked() {
                    if let Some(p) = rfd::FileDialog::new().pick_file() {
                        self.gwyddion = Some(p);
                    }
                }
                match &self.gwyddion {
                    Some(g) => {
                        let name = g
                            .file_name()
                            .and_then(|n| n.to_str())
                            .unwrap_or("?");
                        ui.monospace(RichText::new(name).size(10.0));
                    }
                    None => {
                        ui.label(RichText::new("not found").weak());
                    }
                }
            });
        });
    }

    // ------------------------------------------------------------ single view

    fn tab_single(&mut self, ui: &mut egui::Ui) {
        if self.filtered.is_empty() {
            ui.centered_and_justified(|ui| {
                ui.label(RichText::new("No files match the current filters.").weak());
            });
            return;
        }
        let path_for_panel = self.files[self.selected.unwrap_or(0)].path.clone();
        let meta = self.files[self.selected.unwrap_or(0)].tiff_meta.clone();

        egui::Panel::right("single_right")
            .resizable(true)
            .default_size(330.0)
            .show_inside(ui, |ui| {
                ui.heading("📄 File Selection");
                ui.add_space(4.0);

                // build labels without borrowing self mutably twice
                let labels: Vec<String> = self
                    .filtered
                    .iter()
                    .enumerate()
                    .map(|(n, &i)| format!("{}. {}", n + 1, self.files[i].full_name))
                    .collect();
                let cur_name = {
                    let i = self.selected.unwrap_or(0);
                    self.files[i].full_name.clone()
                };
                egui::ComboBox::from_id_salt("file_select")
                    .selected_text(cur_name)
                    .show_ui(ui, |ui| {
                        for (n, label) in labels.iter().enumerate() {
                            let idx = self.filtered[n];
                            ui.selectable_value(&mut self.selected, Some(idx), label);
                        }
                    });

                ui.add_space(8.0);
                ui.horizontal(|ui| {
                    if ui.button("⬅ Prev").clicked() {
                        let cur = self.selected.unwrap_or(0);
                        self.selected = Some(cur.saturating_sub(1).min(self.filtered.len() - 1));
                    }
                    if ui.button("Next ➡").clicked() {
                        let cur = self.selected.unwrap_or(0);
                        self.selected = Some((cur + 1).min(self.filtered.len() - 1));
                    }
                });

                let f = &self.files[self.selected.unwrap_or(0)];
                ui.add_space(8.0);
                ui.label(RichText::new(&f.full_name).strong());
                ui.label(format!(
                    "Channel: {}   Direction: {}",
                    f.channel.as_deref().unwrap_or("—"),
                    f.direction.as_deref().unwrap_or("—")
                ));
                ui.label(format!(
                    "Frame: {}",
                    f.frame.map_or("?".to_string(), |v| format!("{v:03}"))
                ));

                ui.add_space(4.0);
                let path = f.path.clone();
                ui.horizontal(|ui| {
                    if ui.button("📋 Copy path").clicked() {
                        self.copy_path(&path);
                    }
                    if ui.button("🔬 Open in Gwyddion").clicked() {
                        self.try_open_gwyddion(&path);
                    }
                });

                if let Some(m) = &meta {
                    ui.separator();
                    ui.heading("📊 TIFF Metadata");
                    egui::Grid::new("meta").num_columns(2).show(ui, |ui| {
                        ui.label("Channel:");
                        ui.label(m.channel.as_deref().unwrap_or("—"));
                        ui.end_row();
                        ui.label("Scan size:");
                        ui.label(
                            m.scan_size_um
                                .map(|v| format!("{v:.2} µm"))
                                .unwrap_or_else(|| "—".into()),
                        );
                        ui.end_row();
                        ui.label("Scan rate:");
                        ui.label(
                            m.scan_rate_hz
                                .map(|v| format!("{v:.1} Hz"))
                                .unwrap_or_else(|| "—".into()),
                        );
                        ui.end_row();
                        ui.label("Z scale:");
                        ui.label(
                            m.z_scale
                                .map(|v| format!("{v:.3}"))
                                .unwrap_or_else(|| "—".into()),
                        );
                        ui.end_row();
                        ui.label("Date:");
                        ui.label(m.datetime.as_deref().unwrap_or("—"));
                        ui.end_row();
                        ui.label("Software:");
                        ui.label(m.software.as_deref().unwrap_or("—"));
                        ui.end_row();
                        ui.label("Size:");
                        ui.label(format!("{}×{}", m.width, m.height));
                        ui.end_row();
                    });
                }
            });

        egui::ScrollArea::both().show(ui, |ui| {
            ui.heading("🖼️ Image Preview");
            let i = match self
                .filtered
                .iter()
                .position(|&p| self.files[p].path == path_for_panel)
            {
                Some(p) => self.filtered[p],
                None => return,
            };
            let f = &self.files[i];
            let bg = self.bg_for(f.channel.as_deref());
            if bg != BgMethod::None {
                ui.label(RichText::new(format!("🔧 Background: {}", bg.label())).small());
            }

            // stats row
            let path2 = f.path.clone();
            if let Some((w, h, raw)) = self.load_raw(&path2) {
                let mut data = (*raw).clone();
                backgrounds::apply(&mut data, w, h, bg, &self.bg_params);
                let mn = backgrounds::nanmin(&data);
                let mx = backgrounds::nanmax(&data);
                let mean = backgrounds::nanmean(&data);
                let (vmin, vmax) = self.display_range(&data);
                ui.label(
                    RichText::new(format!(
                        "Shape {w}×{h}   Min {}   Max {}   Mean {}   🌈 {}–{}", g3(mn), g3(mx), g3(mean), g3(vmin), g3(vmax)
                    ))
                    .small()
                    .weak(),
                );
            }

            ui.add_space(6.0);
            let tex = self.texture_for(ui.ctx(), i);
            match tex {
                Some(tex) => {
                    // scale to fit remaining space, keeping aspect
                    let avail = ui.available_size_before_wrap();
                    let ts = egui::vec2(tex.size()[0] as f32, tex.size()[1] as f32);
                    let scale = (avail.x / ts.x).min(avail.y / ts.y).min(1.0).max(0.05);
                    let size = ts * scale;
                    let (rect, _) = ui.allocate_exact_size(size, Sense::hover());
                    ui.painter().image(
                        tex.id(),
                        rect,
                        egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                        Color32::WHITE,
                    );
                }
                None => {
                    ui.colored_label(Color32::RED, "Failed to load image");
                }
            }
        });
    }

    // ------------------------------------------------------------ all channels

    fn tab_all_channels(&mut self, ui: &mut egui::Ui) {
        ui.heading("📊 All Channels for Selected Frame");
        let frames = core::get_frames_info(&self.files);
        if frames.is_empty() {
            ui.label("No frames with valid metadata found.");
            return;
        }
        self.frame_sel = self.frame_sel.min(frames.len() - 1);
        let (exp, frame) = frames[self.frame_sel].clone();

        ui.horizontal(|ui| {
            let mut sel = self.frame_sel;
            egui::ComboBox::from_id_salt("frame_select_all")
                .selected_text(format!("{exp} - Frame {frame:03}"))
                .show_ui(ui, |ui| {
                    for (n, (e, f)) in frames.iter().enumerate() {
                        ui.selectable_value(&mut sel, n, format!("{e} - Frame {f:03}"));
                    }
                });
            self.frame_sel = sel;
        });

        let chans = core::get_all_channels_for_frame(&self.files, &exp, frame, None);
        if chans.is_empty() {
            ui.label("No channels found for this frame.");
            return;
        }
        ui.label(RichText::new(format!("Found {} channels", chans.len())).weak());
        ui.label(RichText::new("Click a thumbnail to copy its file path to the clipboard.").weak());

        let cell = 200.0;
        let cap_h = 34.0;
        let cols = ((ui.available_width() / (cell + 10.0)).floor() as usize).max(1);

        egui::ScrollArea::vertical().show(ui, |ui| {
            // phase 1: completeness pass (mutable), collected without closures
            let indices: Vec<usize> = chans
                .iter()
                .filter_map(|f| self.files.iter().position(|g| g.path == f.path))
                .collect();
            let mut infos: Vec<(usize, String, bool, f32, String)> = vec![];
            for &i in &indices {
                let (channel, direction) = {
                    let f = &self.files[i];
                    (
                        f.channel.clone().unwrap_or_else(|| "Data".into()),
                        f.direction.clone().unwrap_or_default(),
                    )
                };
                let ch = channel.clone();
                let dir = direction;
                let (ok, pct) = self.completeness_of(i);
                let bgm = self.bg_for(Some(ch.as_str()));
                let bg_label = if bgm != BgMethod::None {
                    format!(" [{}]", bgm.label())
                } else {
                    String::new()
                };
                let status = if ok { "✅".to_string() } else { format!("⚠️{pct:.0}%") };
                let cap = format!("{ch} · {dir} {status}{bg_label}");
                infos.push((i, ch, ok, pct, cap));
            }

            for (row, chunk) in infos.chunks(cols).enumerate() {
                let _ = row;
                for (i, _ch, _ok, _pct, cap) in chunk {
                    let path = self.files[*i].path.clone();
                    ui.vertical(|ui| {
                        let tex = self.texture_for(ui.ctx(), *i);
                        match tex {
                            Some(t) => {
                                let resp = ui
                                    .add(
                                        egui::Image::from_texture(egui::load::SizedTexture::from_handle(&t))
                                            .fit_to_exact_size(Vec2::new(cell, cell))
                                            .sense(Sense::click()),
                                    );
                                if resp.clicked() {
                                    self.copy_path(&path);
                                }
                            }
                            None => {
                                ui.label("⚠️ load failed");
                            }
                        }
                        ui.label(RichText::new(cap.clone()).small().weak());
                    });
                }
            }
        });
    }

    // ------------------------------------------------------------ gallery

    fn tab_gallery(&mut self, ui: &mut egui::Ui) {
        ui.heading("🖼️ Gallery");
        ui.label(RichText::new("Click any image to open it in the Single File view (path is copied).").weak());

        // frames from filtered files
        let filtered_clone: Vec<ParkFileMeta> = self
            .filtered
            .iter()
            .map(|&i| self.files[i].clone())
            .collect();
        let frames = core::get_frames_info(&filtered_clone);
        if frames.is_empty() {
            ui.label("No frames to show.");
            return;
        }

        let cell = 170.0;
        let cols = ((ui.available_width() / (cell + 10.0)).floor() as usize).max(1);
        egui::ScrollArea::vertical().show(ui, |ui| {
            for (exp, frame) in frames.iter().take(12) {
                ui.add_space(4.0);
                ui.strong(format!("{exp} - Frame {frame:03}"));
                let chans = core::get_all_channels_for_frame(&self.files, exp, *frame, None);
                let entries: Vec<usize> = chans
                    .iter()
                    .filter_map(|f| self.files.iter().position(|g| g.path == f.path))
                    .filter(|&i| self.filtered.contains(&i))
                    .collect();
                if entries.is_empty() {
                    continue;
                }
                for chunk in entries.chunks(cols) {
                    ui.horizontal(|ui| {
                        for &i in chunk {
                            let path = self.files[i].path.clone();
                            let f = &self.files[i];
                            let label = format!(
                                "{} · {}",
                                f.channel.as_deref().unwrap_or("Data"),
                                f.direction.as_deref().unwrap_or("—")
                            );
                            ui.vertical(|ui| {
                                if let Some(tex) = self.texture_for(ui.ctx(), i) {
                                    let resp = ui
                                        .add(
                                            egui::Image::from_texture(egui::load::SizedTexture::from_handle(&tex))
                                                .fit_to_exact_size(Vec2::splat(cell))
                                                .sense(Sense::click()),
                                        );
                                    if resp.clicked() {
                                        if let Some(p) =
                                            self.filtered.iter().position(|&x| x == i)
                                        {
                                            self.selected = Some(p);
                                            self.view = View::Single;
                                        }
                                        self.copy_path(&path);
                                    }
                                    ui.label(RichText::new(label).small().weak());
                                }
                            });
                        }
                    });
                }
                ui.separator();
            }
        });
    }
}

// ---------------------------------------------------------------- helpers

/// 3-significant-digit formatting (mirrors Python's `:.3g`).
fn g3(v: f32) -> String {
    if v == 0.0 {
        return "0".into();
    }
    if v.is_nan() {
        return "NaN".into();
    }
    let a = v.abs();
    if a >= 1e6 || a < 1e-4 {
        return format!("{v:.3e}");
    }
    let digits = 3usize.saturating_sub((a.log10().floor() as i32 + 1).max(0) as usize);
    format!("{v:.*}", digits)
}

fn multi_select(ui: &mut egui::Ui, label: &str, available: &[String], selected: &mut Vec<String>) {
    ui.label(RichText::new(label).strong());
    egui::ScrollArea::horizontal()
        .max_height(90.0)
        .show(ui, |ui| {
            ui.horizontal_wrapped(|ui| {
                for item in available {
                    let mut on = selected.contains(item);
                    if ui.toggle_value(&mut on, item).changed() {
                        if on {
                            if !selected.contains(item) {
                                selected.push(item.clone());
                            }
                        } else {
                            selected.retain(|s| s != item);
                        }
                    }
                }
                if ui
                    .add(egui::Button::new(RichText::new("all/none").small()))
                    .clicked()
                {
                    if selected.len() == available.len() {
                        selected.clear();
                    } else {
                        *selected = available.to_vec();
                    }
                }
            });
        });
}

// ---------------------------------------------------------------- eframe glue

impl eframe::App for ParkViewerApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        egui::Panel::left("sidebar")
            .resizable(true)
            .min_size(260.0)
            .default_size(320.0)
            .show(ui, |ui| self.sidebar(ui));

        egui::Panel::bottom("status")
            .exact_size(26.0)
            .show(ui, |ui| {
                ui.horizontal_centered(|ui| {
                    let color = if self.status_good {
                        Color32::from_rgb(110, 190, 110)
                    } else {
                        Color32::from_rgb(225, 120, 100)
                    };
                    ui.colored_label(color, RichText::new(&self.status).small());
                });
            });

        egui::Panel::top("tabs")
            .exact_size(34.0)
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.selectable_value(&mut self.view, View::Single, "📄 Single File");
                    ui.selectable_value(&mut self.view, View::AllChannels, "📊 All Channels");
                    ui.selectable_value(&mut self.view, View::Gallery, "🖼️ Gallery");
                });
            });

        egui::CentralPanel::default().show_inside(ui, |ui| match self.view {
            View::Single => self.tab_single(ui),
            View::AllChannels => self.tab_all_channels(ui),
            View::Gallery => self.tab_gallery(ui),
        });
    }

    fn save(&mut self, storage: &mut dyn eframe::Storage) {
        let p = Persisted {
            folder: self.folder.as_ref().map(|p| p.display().to_string()),
            gwyddion: self.gwyddion.as_ref().map(|p| p.display().to_string()),
        };
        if let Ok(s) = serde_json::to_string(&p) {
            storage.set_string("parkviewer", s);
        }
    }
}