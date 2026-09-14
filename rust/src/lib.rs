//! ParkViewer — native Rust port of the Park AFM TIFF viewer.

pub mod app;
pub mod backgrounds;
pub mod colormaps;
pub mod core;
pub mod metadata;

pub use app::run_native;