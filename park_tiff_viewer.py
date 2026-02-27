"""
Park AFM TIFF Viewer
====================
A Streamlit app for browsing and previewing Park AFM TIFF data files.

Run with: streamlit run park_tiff_viewer.py

Author: NanosparQ Training
"""

import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

# Import core logic from separate module
from park_tiff_core import (
    BACKGROUND_METHODS,
    DEFAULT_FOLDER,
    KNOWN_CHANNELS,
    apply_background_subtraction,
    check_file_complete,
    export_to_powerpoint,
    extract_tiff_metadata,
    filter_files,
    get_all_channels_for_frame,
    get_colormap_for_channel,
    get_display_range,
    get_frames_info,
    get_image_stats,
    get_scan_size_from_file,
    get_tiff_files,
    get_unique_values,
    load_tiff,
)


# ============================================================
# Streamlit-specific wrappers with caching
# ============================================================
@st.cache_data
def cached_load_tiff(filepath):
    """Cached wrapper for load_tiff."""
    return load_tiff(filepath)


@st.cache_data
def cached_check_file_complete(filepath, threshold_ratio=0.1):
    """Cached wrapper for check_file_complete."""
    return check_file_complete(filepath, threshold_ratio)


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

    # Helper function to apply background subtraction
    def apply_bg_for_channel(data, channel):
        method = get_bg_method_for_channel(channel)
        return apply_background_subtraction(
            data, method, degree=poly_degree, cutoff_fraction=fourier_cutoff
        )

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
                f"{i + 1}. {f['full_name']}": i for i, f in enumerate(filtered_files)
            }

            # Navigation mode
            nav_mode = st.radio(
                "Navigation",
                ["Dropdown", "Slider", "Prev/Next"],
                horizontal=True,
                key="nav_mode_single",
            )

            if nav_mode == "Dropdown":
                selected_name = st.selectbox(
                    "Select file", options=list(file_options.keys()), index=0
                )
                selected_idx = file_options[selected_name]

            elif nav_mode == "Slider":
                selected_idx = st.slider(
                    "File index",
                    min_value=0,
                    max_value=len(filtered_files) - 1,
                    value=0,
                )

            else:  # Prev/Next
                if "file_idx" not in st.session_state:
                    st.session_state.file_idx = 0

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
            data = cached_load_tiff(selected_file["path"])

            if data is not None:
                # Work with a copy to avoid modifying cached data
                data = data.copy()

                # Apply background subtraction
                bg_method = get_bg_method_for_channel(selected_file["channel"])
                if bg_method != "None":
                    data = apply_bg_for_channel(data, selected_file["channel"])
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

        # Get unique frames (experiment + frame number combinations)
        frames_info = get_frames_info(filtered_files)

        if not frames_info:
            st.warning("No frames with valid metadata found.")
        else:
            # Frame selection
            col_sel1, col_sel2, col_sel3 = st.columns([2, 1, 1])

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

            with col_sel3:
                cols_per_row = st.selectbox(
                    "Columns", options=[2, 3, 4], index=1, key="cols_all"
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

                # Display all channels in grid
                n_channels = len(frame_channels)
                rows_needed = (n_channels + cols_per_row - 1) // cols_per_row

                for row in range(rows_needed):
                    cols = st.columns(cols_per_row)
                    for col_idx in range(cols_per_row):
                        file_idx = row * cols_per_row + col_idx
                        if file_idx < n_channels:
                            f = frame_channels[file_idx]
                            with cols[col_idx]:
                                data = cached_load_tiff(f["path"])
                                if data is not None:
                                    # Work with a copy to avoid modifying cached data
                                    data = data.copy()

                                    # Apply background subtraction
                                    bg_method = get_bg_method_for_channel(f["channel"])
                                    if bg_method != "None":
                                        data = apply_bg_for_channel(data, f["channel"])

                                    # Check completeness
                                    is_complete, pct = cached_check_file_complete(
                                        f["path"]
                                    )
                                    status = "✅" if is_complete else f"⚠️ {pct:.0f}%"

                                    # Choose colormap
                                    if auto_colormap and f["channel"]:
                                        cmap = get_colormap_for_channel(f["channel"])
                                    else:
                                        cmap = colormap

                                    # Get color scale limits
                                    vmin, vmax = get_adjusted_display_range(data)

                                    # Create figure
                                    fig, ax = plt.subplots(figsize=(4, 4))
                                    im = ax.imshow(
                                        data,
                                        cmap=cmap,
                                        vmin=vmin,
                                        vmax=vmax,
                                        origin="upper",
                                    )
                                    plt.colorbar(im, ax=ax, shrink=0.8)

                                    # Title with BG indicator
                                    title = f"{f['channel']}\n{f['direction']} {status}"
                                    if bg_method != "None":
                                        title += f"\n[{bg_method}]"
                                    ax.set_title(title, fontsize=9)
                                    ax.axis("off")
                                    plt.tight_layout()

                                    st.pyplot(fig)
                                    plt.close(fig)

                                    stats = get_image_stats(data)
                                    st.caption(
                                        f"Range: {stats['min']:.2g} - {stats['max']:.2g}"
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
            import os
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
                        data = cached_load_tiff(file_info["path"])
                        if data is None:
                            st.caption("no data")
                            continue
                        data = data.copy()

                        # Apply background subtraction
                        bg_method = get_bg_method_for_channel(ch)
                        if bg_method != "None":
                            data = apply_bg_for_channel(data, ch)

                        # Choose colormap
                        if auto_colormap and ch:
                            cmap = get_colormap_for_channel(ch)
                        else:
                            cmap = colormap

                        # Get color scale limits
                        vmin, vmax = get_adjusted_display_range(data)

                        fig, ax = plt.subplots(figsize=(2.5, 2.5))
                        ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
                        ax.axis("off")
                        plt.tight_layout(pad=0.3)
                        st.pyplot(fig)
                        plt.close(fig)

    # ---- Bottom: Quick Gallery ----
    st.markdown("---")
    with st.expander("📸 Quick Gallery", expanded=False):
        # Build grid: rows = frames, columns = channels
        frames_info_gallery = get_frames_info(filtered_files)
        frame_keys = sorted(frames_info_gallery.keys())

        if not frame_keys:
            st.info("No frames available for gallery.")
        else:
            # Collect unique channels preserving order of appearance
            seen_channels = []
            for f in filtered_files:
                ch = f.get("channel") or "Data"
                if ch not in seen_channels:
                    seen_channels.append(ch)

            # Build a lookup: (frame_key, channel) -> file info
            gallery_lookup = {}
            for f in filtered_files:
                fkey = (f["experiment"], f["frame"])
                ch = f.get("channel") or "Data"
                if fkey in frames_info_gallery and (fkey, ch) not in gallery_lookup:
                    gallery_lookup[(fkey, ch)] = f

            # Header row with channel labels
            header_cols = st.columns([0.8] + [1] * len(seen_channels))
            with header_cols[0]:
                st.markdown("**Frame**")
            for j, ch in enumerate(seen_channels):
                with header_cols[j + 1]:
                    st.markdown(f"**{ch}**")

            # One row per frame (limit to first 6)
            for fk in frame_keys[:6]:
                row_cols = st.columns([0.8] + [1] * len(seen_channels))
                with row_cols[0]:
                    st.markdown(f"_Frame {fk[1]:03d}_")
                for j, ch in enumerate(seen_channels):
                    with row_cols[j + 1]:
                        file_info = gallery_lookup.get((fk, ch))
                        if file_info is None:
                            st.caption("—")
                            continue
                        data = cached_load_tiff(file_info["path"])
                        if data is None:
                            st.caption("no data")
                            continue
                        data = data.copy()

                        # Apply background subtraction
                        bg_method = get_bg_method_for_channel(ch)
                        if bg_method != "None":
                            data = apply_bg_for_channel(data, ch)

                        # Choose colormap
                        if auto_colormap and ch:
                            cmap = get_colormap_for_channel(ch)
                        else:
                            cmap = colormap

                        # Get color scale limits
                        vmin, vmax = get_adjusted_display_range(data)

                        fig, ax = plt.subplots(figsize=(2.5, 2.5))
                        ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
                        ax.axis("off")
                        plt.tight_layout(pad=0.3)
                        st.pyplot(fig)
                        plt.close(fig)


if __name__ == "__main__":
    main()
