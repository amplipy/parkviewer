//! Build script: locate the repo-root icon regardless of checkout depth.
use std::path::PathBuf;

fn main() {
    // CARGO_MANIFEST_DIR = <repo>/rust
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let root = manifest.parent().expect("manifest has parent");
    let icon = root.join("packaging/assets/icon_256.png");
    println!("cargo:rustc-env=PARKVIEWER_ICON_PATH={}", icon.display());
    println!("cargo:rerun-if-changed={}", icon.display());
}
