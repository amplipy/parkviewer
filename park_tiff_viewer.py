"""
Park AFM TIFF Viewer
====================
A Streamlit app for browsing and previewing Park AFM TIFF data files.

Run with: streamlit run park_tiff_viewer.py

Author: NanosparQ Training
"""

import io
import os

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st
from streamlit_image_select import image_select

# Import core logic from separate module
from park_tiff_core import (
    BACKGROUND_METHODS,
    DEFAULT_FOLDER,
    KNOWN_CHANNELS,
    apply_background_subtraction,
    check_file_complete,
    copy_to_clipboard,
    export_to_powerpoint,
    extract_tiff_metadata,
    filter_files,
    find_gwyddion_executable,
    get_all_channels_for_frame,
    get_colormap_for_channel,
    get_display_range,
    get_frames_info,
    get_image_stats,
    get_scan_size_from_file,
    get_tiff_files,
    get_unique_values,
    load_tiff,
    open_in_gwyddion,
)


# ============================================================
# Streamlit-specific wrappers with caching
# ============================================================
@st.cache_data
def cached_check_file_complete(filepath, threshold_ratio=0.1):
    """Cached wrapper for check_file_complete."""
    return check_file_complete(filepath, threshold_ratio)


@st.cache_data
def cached_load_and_process(filepath, bg_method, degree, cutoff_fraction):
    """
    Load a TIFF and apply background subtraction, cached on every input
    that affects the result. Background subtraction (poly fits, Fourier
    filtering) is the expensive step, and without this it gets redone for
    every visible thumbnail on *every* rerun, since Streamlit re-executes
    all tabs/expanders (not just the visible one) each interaction.
    """
    data = load_tiff(filepath)
    if data is None:
        return None
    if bg_method != "None":
        data = apply_background_subtraction(
            data, bg_method, degree=degree, cutoff_fraction=cutoff_fraction
        )
    return data


@st.cache_data
def render_thumbnail_png(
    filepath, bg_method, degree, cutoff_fraction, cmap, vmin, vmax, figsize=(2.5, 2.5)
):
    """
    Render a small gallery thumbnail to PNG bytes, cached on everything
    that affects its appearance. Repeated reruns (e.g. after clicking a
    different thumbnail) hit this cache instead of re-running matplotlib
    for every cell in the grid.
    """
    data = cached_load_and_process(filepath, bg_method, degree, cutoff_fraction)
    if data is None:
        return None
    fig, ax = plt.subplots(figsize=figsize)
    ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
    ax.axis("off")
    plt.tight_layout(pad=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()


@st.cache_data
def render_thumbnail_array(filepath, bg_method, degree, cutoff_fraction, cmap, vmin, vmax):
    """
    Map data straight to an RGB uint8 array via the colormap, skipping
    matplotlib's Figure/Axes machinery entirely. Cheaper than rendering a
    full pyplot figure per thumbnail, and it's the format image_select
    (used for click-to-select gallery thumbnails) expects.
    """
    data = cached_load_and_process(filepath, bg_method, degree, cutoff_fraction)
    if data is None:
        return None
    norm = mpl.colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = mpl.colormaps[cmap](norm(data))
    return (rgba[..., :3] * 255).astype(np.uint8)


# ============================================================
# Gallery <-> Single File tab sync
# ============================================================
def file_label(idx, file_info):
    """Dropdown label for the file at a given index in filtered_files."""
    return f"{idx + 1}. {file_info['full_name']}"


def select_file_from_gallery(file_info, filtered_files):
    """
    Point the Single File tab at this file and copy its path to the
    clipboard. Safe to call either from a plain st.button on_click, or
    (as with the image_select gallery rows) from regular top-of-script
    code that runs before Tab1 renders — either way no st.rerun() is
    needed here, since the caller controls when/whether one happens.
    """
    idx = next(
        (i for i, f in enumerate(filtered_files) if f["path"] == file_info["path"]),
        None,
    )
    if idx is not None:
        st.session_state.file_idx = idx
        st.session_state._force_nav_sync = True

    ok, msg = copy_to_clipboard(file_info["path"])
    channel = file_info.get("channel") or "Data"
    name = os.path.basename(file_info["path"])
    st.toast(f"📋 Copied path — {channel} ({name})" if ok else f"⚠️ {msg}")


def build_gallery_lookup(filtered_files, frames_info):
    """(seen_channels, {(frame_key, channel): file_info}) for the gallery."""
    seen_channels = []
    for f in filtered_files:
        ch = f.get("channel") or "Data"
        if ch not in seen_channels:
            seen_channels.append(ch)

    lookup = {}
    for f in filtered_files:
        fkey = (f["experiment"], f["frame"])
        ch = f.get("channel") or "Data"
        if fkey in frames_info and (fkey, ch) not in lookup:
            lookup[(fkey, ch)] = f

    return seen_channels, lookup


def build_gallery_rows(
    seen_channels,
    gallery_lookup,
    frame_keys,
    get_bg_method_for_channel,
    poly_degree,
    fourier_cutoff,
    limit=6,
):
    """
    Ordered (frame_key, [(channel, file_info), ...]) rows for the Quick
    Gallery, filtered to files with loadable data. This exact ordering is
    what image_select's returned index refers to, so it must be computed
    identically whether it's used to peek for a pending click (before
    Tab1 renders) or for the actual later render — same function, called
    twice, guarantees that.
    """
    rows = []
    for fk in frame_keys[:limit]:
        entries = []
        for ch in seen_channels:
            file_info = gallery_lookup.get((fk, ch))
            if file_info is None:
                continue
            bg_method = get_bg_method_for_channel(ch)
            data = cached_load_and_process(
                file_info["path"], bg_method, poly_degree, fourier_cutoff
            )
            if data is None:
                continue
            entries.append((ch, file_info))
        if entries:
            rows.append((fk, entries))
    return rows


def detect_image_select_click(row_key, count):
    """
    Returns the newly-clicked index for a `key=row_key` image_select
    widget if it changed since the last time this was checked, else None.
    Tracks "last seen index" itself in session_state, so callers don't
    re-fire on every rerun — only on an actual fresh click.
    """
    prev_key = f"_{row_key}_prev"
    clicked_idx = st.session_state.get(row_key)
    prev_idx = st.session_state.get(prev_key, 0)
    if clicked_idx is not None and clicked_idx != prev_idx and clicked_idx < count:
        st.session_state[prev_key] = clicked_idx
        return clicked_idx
    return None


def sync_gallery_clicks(gallery_rows, filtered_files):
    """
    Peek at each gallery row's image_select value *before* it's actually
    rendered again further down the page, and apply a pending click right
    away. Streamlit already has a keyed widget's latest value in
    session_state at the start of a rerun, before the script reaches the
    line that re-creates that widget — so this lets a gallery click take
    effect in the same rerun Tab1 uses, instead of needing a second,
    visibly-flashing st.rerun() after Quick Gallery finishes rendering.
    """
    for fk, entries in gallery_rows:
        row_key = f"gallery_row_{fk[0]}_{fk[1]}"
        clicked_idx = detect_image_select_click(row_key, len(entries))
        if clicked_idx is not None:
            _, clicked_file = entries[clicked_idx]
            select_file_from_gallery(clicked_file, filtered_files)


# ============================================================
# Visualization Functions
# ============================================================
def create_figure(
    data, title="", colormap="viridis", show_colorbar=True, vmin=None, vmax=None
):
    """Create matplotlib figure for TIFF data."""
    fig, ax = plt.subplots(figsize=(8, 8))

    # Use provided vmin/vmax or calculate from data
    if vmin is None or vmax is None:
        auto_vmin, auto_vmax = get_display_range(data)
        vmin = vmin if vmin is not None else auto_vmin
        vmax = vmax if vmax is not None else auto_vmax

    im = ax.imshow(data, cmap=colormap, vmin=vmin, vmax=vmax, origin="upper")

    if show_colorbar:
        cbar = plt.colorbar(im, ax=ax, shrink=0.8)
        cbar.ax.tick_params(labelsize=10)

    ax.set_title(title, fontsize=12)
    ax.set_xlabel("X (pixels)")
    ax.set_ylabel("Y (pixels)")

    plt.tight_layout()
    return fig


# ============================================================
# Streamlit App
# ============================================================
def main():
    st.set_page_config(page_title="Park AFM TIFF Viewer", page_icon="🔬", layout="wide")

    st.title("🔬 Park AFM TIFF Viewer")
    st.markdown("Browse and preview TIFF files from Park AFM measurements")

    # ---- Sidebar: Folder Selection ----
    st.sidebar.header("📁 Folder Selection")

    folder_path = st.sidebar.text_input(
        "Data Folder Path",
        value=DEFAULT_FOLDER,
        help="Enter the full path to the folder containing TIFF files",
    )

    # Load files
    files = get_tiff_files(folder_path)

    if not files:
        st.warning(f"No TIFF files found in: {folder_path}")
        st.info("Please enter a valid folder path in the sidebar.")
        return

    st.sidebar.success(f"Found {len(files)} TIFF files")

    # ---- Sidebar: Filtering Options ----
    st.sidebar.header("🔍 Filter Options")

    # Hide incomplete images toggle
    hide_incomplete = st.sidebar.toggle(
        "🚫 Hide incomplete images",
        value=False,
        help="Hide images that were cut-off during scanning",
    )

    # Get unique values for filtering
    unique_vals = get_unique_values(files)
    channels = unique_vals["channels"]
    directions = unique_vals["directions"]
    experiments = unique_vals["experiments"]

    # Filter by channel
    selected_channels = st.sidebar.multiselect(
        "Channels",
        options=channels,
        default=channels,
        help="Select which channels to display",
    )

    # Filter by direction
    selected_directions = st.sidebar.multiselect(
        "Scan Direction", options=directions, default=directions
    )

    # Filter by experiment
    selected_experiments = st.sidebar.multiselect(
        "Experiments", options=experiments, default=experiments
    )

    # Apply filters
    filtered_files = filter_files(
        files,
        channels=selected_channels if selected_channels else None,
        directions=selected_directions if selected_directions else None,
        experiments=selected_experiments if selected_experiments else None,
    )

    # Apply incomplete filter if enabled
    if hide_incomplete:
        complete_files = []
        incomplete_count = 0
        for f in filtered_files:
            is_complete, pct = cached_check_file_complete(f["path"])
            if is_complete:
                complete_files.append(f)
            else:
                incomplete_count += 1
        filtered_files = complete_files
        if incomplete_count > 0:
            st.sidebar.warning(f"Hidden {incomplete_count} incomplete images")

    if not filtered_files:
        st.warning("No files match the current filters.")
        return

    st.sidebar.info(f"Showing {len(filtered_files)} of {len(files)} files")

    # Canonical "currently selected file" index, shared by every nav mode
    # in the Single File tab and settable from the gallery views below.
    if "file_idx" not in st.session_state:
        st.session_state.file_idx = 0
    st.session_state.file_idx = max(
        0, min(st.session_state.file_idx, len(filtered_files) - 1)
    )

    # ---- Sidebar: Display Options ----
    st.sidebar.header("🎨 Display Options")

    colormap = st.sidebar.selectbox(
        "Colormap",
        options=[
            "viridis",
            "plasma",
            "inferno",
            "magma",
            "terrain",
            "RdBu_r",
            "twilight",
            "gray",
            "hot",
            "cool",
        ],
        index=0,
    )

    auto_colormap = st.sidebar.checkbox(
        "Auto-select colormap by channel",
        value=True,
        help="Automatically choose colormap based on data channel",
    )

    show_metadata = st.sidebar.checkbox(
        "Show TIFF metadata",
        value=True,
        help="Display metadata extracted from Park AFM TIFF files",
    )

    # ---- Sidebar: Color Scale ----
    st.sidebar.header("🌈 Color Scale")

    color_scale_mode = st.sidebar.radio(
        "Color scale mode",
        ["Auto (Percentile)", "Manual"],
        index=0,
        horizontal=True,
        help="Auto uses percentile-based limits; Manual lets you set exact values",
    )

    if color_scale_mode == "Auto (Percentile)":
        # More intuitive: use symmetric clipping
        color_clip = st.sidebar.slider(
            "Clip outliers (%)",
            min_value=0.0,
            max_value=10.0,
            value=1.0,
            step=0.5,
            help="Percentage of data to clip from each end (1% = use 1st-99th percentile)",
        )
        percentile_low = color_clip
        percentile_high = 100.0 - color_clip

        # Initialize manual vars for export
        manual_scale_factor = 1.0
        manual_center_offset = 0.0
    else:
        # Manual range - will be set per-image based on data
        st.sidebar.info("Manual range is set relative to data")
        manual_scale_factor = st.sidebar.slider(
            "Scale factor",
            min_value=0.1,
            max_value=3.0,
            value=1.0,
            step=0.1,
            help="Multiply the auto-detected range by this factor (>1 = wider range, <1 = narrower)",
        )
        manual_center_offset = st.sidebar.slider(
            "Center offset",
            min_value=-1.0,
            max_value=1.0,
            value=0.0,
            step=0.05,
            help="Shift the color center (-1 to +1 relative to data range)",
        )
        percentile_low = 1.0
        percentile_high = 99.0

    # Helper function to get display range with current settings
    def get_adjusted_display_range(data):
        """Get vmin/vmax based on current color scale settings."""
        if data is None:
            return 0, 1

        if color_scale_mode == "Auto (Percentile)":
            vmin = np.nanpercentile(data, percentile_low)
            vmax = np.nanpercentile(data, percentile_high)
        else:
            # Manual mode with scale factor
            base_vmin = np.nanpercentile(data, 1)
            base_vmax = np.nanpercentile(data, 99)
            data_range = base_vmax - base_vmin
            center = (base_vmin + base_vmax) / 2

            # Apply center offset
            center += manual_center_offset * data_range

            # Apply scale factor
            half_range = (data_range / 2) * manual_scale_factor
            vmin = center - half_range
            vmax = center + half_range

        return vmin, vmax

    # ---- Sidebar: Background Subtraction ----
    st.sidebar.header("🔧 Background Subtraction")

    # Global default method
    default_bg_method = st.sidebar.selectbox(
        "Default method",
        options=BACKGROUND_METHODS,
        index=0,
        key="default_bg_method",
        help="Default background subtraction for all channels",
    )

    # Get available channels
    available_channels = get_unique_values(files)["channels"]

    # Per-channel override settings
    use_per_channel = st.sidebar.checkbox(
        "Use per-channel settings",
        value=False,
        help="Enable different background subtraction methods for each channel",
    )

    # Initialize/store per-channel settings
    if "bg_settings" not in st.session_state:
        st.session_state.bg_settings = {}

    if use_per_channel:
        with st.sidebar.expander("Per-channel settings", expanded=True):
            for channel in available_channels:
                # Get current setting or default
                current = st.session_state.bg_settings.get(channel, default_bg_method)
                if current not in BACKGROUND_METHODS:
                    current = default_bg_method

                st.session_state.bg_settings[channel] = st.selectbox(
                    f"{channel}",
                    options=BACKGROUND_METHODS,
                    index=BACKGROUND_METHODS.index(current),
                    key=f"bg_{channel}",
                )

    # Advanced parameters
    with st.sidebar.expander("Advanced parameters", expanded=False):
        poly_degree = st.slider(
            "Polynomial degree",
            min_value=1,
            max_value=5,
            value=2,
            help="Degree for polynomial fitting methods",
        )
        fourier_cutoff = st.slider(
            "Fourier cutoff",
            min_value=0.01,
            max_value=0.20,
            value=0.05,
            step=0.01,
            help="Cutoff fraction for Fourier high-pass filter (lower = less aggressive)",
        )

    # Helper function to get background method for a channel
    def get_bg_method_for_channel(channel):
        if use_per_channel:
            return st.session_state.bg_settings.get(channel, default_bg_method)
        return default_bg_method

    # ---- Sidebar: External Tools ----
    st.sidebar.header("🔬 External Tools")

    if "gwyddion_path" not in st.session_state:
        st.session_state.gwyddion_path = find_gwyddion_executable() or ""

    with st.sidebar.expander(
        "Gwyddion path",
        expanded=not st.session_state.gwyddion_path,
    ):
        st.session_state.gwyddion_path = st.text_input(
            "Executable / app path",
            value=st.session_state.gwyddion_path,
            help="Path to the Gwyddion executable (Windows/Linux) or "
            "Gwyddion.app (macOS). Auto-detected if left as found.",
            label_visibility="collapsed",
        )
        if st.session_state.gwyddion_path:
            st.caption(f"✅ {st.session_state.gwyddion_path}")
        else:
            st.caption("⚠️ Gwyddion not found — set the path manually")

    # ---- Early gallery-click detection (must run before Tab1) ----
    # The Quick Gallery itself is rendered far below (after all 4 tabs),
    # but a click there needs to affect Tab1's selection in the *same*
    # rerun. Computing the rows and checking for a pending click here,
    # before Tab1 exists, makes that possible with zero extra reruns.
    frames_info_gallery = get_frames_info(filtered_files)
    frame_keys_gallery = sorted(frames_info_gallery.keys())
    seen_channels_gallery, gallery_lookup = build_gallery_lookup(
        filtered_files, frames_info_gallery
    )
    gallery_rows = build_gallery_rows(
        seen_channels_gallery,
        gallery_lookup,
        frame_keys_gallery,
        get_bg_method_for_channel,
        poly_degree,
        fourier_cutoff,
    )
    sync_gallery_clicks(gallery_rows, filtered_files)

    # ---- Main Tabs ----
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📄 Single File", "📊 All Channels", "📤 Export to PowerPoint", "🖼️ Full Gallery"]
    )

    # ============================================================
    # TAB 1: Single File View
    # ============================================================
    with tab1:
        col1, col2 = st.columns([1, 2])

        with col1:
            st.subheader("📄 File Selection")

            # Create display names for dropdown
            file_options = {
                file_label(i, f): i for i, f in enumerate(filtered_files)
            }
            file_names = list(file_options.keys())

            # Navigation mode
            nav_mode = st.radio(
                "Navigation",
                ["Dropdown", "Slider", "Prev/Next"],
                horizontal=True,
                key="nav_mode_single",
            )

            # If a gallery click just happened, force the *active* nav
            # widget's stored state to match st.session_state.file_idx
            # before it's instantiated below (must happen pre-creation).
            if st.session_state.pop("_force_nav_sync", False):
                if nav_mode == "Dropdown":
                    st.session_state["dropdown_select_idx"] = file_label(
                        st.session_state.file_idx,
                        filtered_files[st.session_state.file_idx],
                    )
                elif nav_mode == "Slider":
                    st.session_state["slider_select_idx"] = st.session_state.file_idx

            if nav_mode == "Dropdown":
                # Guard against a stale selection left over from before a
                # filter change removed that file from the options list.
                if st.session_state.get("dropdown_select_idx") not in file_names:
                    st.session_state["dropdown_select_idx"] = file_names[
                        min(st.session_state.file_idx, len(file_names) - 1)
                    ]
                selected_name = st.selectbox(
                    "Select file", options=file_names, key="dropdown_select_idx"
                )
                selected_idx = file_options[selected_name]

            elif nav_mode == "Slider":
                max_idx = len(filtered_files) - 1
                if (
                    "slider_select_idx" not in st.session_state
                    or st.session_state["slider_select_idx"] > max_idx
                ):
                    st.session_state["slider_select_idx"] = min(
                        st.session_state.file_idx, max_idx
                    )
                selected_idx = st.slider(
                    "File index",
                    min_value=0,
                    max_value=max_idx,
                    key="slider_select_idx",
                )

            else:  # Prev/Next
                col_prev, col_next = st.columns(2)
                with col_prev:
                    if st.button(
                        "⬅️ Previous", use_container_width=True, key="prev_single"
                    ):
                        st.session_state.file_idx = max(
                            0, st.session_state.file_idx - 1
                        )
                with col_next:
                    if st.button("Next ➡️", use_container_width=True, key="next_single"):
                        st.session_state.file_idx = min(
                            len(filtered_files) - 1, st.session_state.file_idx + 1
                        )

                selected_idx = st.session_state.file_idx
                st.write(f"File {selected_idx + 1} of {len(filtered_files)}")

            # Keep the canonical index in sync with whichever widget the
            # user actually just used, so switching nav modes or clicking
            # a gallery thumbnail next always starts from the right place.
            st.session_state.file_idx = selected_idx

            # Get selected file
            selected_file = filtered_files[selected_idx]

            # Display metadata
            st.markdown("---")
            st.subheader("📋 File Info")

            # Check completeness
            is_complete, completion_pct = cached_check_file_complete(
                selected_file["path"]
            )

            info_cols = st.columns(2)
            with info_cols[0]:
                st.markdown(f"**Channel:** {selected_file['channel'] or 'Unknown'}")
                st.markdown(f"**Direction:** {selected_file['direction'] or 'Unknown'}")
            with info_cols[1]:
                st.markdown(f"**Frame:** {selected_file['frame'] or 'Unknown'}")
                st.markdown(f"**Experiment:** {selected_file['experiment']}")

            # Completion status
            if is_complete:
                st.success(f"✅ Complete ({completion_pct:.0f}%)")
            else:
                st.error(f"⚠️ Incomplete ({completion_pct:.0f}%)")

            # Open in Gwyddion
            if st.button(
                "🔬 Open in Gwyddion",
                use_container_width=True,
                key="open_gwyddion_single",
                disabled=not st.session_state.gwyddion_path,
                help="Launch this file in Gwyddion"
                if st.session_state.gwyddion_path
                else "Set the Gwyddion path in the sidebar first",
            ):
                ok, msg = open_in_gwyddion(
                    selected_file["path"], st.session_state.gwyddion_path
                )
                if ok:
                    st.toast(msg, icon="🔬")
                else:
                    st.error(msg)

            # TIFF Metadata (if enabled)
            if show_metadata:
                st.markdown("---")
                st.subheader("📊 TIFF Metadata")
                tiff_meta = extract_tiff_metadata(selected_file["path"])
                if tiff_meta:
                    meta_cols = st.columns(2)
                    with meta_cols[0]:
                        st.markdown(
                            f"**Scan Size:** {tiff_meta.get('scan_size_um', 'N/A')} µm"
                        )
                        st.markdown(
                            f"**Scan Rate:** {tiff_meta.get('scan_rate_hz', 'N/A'):.1f} Hz"
                            if tiff_meta.get("scan_rate_hz")
                            else "**Scan Rate:** N/A"
                        )
                    with meta_cols[1]:
                        st.markdown(
                            f"**Date/Time:** {tiff_meta.get('datetime', 'N/A')}"
                        )
                        st.markdown(f"**Software:** {tiff_meta.get('software', 'N/A')}")
                else:
                    st.info("No metadata available for this file")

        # ---- Main Area: Image Display ----
        with col2:
            st.subheader("🖼️ Image Preview")

            # Load and display image
            bg_method = get_bg_method_for_channel(selected_file["channel"])
            data = cached_load_and_process(
                selected_file["path"], bg_method, poly_degree, fourier_cutoff
            )

            if data is not None:
                if bg_method != "None":
                    st.caption(f"🔧 Background: {bg_method}")

                # Show data statistics
                stats = get_image_stats(data)
                stat_cols = st.columns(4)
                with stat_cols[0]:
                    st.metric("Shape", f"{stats['shape'][0]}×{stats['shape'][1]}")
                with stat_cols[1]:
                    st.metric("Min", f"{stats['min']:.3g}")
                with stat_cols[2]:
                    st.metric("Max", f"{stats['max']:.3g}")
                with stat_cols[3]:
                    st.metric("Mean", f"{stats['mean']:.3g}")

                # Choose colormap
                if auto_colormap and selected_file["channel"]:
                    use_colormap = get_colormap_for_channel(selected_file["channel"])
                else:
                    use_colormap = colormap

                # Get color scale limits
                vmin, vmax = get_adjusted_display_range(data)

                # Show color scale info
                st.caption(f"🌈 Color range: {vmin:.3g} to {vmax:.3g}")

                # Create figure
                title = f"{selected_file['channel'] or 'Data'} - {selected_file['direction'] or ''} (Frame {selected_file['frame'] or '?'})"
                fig = create_figure(
                    data, title=title, colormap=use_colormap, vmin=vmin, vmax=vmax
                )

                st.pyplot(fig)
                plt.close(fig)
            else:
                st.error("Failed to load image")

    # ============================================================
    # TAB 2: All Channels View
    # ============================================================
    with tab2:
        st.subheader("📊 All Channels for Selected Frame")
        st.caption("Click a thumbnail to copy its file path to the clipboard.")

        # Get unique frames (experiment + frame number combinations)
        frames_info = get_frames_info(filtered_files)

        if not frames_info:
            st.warning("No frames with valid metadata found.")
        else:
            # Frame selection
            col_sel1, col_sel2 = st.columns([2, 1])

            with col_sel1:
                frame_options = [
                    f"{exp} - Frame {frm:03d}"
                    for (exp, frm) in sorted(frames_info.keys())
                ]
                selected_frame_str = st.selectbox(
                    "Select Frame",
                    options=frame_options,
                    index=0,
                    key="frame_select_all",
                )

                # Parse selection
                selected_key = list(sorted(frames_info.keys()))[
                    frame_options.index(selected_frame_str)
                ]
                sel_experiment, sel_frame = selected_key

            with col_sel2:
                direction_filter = st.selectbox(
                    "Direction",
                    options=["Both", "Forward", "Backward"],
                    index=0,
                    key="dir_filter_all",
                )

            # Get all channels for this frame
            dir_filter = None if direction_filter == "Both" else direction_filter
            frame_channels = get_all_channels_for_frame(
                files, sel_experiment, sel_frame, dir_filter
            )

            if not frame_channels:
                st.info(f"No channels found for {sel_experiment} Frame {sel_frame}")
            else:
                st.markdown(f"**Found {len(frame_channels)} channels**")

                entries = []
                for f in frame_channels:
                    bg_method = get_bg_method_for_channel(f["channel"])
                    data = cached_load_and_process(
                        f["path"], bg_method, poly_degree, fourier_cutoff
                    )
                    if data is None:
                        continue

                    is_complete, pct = cached_check_file_complete(f["path"])
                    status = "✅" if is_complete else f"⚠️{pct:.0f}%"

                    cmap = (
                        get_colormap_for_channel(f["channel"])
                        if auto_colormap and f["channel"]
                        else colormap
                    )
                    vmin, vmax = get_adjusted_display_range(data)
                    arr = render_thumbnail_array(
                        f["path"],
                        bg_method,
                        poly_degree,
                        fourier_cutoff,
                        cmap,
                        float(vmin),
                        float(vmax),
                    )
                    if arr is None:
                        continue

                    stats = get_image_stats(data)
                    caption = f"{f['channel']} · {f['direction']} {status}"
                    if bg_method != "None":
                        caption += f" [{bg_method}]"
                    caption += f" · {stats['min']:.2g}–{stats['max']:.2g}"

                    entries.append((f, arr, caption))

                if entries:
                    row_key = "all_channels_select"
                    clicked_idx = detect_image_select_click(row_key, len(entries))
                    if clicked_idx is not None:
                        clicked_file = entries[clicked_idx][0]
                        ok, msg = copy_to_clipboard(clicked_file["path"])
                        name = os.path.basename(clicked_file["path"])
                        st.toast(f"📋 Copied path — {name}" if ok else f"⚠️ {msg}")

                    default_idx = min(
                        st.session_state.get(f"_{row_key}_prev", 0), len(entries) - 1
                    )
                    image_select(
                        "",
                        images=[arr for _, arr, _ in entries],
                        captions=[caption for _, _, caption in entries],
                        index=default_idx,
                        return_value="index",
                        key=row_key,
                    )

    # ============================================================
    # TAB 3: Export to PowerPoint
    # ============================================================
    with tab3:
        st.subheader("📤 Export to PowerPoint")
        st.markdown(
            "Create a PowerPoint presentation with AFM images. Each slide contains one frame with all its channels."
        )

        col_export1, col_export2 = st.columns([1, 1])

        with col_export1:
            st.markdown("#### Export Options")

            # Data selection
            export_scope = st.radio(
                "Data to export",
                ["All filtered data", "Selected frame only"],
                index=0,
                help="Export all data matching current filters, or just a specific frame",
            )

            # If single frame, select which one
            if export_scope == "Selected frame only":
                frames_info_export = get_frames_info(filtered_files)
                if frames_info_export:
                    frame_options_export = [
                        f"{exp} - Frame {frm:03d}"
                        for (exp, frm) in sorted(frames_info_export.keys())
                    ]
                    selected_export_frame = st.selectbox(
                        "Select frame to export",
                        options=frame_options_export,
                        index=0,
                        key="export_frame_select",
                    )
                else:
                    st.warning("No frames available for export")
                    selected_export_frame = None

            # Direction filter for export
            export_direction = st.selectbox(
                "Direction to export",
                options=["Both", "Forward", "Backward"],
                index=0,
                key="export_direction",
            )

            # Auto scan size toggle
            auto_scan_size = st.checkbox(
                "Auto-detect scan size from metadata",
                value=True,
                key="auto_scan_size",
                help="Automatically extract scan size from TIFF metadata",
            )

            # Scan size for scale bar (only if not auto)
            if not auto_scan_size:
                scan_size = st.number_input(
                    "Scan size (µm)",
                    min_value=0.1,
                    max_value=100.0,
                    value=10.0,
                    step=0.5,
                    help="Physical scan size in micrometers (for scale bar)",
                )
            else:
                scan_size = None  # Will be extracted from metadata

            # Show metadata on slides
            export_show_metadata = st.checkbox(
                "Include metadata on slides",
                value=True,
                key="export_metadata",
                help="Show filename, date, scan size on each slide",
            )

        with col_export2:
            st.markdown("#### Channel Selection")

            # Get all channels available
            all_channels_in_files = get_unique_values(filtered_files)["channels"]

            # Channels to exclude (noisy channels)
            st.markdown("**Exclude channels** (e.g., noisy channels)")
            excluded_channels = st.multiselect(
                "Channels to exclude from export",
                options=all_channels_in_files,
                default=[],
                key="excluded_channels",
                help="Select channels to exclude from the PowerPoint export",
            )

            # Layout options
            st.markdown("#### Layout Options")
            export_cols = st.selectbox(
                "Columns per slide", options=[2, 3, 4, 5], index=2, key="export_cols"
            )

            use_auto_colormap = auto_colormap

        st.markdown("---")

        # Export button and progress
        col_btn, col_status = st.columns([1, 2])

        with col_btn:
            export_clicked = st.button(
                "🚀 Generate PowerPoint", type="primary", use_container_width=True
            )

        if export_clicked:
            # Determine files to export
            if (
                export_scope == "Selected frame only"
                and "selected_export_frame" in dir()
                and selected_export_frame
            ):
                # Get the selected frame
                frames_info_export = get_frames_info(filtered_files)
                frame_keys = list(sorted(frames_info_export.keys()))
                frame_options_export = [
                    f"{exp} - Frame {frm:03d}" for (exp, frm) in frame_keys
                ]
                selected_idx = frame_options_export.index(selected_export_frame)
                sel_exp, sel_frm = frame_keys[selected_idx]

                # Filter to just this frame
                export_files = [
                    f
                    for f in filtered_files
                    if f["experiment"] == sel_exp and f["frame"] == sel_frm
                ]
            else:
                export_files = filtered_files

            # Direction filter
            dir_filter = None if export_direction == "Both" else export_direction

            # Create temporary file for download
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                output_path = os.path.join(tmpdir, "AFM_Export.pptx")

                # Progress bar
                progress_bar = st.progress(0, text="Preparing export...")

                def update_progress(current, total):
                    progress_bar.progress(
                        current / total, text=f"Processing frame {current}/{total}..."
                    )

                # Build color scale settings for export
                if color_scale_mode == "Auto (Percentile)":
                    export_color_settings = {
                        "mode": "auto",
                        "percentile_low": percentile_low,
                        "percentile_high": percentile_high,
                    }
                else:
                    export_color_settings = {
                        "mode": "manual",
                        "scale_factor": manual_scale_factor,
                        "center_offset": manual_center_offset,
                    }

                # Build background settings for export
                if use_per_channel:
                    export_bg_settings = st.session_state.bg_settings.copy()
                else:
                    # Apply default method to all channels
                    export_bg_settings = {
                        ch: default_bg_method for ch in available_channels
                    }

                # Run export
                success, message, num_slides = export_to_powerpoint(
                    export_files,
                    output_path,
                    scan_size_um=scan_size,
                    cols_per_row=export_cols,
                    auto_colormap=use_auto_colormap,
                    excluded_channels=excluded_channels if excluded_channels else None,
                    direction_filter=dir_filter,
                    progress_callback=update_progress,
                    show_metadata=export_show_metadata,
                    auto_scan_size=auto_scan_size,
                    bg_settings=export_bg_settings,
                    bg_params={
                        "degree": poly_degree,
                        "cutoff_fraction": fourier_cutoff,
                    },
                    color_scale_settings=export_color_settings,
                    fallback_colormap=colormap,
                )

                progress_bar.empty()

                if success:
                    st.success(f"✅ {message}")

                    # Read the file for download
                    with open(output_path, "rb") as f:
                        pptx_data = f.read()

                    # Generate filename
                    from datetime import datetime

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    download_filename = f"AFM_Export_{timestamp}.pptx"

                    st.download_button(
                        label="📥 Download PowerPoint",
                        data=pptx_data,
                        file_name=download_filename,
                        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        type="primary",
                    )
                else:
                    st.error(f"❌ {message}")

    # ============================================================
    # TAB 4: Full Gallery
    # ============================================================
    with tab4:
        st.subheader("🖼️ Full Gallery")
        st.markdown("All frames and channels with current display settings.")

        frames_info_full = get_frames_info(filtered_files)
        frame_keys_full = sorted(frames_info_full.keys())

        if not frame_keys_full:
            st.info("No frames available.")
        else:
            # Collect unique channels preserving order of appearance
            all_channels = []
            for f in filtered_files:
                ch = f.get("channel") or "Data"
                if ch not in all_channels:
                    all_channels.append(ch)

            # Build a lookup: (frame_key, channel) -> file info
            full_lookup = {}
            for f in filtered_files:
                fkey = (f["experiment"], f["frame"])
                ch = f.get("channel") or "Data"
                if fkey in frames_info_full and (fkey, ch) not in full_lookup:
                    full_lookup[(fkey, ch)] = f

            st.caption(f"{len(frame_keys_full)} frames × {len(all_channels)} channels")

            # Header row with channel labels
            header_cols = st.columns([0.8] + [1] * len(all_channels))
            with header_cols[0]:
                st.markdown("**Frame**")
            for j, ch in enumerate(all_channels):
                with header_cols[j + 1]:
                    st.markdown(f"**{ch}**")

            # One row per frame
            for fk in frame_keys_full:
                row_cols = st.columns([0.8] + [1] * len(all_channels))
                with row_cols[0]:
                    st.markdown(f"_Frame {fk[1]:03d}_")
                for j, ch in enumerate(all_channels):
                    with row_cols[j + 1]:
                        file_info = full_lookup.get((fk, ch))
                        if file_info is None:
                            st.caption("—")
                            continue

                        bg_method = get_bg_method_for_channel(ch)
                        data = cached_load_and_process(
                            file_info["path"], bg_method, poly_degree, fourier_cutoff
                        )
                        if data is None:
                            st.caption("no data")
                            continue

                        # Choose colormap
                        if auto_colormap and ch:
                            cmap = get_colormap_for_channel(ch)
                        else:
                            cmap = colormap

                        # Get color scale limits
                        vmin, vmax = get_adjusted_display_range(data)

                        png = render_thumbnail_png(
                            file_info["path"],
                            bg_method,
                            poly_degree,
                            fourier_cutoff,
                            cmap,
                            float(vmin),
                            float(vmax),
                        )
                        if png:
                            st.image(png, use_container_width=True)

    # ---- Bottom: Quick Gallery ----
    st.markdown("---")
    with st.expander("📸 Quick Gallery", expanded=False):
        st.caption(
            "Click a thumbnail to show it in the Single File tab and copy "
            "its path to the clipboard. ✅ marks the one currently shown there."
        )

        if not gallery_rows:
            st.info("No frames available for gallery.")
        else:
            current_path = filtered_files[st.session_state.file_idx]["path"]

            # One clickable image_select row per frame. Any click was
            # already applied above (before Tab1 rendered) — this just
            # displays the current state, it doesn't need to detect
            # changes or rerun itself.
            for fk, entries in gallery_rows:
                st.markdown(f"**Frame {fk[1]:03d}** — _{fk[0]}_")

                images = []
                captions = []
                for ch, file_info in entries:
                    bg_method = get_bg_method_for_channel(ch)
                    data = cached_load_and_process(
                        file_info["path"], bg_method, poly_degree, fourier_cutoff
                    )
                    cmap = (
                        get_colormap_for_channel(ch)
                        if auto_colormap and ch
                        else colormap
                    )
                    vmin, vmax = get_adjusted_display_range(data)
                    images.append(
                        render_thumbnail_array(
                            file_info["path"],
                            bg_method,
                            poly_degree,
                            fourier_cutoff,
                            cmap,
                            float(vmin),
                            float(vmax),
                        )
                    )
                    captions.append(f"{ch} ✅" if file_info["path"] == current_path else ch)

                row_key = f"gallery_row_{fk[0]}_{fk[1]}"
                prev_key = f"_{row_key}_prev"
                image_select(
                    "",
                    images=images,
                    captions=captions,
                    index=st.session_state.get(prev_key, 0),
                    return_value="index",
                    key=row_key,
                )


if __name__ == "__main__":
    main()
