"""
Park AFM TIFF Core Module
=========================
Core logic for loading and processing Park AFM TIFF data files.

This module contains all data processing logic, independent of any UI framework.
"""

import numpy as np
from pathlib import Path
import os
import re
import platform
import shutil
import subprocess
from PIL import Image


# ============================================================
# Configuration
# ============================================================
DEFAULT_FOLDER = "/Users/pmaksym/Library/CloudStorage/Box-Box/ClemsonData/2026-01-16/VoltageAmplified"

# Common Park AFM channel names and their recommended colormaps
CHANNEL_COLORS = {
    'Z Height': 'terrain',
    'EFM Phase': 'twilight',
    'EFM Amplitude': 'viridis',
    'KPFM Potential': 'RdBu_r',
    'Lock-In4 Phase': 'hsv',
    'Lock-In4 Amplitude': 'plasma',
    'NCM Amplitude': 'magma',
    'Aux1 Out': 'gray',
}

# Known channel names for parsing
KNOWN_CHANNELS = [
    'Z Height', 'EFM Phase', 'EFM Amplitude', 'KPFM Potential',
    'Lock-In4 Phase', 'Lock-In4 Amplitude', 'NCM Amplitude',
    'Aux1 Out', 'Vision'
]


# ============================================================
# Data Loading Functions
# ============================================================
def load_tiff(filepath):
    """
    Load a TIFF file and return as numpy array.
    
    Args:
        filepath: Path to the TIFF file
        
    Returns:
        numpy.ndarray or None if loading fails
    """
    try:
        img = Image.open(filepath)
        data = np.array(img, dtype=np.float32)
        return data
    except Exception as e:
        return None


def extract_tiff_metadata(filepath):
    """
    Extract metadata from Park AFM TIFF file.
    
    Park AFM stores metadata in custom TIFF tags:
    - Tag 305: Software name
    - Tag 306: DateTime
    - Tag 50435: Binary header with channel name, scan size, etc.
    - Tag 50441: XML extended header
    
    Args:
        filepath: Path to the TIFF file
        
    Returns:
        dict with metadata fields, or None if extraction fails
    """
    import struct
    
    try:
        img = Image.open(filepath)
        
        metadata = {
            'filename': Path(filepath).name,
            'filepath': str(filepath),
            'software': None,
            'datetime': None,
            'channel': None,
            'scan_size_um': None,
            'scan_rate_hz': None,
            'z_scale': None,
            'image_size': img.size,
        }
        
        # Standard TIFF tags
        if hasattr(img, 'tag_v2'):
            metadata['software'] = img.tag_v2.get(305, None)
            metadata['datetime'] = img.tag_v2.get(306, None)
            
            # Park-specific binary header (Tag 50435)
            if 50435 in img.tag_v2:
                data = img.tag_v2[50435]
                
                # Channel name at offset 4-68 (UTF-16-LE)
                if len(data) >= 68:
                    channel = data[4:68].decode('utf-16-le', errors='ignore').rstrip('\x00')
                    metadata['channel'] = channel.strip() if channel.strip() else None
                
                # Z-scale at offset 120 (double)
                if len(data) >= 128:
                    metadata['z_scale'] = struct.unpack('<d', data[120:128])[0]
                
                # Scan rate at offset 360 (double, Hz)
                if len(data) >= 368:
                    metadata['scan_rate_hz'] = struct.unpack('<d', data[360:368])[0]
                
                # Scan size at offset 448 (double, micrometers)
                if len(data) >= 456:
                    metadata['scan_size_um'] = struct.unpack('<d', data[448:456])[0]
        
        return metadata
        
    except Exception as e:
        return None


def get_scan_size_from_file(filepath, default=10.0):
    """
    Get scan size from TIFF metadata.
    
    Args:
        filepath: Path to the TIFF file
        default: Default value if metadata extraction fails
        
    Returns:
        float: Scan size in micrometers
    """
    metadata = extract_tiff_metadata(filepath)
    if metadata and metadata.get('scan_size_um'):
        return metadata['scan_size_um']
    return default


def check_image_complete(data, threshold_ratio=0.1):
    """
    Check if an image is complete (not cut-off during scan).
    
    Detects incomplete images by checking for rows of constant/zero values
    at the bottom of the image (indicating scan was interrupted).
    
    Args:
        data: numpy array of image data
        threshold_ratio: ratio of incomplete rows to consider image incomplete
        
    Returns:
        tuple: (is_complete: bool, completion_percent: float)
    """
    if data is None:
        return False, 0.0
    
    try:
        n_rows = data.shape[0]
        
        # Check from bottom up for rows that are all the same value (or zero)
        incomplete_rows = 0
        for i in range(n_rows - 1, -1, -1):
            row = data[i, :]
            # Check if row is constant (all same value) or all zeros
            if np.all(row == row[0]) or np.std(row) < 1e-10:
                incomplete_rows += 1
            else:
                break
        
        completion_percent = 100 * (n_rows - incomplete_rows) / n_rows
        is_complete = (incomplete_rows / n_rows) < threshold_ratio
        
        return is_complete, completion_percent
    except:
        return False, 0.0


def check_file_complete(filepath, threshold_ratio=0.1):
    """
    Check if a TIFF file contains a complete image.
    
    Convenience function that loads the file and checks completeness.
    
    Args:
        filepath: Path to the TIFF file
        threshold_ratio: ratio of incomplete rows to consider image incomplete
        
    Returns:
        tuple: (is_complete: bool, completion_percent: float)
    """
    data = load_tiff(filepath)
    return check_image_complete(data, threshold_ratio)


# ============================================================
# Filename Parsing Functions
# ============================================================
def parse_park_filename(filename):
    """
    Parse Park AFM filename to extract metadata.
    
    Example: efm_after_writing_260116_EFM Phase_Forward_007.tiff
    
    Args:
        filename: Name of the file (with or without path)
        
    Returns:
        dict with keys: filename, experiment, channel, direction, frame, full_name
    """
    # Remove extension
    name = Path(filename).stem
    
    # Try to extract frame number (usually last digits after underscore)
    frame_match = re.search(r'_(\d{3})$', name)
    frame = int(frame_match.group(1)) if frame_match else None
    
    # Try to find direction (Forward/Backward)
    direction = None
    if '_Forward_' in name or name.endswith('_Forward'):
        direction = 'Forward'
    elif '_Backward_' in name or name.endswith('_Backward'):
        direction = 'Backward'
    
    # Try to identify channel
    detected_channel = None
    for ch in KNOWN_CHANNELS:
        if ch in name:
            detected_channel = ch
            break
    
    # Extract experiment name (typically at the start)
    parts = name.split('_')
    experiment = parts[0] if parts else 'Unknown'
    
    return {
        'filename': filename,
        'experiment': experiment,
        'channel': detected_channel,
        'direction': direction,
        'frame': frame,
        'full_name': name
    }


def get_tiff_files(folder_path):
    """
    Get all TIFF files in folder with parsed metadata.
    
    Args:
        folder_path: Path to the folder containing TIFF files
        
    Returns:
        list of dicts with file metadata and 'path' key
    """
    folder = Path(folder_path)
    if not folder.exists():
        return []
    
    tiff_files = list(folder.glob('*.tiff')) + list(folder.glob('*.tif'))
    tiff_files = sorted(tiff_files)
    
    # Parse metadata for each file
    files_with_meta = []
    for f in tiff_files:
        meta = parse_park_filename(f.name)
        meta['path'] = str(f)
        files_with_meta.append(meta)
    
    return files_with_meta


# ============================================================
# Data Query Functions
# ============================================================
def get_colormap_for_channel(channel):
    """
    Return appropriate colormap name for channel type.
    
    Args:
        channel: Channel name string
        
    Returns:
        str: colormap name
    """
    return CHANNEL_COLORS.get(channel, 'viridis')


def get_frame_identifier(file_meta):
    """
    Create unique identifier for a frame (experiment + frame number).
    
    Args:
        file_meta: dict with 'experiment' and 'frame' keys
        
    Returns:
        str or None if frame number is missing
    """
    if file_meta.get('frame') is not None:
        return f"{file_meta['experiment']}_{file_meta['frame']:03d}"
    return None


def get_all_channels_for_frame(files, experiment, frame, direction=None):
    """
    Get all channel files for a specific frame.
    
    Args:
        files: list of file metadata dicts
        experiment: experiment name to match
        frame: frame number to match
        direction: optional direction filter ('Forward', 'Backward', or None for both)
        
    Returns:
        list of matching file metadata dicts, sorted by channel name
    """
    matching = []
    for f in files:
        if (f['experiment'] == experiment and 
            f['frame'] == frame and
            f['channel'] is not None):
            if direction is None or f['direction'] == direction:
                matching.append(f)
    return sorted(matching, key=lambda x: x['channel'] or '')


def filter_files(files, channels=None, directions=None, experiments=None):
    """
    Filter files by channel, direction, and experiment.
    
    Args:
        files: list of file metadata dicts
        channels: list of channels to include (None = all)
        directions: list of directions to include (None = all)
        experiments: list of experiments to include (None = all)
        
    Returns:
        list of filtered file metadata dicts
    """
    filtered = []
    for f in files:
        # Check channel
        if channels is not None:
            if f['channel'] not in channels and f['channel'] is not None:
                continue
        
        # Check direction
        if directions is not None:
            if f['direction'] not in directions and f['direction'] is not None:
                continue
        
        # Check experiment
        if experiments is not None:
            if f['experiment'] not in experiments and f['experiment'] is not None:
                continue
        
        filtered.append(f)
    
    return filtered


def get_unique_values(files):
    """
    Extract unique channels, directions, and experiments from file list.
    
    Args:
        files: list of file metadata dicts
        
    Returns:
        dict with keys 'channels', 'directions', 'experiments' containing sorted lists
    """
    channels = sorted(set(f['channel'] for f in files if f['channel']))
    directions = sorted(set(f['direction'] for f in files if f['direction']))
    experiments = sorted(set(f['experiment'] for f in files if f['experiment']))
    
    return {
        'channels': channels,
        'directions': directions,
        'experiments': experiments
    }


def get_frames_info(files):
    """
    Get unique frame identifiers from file list.
    
    Args:
        files: list of file metadata dicts
        
    Returns:
        dict mapping (experiment, frame) tuples to info dicts
    """
    frames_info = {}
    for f in files:
        if f['frame'] is not None and f['experiment']:
            key = (f['experiment'], f['frame'])
            if key not in frames_info:
                frames_info[key] = {'experiment': f['experiment'], 'frame': f['frame']}
    return frames_info


# ============================================================
# Background Subtraction Functions
# ============================================================
BACKGROUND_METHODS = [
    'None',
    'Plane',
    'Line (Mean)',
    'Line (Median)',
    'Line (Polynomial)',
    'Polynomial 2D',
    'Fourier High-Pass'
]


def subtract_plane(data):
    """
    Subtract a best-fit plane from the image data.
    
    Uses least squares fitting to find the plane z = ax + by + c
    that best fits the data, then subtracts it.
    
    Args:
        data: 2D numpy array
        
    Returns:
        2D numpy array with plane subtracted
    """
    if data is None:
        return None
    
    rows, cols = data.shape
    
    # Create coordinate grids
    x = np.arange(cols)
    y = np.arange(rows)
    X, Y = np.meshgrid(x, y)
    
    # Flatten for least squares
    X_flat = X.flatten()
    Y_flat = Y.flatten()
    Z_flat = data.flatten()
    
    # Handle NaN values
    valid = ~np.isnan(Z_flat)
    if np.sum(valid) < 3:
        return data
    
    # Build design matrix [x, y, 1]
    A = np.column_stack([X_flat[valid], Y_flat[valid], np.ones(np.sum(valid))])
    
    # Solve least squares: A @ [a, b, c] = Z
    coeffs, _, _, _ = np.linalg.lstsq(A, Z_flat[valid], rcond=None)
    
    # Create fitted plane
    plane = coeffs[0] * X + coeffs[1] * Y + coeffs[2]
    
    return data - plane


def subtract_line_mean(data):
    """
    Subtract the mean of each row (line-by-line leveling).
    
    Args:
        data: 2D numpy array
        
    Returns:
        2D numpy array with row means subtracted
    """
    if data is None:
        return None
    
    row_means = np.nanmean(data, axis=1, keepdims=True)
    return data - row_means


def subtract_line_median(data):
    """
    Subtract the median of each row (line-by-line leveling).
    
    More robust to outliers than mean subtraction.
    
    Args:
        data: 2D numpy array
        
    Returns:
        2D numpy array with row medians subtracted
    """
    if data is None:
        return None
    
    row_medians = np.nanmedian(data, axis=1, keepdims=True)
    return data - row_medians


def subtract_line_polynomial(data, degree=1):
    """
    Fit and subtract a polynomial from each row.
    
    Args:
        data: 2D numpy array
        degree: polynomial degree (1=linear, 2=quadratic, etc.)
        
    Returns:
        2D numpy array with row polynomials subtracted
    """
    if data is None:
        return None
    
    rows, cols = data.shape
    result = np.copy(data)
    x = np.arange(cols)
    
    for i in range(rows):
        row = data[i, :]
        valid = ~np.isnan(row)
        
        if np.sum(valid) > degree:
            # Fit polynomial
            coeffs = np.polyfit(x[valid], row[valid], degree)
            fit = np.polyval(coeffs, x)
            result[i, :] = row - fit
    
    return result


def subtract_polynomial_2d(data, degree=2):
    """
    Fit and subtract a 2D polynomial surface from the image.
    
    Args:
        data: 2D numpy array
        degree: polynomial degree (2 = includes x², y², xy terms)
        
    Returns:
        2D numpy array with polynomial surface subtracted
    """
    if data is None:
        return None
    
    rows, cols = data.shape
    
    # Create coordinate grids
    x = np.arange(cols)
    y = np.arange(rows)
    X, Y = np.meshgrid(x, y)
    
    # Flatten
    X_flat = X.flatten()
    Y_flat = Y.flatten()
    Z_flat = data.flatten()
    
    # Handle NaN values
    valid = ~np.isnan(Z_flat)
    if np.sum(valid) < (degree + 1) ** 2:
        return data
    
    # Build design matrix for polynomial terms
    # For degree=2: [1, x, y, x², xy, y²]
    terms = []
    for i in range(degree + 1):
        for j in range(degree + 1 - i):
            terms.append((X_flat[valid] ** i) * (Y_flat[valid] ** j))
    
    A = np.column_stack(terms)
    
    # Solve least squares
    coeffs, _, _, _ = np.linalg.lstsq(A, Z_flat[valid], rcond=None)
    
    # Create fitted surface
    terms_full = []
    for i in range(degree + 1):
        for j in range(degree + 1 - i):
            terms_full.append((X ** i) * (Y ** j))
    
    surface = np.zeros_like(data)
    for coeff, term in zip(coeffs, terms_full):
        surface += coeff * term
    
    return data - surface


def subtract_fourier_highpass(data, cutoff_fraction=0.05):
    """
    Apply Fourier high-pass filter to remove low-frequency background.
    
    Args:
        data: 2D numpy array
        cutoff_fraction: fraction of frequency space to filter (0-1)
                        smaller = less aggressive filtering
        
    Returns:
        2D numpy array with low frequencies removed
    """
    if data is None:
        return None
    
    # Handle NaN by replacing with mean temporarily
    data_filled = np.copy(data)
    nan_mask = np.isnan(data_filled)
    if np.any(nan_mask):
        data_filled[nan_mask] = np.nanmean(data_filled)
    
    # Apply FFT
    fft_data = np.fft.fft2(data_filled)
    fft_shifted = np.fft.fftshift(fft_data)
    
    rows, cols = data.shape
    center_row, center_col = rows // 2, cols // 2
    
    # Create high-pass filter (Gaussian roll-off)
    y, x = np.ogrid[:rows, :cols]
    distance = np.sqrt((x - center_col) ** 2 + (y - center_row) ** 2)
    max_distance = np.sqrt(center_row ** 2 + center_col ** 2)
    cutoff_distance = cutoff_fraction * max_distance
    
    # Gaussian high-pass: 1 - exp(-d²/2σ²)
    sigma = cutoff_distance
    highpass_filter = 1 - np.exp(-(distance ** 2) / (2 * sigma ** 2 + 1e-10))
    
    # Apply filter
    fft_filtered = fft_shifted * highpass_filter
    
    # Inverse FFT
    result = np.real(np.fft.ifft2(np.fft.ifftshift(fft_filtered)))
    
    # Restore NaN values
    result[nan_mask] = np.nan
    
    return result


def apply_background_subtraction(data, method='None', **kwargs):
    """
    Apply the specified background subtraction method.
    
    Args:
        data: 2D numpy array
        method: one of BACKGROUND_METHODS
        **kwargs: additional arguments for specific methods
                  - degree: for polynomial methods (default 2)
                  - cutoff_fraction: for Fourier (default 0.05)
        
    Returns:
        2D numpy array with background subtracted
    """
    if data is None or method == 'None':
        return data
    
    if method == 'Plane':
        return subtract_plane(data)
    elif method == 'Line (Mean)':
        return subtract_line_mean(data)
    elif method == 'Line (Median)':
        return subtract_line_median(data)
    elif method == 'Line (Polynomial)':
        degree = kwargs.get('degree', 1)
        return subtract_line_polynomial(data, degree=degree)
    elif method == 'Polynomial 2D':
        degree = kwargs.get('degree', 2)
        return subtract_polynomial_2d(data, degree=degree)
    elif method == 'Fourier High-Pass':
        cutoff = kwargs.get('cutoff_fraction', 0.05)
        return subtract_fourier_highpass(data, cutoff_fraction=cutoff)
    else:
        return data


# ============================================================
# Statistics Functions
# ============================================================
def get_image_stats(data):
    """
    Calculate basic statistics for image data.
    
    Args:
        data: numpy array of image data
        
    Returns:
        dict with 'shape', 'min', 'max', 'mean' keys
    """
    if data is None:
        return None
    
    return {
        'shape': data.shape,
        'min': np.nanmin(data),
        'max': np.nanmax(data),
        'mean': np.nanmean(data)
    }


def get_display_range(data, percentile_low=1, percentile_high=99):
    """
    Calculate display range using percentiles to handle outliers.
    
    Args:
        data: numpy array of image data
        percentile_low: lower percentile for vmin
        percentile_high: upper percentile for vmax
        
    Returns:
        tuple: (vmin, vmax)
    """
    if data is None:
        return 0, 1
    
    vmin = np.nanpercentile(data, percentile_low)
    vmax = np.nanpercentile(data, percentile_high)
    return vmin, vmax


# ============================================================
# Visualization Functions (for export)
# ============================================================
def create_figure_with_scalebar(data, title="", colormap='viridis', 
                                 scan_size_um=10.0, show_colorbar=True,
                                 figsize=(4, 4)):
    """
    Create matplotlib figure with scale bar for TIFF data.
    
    Args:
        data: numpy array of image data
        title: title for the figure
        colormap: matplotlib colormap name
        scan_size_um: scan size in micrometers (for scale bar)
        show_colorbar: whether to show colorbar
        figsize: figure size tuple
        
    Returns:
        matplotlib Figure object
    """
    import matplotlib.pyplot as plt
    from matplotlib_scalebar.scalebar import ScaleBar
    
    fig, ax = plt.subplots(figsize=figsize)
    
    vmin, vmax = get_display_range(data)
    im = ax.imshow(data, cmap=colormap, vmin=vmin, vmax=vmax, origin='upper')
    
    if show_colorbar:
        cbar = plt.colorbar(im, ax=ax, shrink=0.8)
        cbar.ax.tick_params(labelsize=8)
    
    # Calculate pixel size in micrometers
    pixels = data.shape[0]
    pixel_size_um = scan_size_um / pixels
    
    # Add scale bar (convert um to meters for ScaleBar)
    scalebar = ScaleBar(
        pixel_size_um * 1e-6,  # pixel size in meters
        "m",
        length_fraction=0.25,
        location='lower right',
        color='white',
        box_color='black',
        box_alpha=0.5,
        font_properties={'size': 8}
    )
    ax.add_artist(scalebar)
    
    ax.set_title(title, fontsize=10)
    ax.axis('off')
    plt.tight_layout()
    
    return fig


def create_multi_channel_figure(channel_data_list, scan_size_um=10.0, 
                                 cols_per_row=4, auto_colormap=True,
                                 fallback_colormap='viridis',
                                 bg_settings=None, bg_params=None,
                                 color_scale_settings=None):
    """
    Create a figure with multiple channel images for a single frame.
    
    Args:
        channel_data_list: list of dicts with 'data', 'channel', 'direction', 'colormap' keys
        scan_size_um: scan size in micrometers
        cols_per_row: number of columns in the grid
        auto_colormap: use channel-specific colormaps
        bg_settings: dict mapping channel names to background subtraction methods
        bg_params: dict with 'degree' and 'cutoff_fraction' for advanced params
        color_scale_settings: dict with 'mode', 'percentile_low', 'percentile_high', 
                              'scale_factor', 'center_offset'
        
    Returns:
        matplotlib Figure object
    """
    import matplotlib.pyplot as plt
    from matplotlib_scalebar.scalebar import ScaleBar
    
    bg_settings = bg_settings or {}
    bg_params = bg_params or {'degree': 2, 'cutoff_fraction': 0.05}
    color_scale_settings = color_scale_settings or {'mode': 'auto', 'percentile_low': 1.0, 'percentile_high': 99.0}
    
    n_channels = len(channel_data_list)
    if n_channels == 0:
        return None
    
    rows = (n_channels + cols_per_row - 1) // cols_per_row
    
    fig, axes = plt.subplots(rows, cols_per_row, figsize=(cols_per_row * 3, rows * 3))
    
    # Ensure axes is always 2D array
    if rows == 1 and cols_per_row == 1:
        axes = np.array([[axes]])
    elif rows == 1:
        axes = axes.reshape(1, -1)
    elif cols_per_row == 1:
        axes = axes.reshape(-1, 1)
    
    for idx, ch_info in enumerate(channel_data_list):
        row = idx // cols_per_row
        col = idx % cols_per_row
        ax = axes[row, col]
        
        data = ch_info['data']
        channel = ch_info.get('channel', 'Data')
        direction = ch_info.get('direction', '')
        
        # Apply background subtraction if specified
        bg_method = bg_settings.get(channel, 'None')
        if bg_method != 'None':
            data = apply_background_subtraction(
                data, bg_method, 
                degree=bg_params.get('degree', 2),
                cutoff_fraction=bg_params.get('cutoff_fraction', 0.05)
            )
        
        if auto_colormap:
            cmap = get_colormap_for_channel(channel)
        else:
            cmap = fallback_colormap
        
        # Calculate color scale limits based on settings
        if color_scale_settings.get('mode') == 'manual':
            base_vmin = np.nanpercentile(data, 1)
            base_vmax = np.nanpercentile(data, 99)
            data_range = base_vmax - base_vmin
            center = (base_vmin + base_vmax) / 2
            center += color_scale_settings.get('center_offset', 0) * data_range
            half_range = (data_range / 2) * color_scale_settings.get('scale_factor', 1.0)
            vmin = center - half_range
            vmax = center + half_range
        else:
            vmin = np.nanpercentile(data, color_scale_settings.get('percentile_low', 1.0))
            vmax = np.nanpercentile(data, color_scale_settings.get('percentile_high', 99.0))
        
        im = ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax, origin='upper')
        
        plt.colorbar(im, ax=ax, shrink=0.8)
        
        # Add scale bar
        pixels = data.shape[0]
        pixel_size_um = scan_size_um / pixels
        scalebar = ScaleBar(
            pixel_size_um * 1e-6,
            "m",
            length_fraction=0.25,
            location='lower right',
            color='white',
            box_color='black',
            box_alpha=0.5,
            font_properties={'size': 6}
        )
        ax.add_artist(scalebar)
        
        # Title with BG method indicator
        title = f"{channel}\n{direction}"
        if bg_method != 'None':
            title += f"\n[{bg_method}]"
        ax.set_title(title, fontsize=8)
        ax.axis('off')
    
    # Hide empty subplots
    for idx in range(n_channels, rows * cols_per_row):
        row = idx // cols_per_row
        col = idx % cols_per_row
        axes[row, col].axis('off')
    
    plt.tight_layout()
    return fig


# ============================================================
# PowerPoint Export Functions
# ============================================================
def export_frame_to_pptx(prs, frame_channels, experiment, frame_num,
                          scan_size_um=10.0, cols_per_row=4, 
                          auto_colormap=True, excluded_channels=None,
                          show_metadata=True, bg_settings=None, bg_params=None,
                          color_scale_settings=None, fallback_colormap='viridis'):
    """
    Add a slide to PowerPoint presentation with all channels for a frame.
    
    Args:
        prs: python-pptx Presentation object
        frame_channels: list of file metadata dicts for channels
        experiment: experiment name
        frame_num: frame number
        scan_size_um: scan size in micrometers (None = auto from metadata)
        cols_per_row: columns per row in the grid
        auto_colormap: use channel-specific colormaps
        excluded_channels: list of channel names to exclude
        show_metadata: include metadata info on slide
        bg_settings: dict mapping channel names to background subtraction methods
        bg_params: dict with 'degree' and 'cutoff_fraction' for advanced params
        color_scale_settings: dict with color scale parameters
        
    Returns:
        The slide object
    """
    from pptx.util import Inches, Pt
    from io import BytesIO
    import matplotlib.pyplot as plt
    
    excluded_channels = excluded_channels or []
    bg_settings = bg_settings or {}
    bg_params = bg_params or {'degree': 2, 'cutoff_fraction': 0.05}
    color_scale_settings = color_scale_settings or {'mode': 'auto', 'percentile_low': 1.0, 'percentile_high': 99.0}
    
    # Filter out excluded channels
    channels_to_export = [
        f for f in frame_channels 
        if f['channel'] not in excluded_channels
    ]
    
    if not channels_to_export:
        return None
    
    # Get scan size from metadata if not provided
    actual_scan_size = scan_size_um
    metadata_info = None
    if channels_to_export:
        first_file = channels_to_export[0]
        metadata = extract_tiff_metadata(first_file['path'])
        if metadata:
            metadata_info = metadata
            if scan_size_um is None and metadata.get('scan_size_um'):
                actual_scan_size = metadata['scan_size_um']
    
    if actual_scan_size is None:
        actual_scan_size = 10.0  # fallback default
    
    # Load data for each channel
    channel_data_list = []
    filenames = []
    for f in channels_to_export:
        data = load_tiff(f['path'])
        if data is not None:
            channel_data_list.append({
                'data': data,
                'channel': f['channel'],
                'direction': f['direction'] or '',
                'filename': f.get('filename', Path(f['path']).name)
            })
            filenames.append(Path(f['path']).name)
    
    if not channel_data_list:
        return None
    
    # Create multi-channel figure
    fig = create_multi_channel_figure(
        channel_data_list, 
        scan_size_um=actual_scan_size,
        cols_per_row=cols_per_row,
        auto_colormap=auto_colormap,
        fallback_colormap=fallback_colormap,
        bg_settings=bg_settings,
        bg_params=bg_params,
        color_scale_settings=color_scale_settings
    )
    
    if fig is None:
        return None
    
    # Save figure to bytes buffer
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    # Add slide with blank layout
    blank_layout = prs.slide_layouts[6]  # Blank layout
    slide = prs.slides.add_slide(blank_layout)
    
    # Add title with experiment and frame
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    
    title_box = slide.shapes.add_textbox(Inches(0.3), Inches(0.1), Inches(9.4), Inches(0.4))
    title_frame = title_box.text_frame
    title_para = title_frame.paragraphs[0]
    title_para.text = f"{experiment} - Frame {frame_num:03d}"
    title_para.font.size = Pt(20)
    title_para.font.bold = True
    
    # Add the figure image
    slide.shapes.add_picture(buf, Inches(0.2), Inches(0.55), width=Inches(9.6))
    
    # Add metadata/filename info at bottom
    if show_metadata:
        # Build info string
        info_parts = []
        if metadata_info:
            if metadata_info.get('datetime'):
                info_parts.append(f"Date: {metadata_info['datetime']}")
            if metadata_info.get('scan_size_um'):
                info_parts.append(f"Scan: {metadata_info['scan_size_um']:.1f} µm")
            if metadata_info.get('scan_rate_hz'):
                info_parts.append(f"Rate: {metadata_info['scan_rate_hz']:.1f} Hz")
        
        # Add first filename as reference
        if filenames:
            base_name = filenames[0].rsplit('_', 2)[0] if '_' in filenames[0] else filenames[0]
            info_parts.append(f"File: {base_name}")
        
        info_text = "  |  ".join(info_parts)
        
        info_box = slide.shapes.add_textbox(Inches(0.3), Inches(7.1), Inches(9.4), Inches(0.3))
        info_frame = info_box.text_frame
        info_para = info_frame.paragraphs[0]
        info_para.text = info_text
        info_para.font.size = Pt(10)
        info_para.font.color.rgb = RGBColor(100, 100, 100)
    
    return slide


def export_to_powerpoint(files, output_path, scan_size_um=None, 
                          cols_per_row=4, auto_colormap=True,
                          excluded_channels=None, direction_filter=None,
                          progress_callback=None, show_metadata=True,
                          auto_scan_size=True, bg_settings=None, bg_params=None,
                          color_scale_settings=None, fallback_colormap='viridis'):
    """
    Export frames to PowerPoint presentation.
    
    Args:
        files: list of file metadata dicts
        output_path: path for output .pptx file
        scan_size_um: scan size in micrometers (None = auto from metadata)
        cols_per_row: columns per row in channel grid
        auto_colormap: use channel-specific colormaps
        excluded_channels: list of channel names to exclude
        direction_filter: 'Forward', 'Backward', or None for both
        progress_callback: optional callback function(current, total) for progress
        show_metadata: include metadata info on slides
        auto_scan_size: automatically extract scan size from TIFF metadata
        bg_settings: dict mapping channel names to background subtraction methods
        bg_params: dict with 'degree' and 'cutoff_fraction' for advanced params
        color_scale_settings: dict with color scale parameters
        
    Returns:
        tuple: (success: bool, message: str, num_slides: int)
    """
    from pptx import Presentation
    from pptx.util import Inches
    
    bg_settings = bg_settings or {}
    bg_params = bg_params or {'degree': 2, 'cutoff_fraction': 0.05}
    color_scale_settings = color_scale_settings or {'mode': 'auto', 'percentile_low': 1.0, 'percentile_high': 99.0}
    
    try:
        # Create presentation with widescreen dimensions
        prs = Presentation()
        prs.slide_width = Inches(10)
        prs.slide_height = Inches(7.5)
        
        # Get unique frames
        frames_info = get_frames_info(files)
        
        if not frames_info:
            return False, "No frames found in the file list", 0
        
        total_frames = len(frames_info)
        slides_added = 0
        
        # Determine scan size to use
        effective_scan_size = scan_size_um
        if auto_scan_size and scan_size_um is None:
            effective_scan_size = None  # Will be extracted per-frame
        
        for idx, (key, info) in enumerate(sorted(frames_info.items())):
            experiment = info['experiment']
            frame_num = info['frame']
            
            # Get channels for this frame
            frame_channels = get_all_channels_for_frame(
                files, experiment, frame_num, direction_filter
            )
            
            if frame_channels:
                slide = export_frame_to_pptx(
                    prs, frame_channels, experiment, frame_num,
                    scan_size_um=effective_scan_size,
                    cols_per_row=cols_per_row,
                    auto_colormap=auto_colormap,
                    excluded_channels=excluded_channels,
                    show_metadata=show_metadata,
                    bg_settings=bg_settings,
                    bg_params=bg_params,
                    color_scale_settings=color_scale_settings,
                    fallback_colormap=fallback_colormap
                )
                if slide:
                    slides_added += 1
            
            # Report progress
            if progress_callback:
                progress_callback(idx + 1, total_frames)
        
        if slides_added == 0:
            return False, "No slides were created (no valid data)", 0
        
        # Save presentation
        prs.save(output_path)
        return True, f"Successfully exported {slides_added} slides", slides_added
        
    except Exception as e:
        return False, f"Export failed: {str(e)}", 0


# ============================================================
# External Tool Integration
# ============================================================
# Common install locations to check before falling back to PATH lookup.
_GWYDDION_CANDIDATES = {
    "Darwin": ["/Applications/Gwyddion.app"],
    "Windows": [
        r"C:\Program Files (x86)\Gwyddion\bin\gwyddion.exe",
        r"C:\Program Files\Gwyddion\bin\gwyddion.exe",
    ],
    "Linux": ["/usr/bin/gwyddion", "/usr/local/bin/gwyddion"],
}


def find_gwyddion_executable():
    """
    Try to locate a Gwyddion install for the current OS.

    Returns the path to the executable (or, on macOS, the .app bundle),
    or None if it could not be found automatically.
    """
    system = platform.system()

    for candidate in _GWYDDION_CANDIDATES.get(system, []):
        if os.path.exists(candidate):
            return candidate

    on_path = shutil.which("gwyddion")
    if on_path:
        return on_path

    return None


def copy_to_clipboard(text):
    """
    Copy text to the system clipboard using OS-native command-line tools
    (no extra Python dependency). Only meaningful when the Streamlit
    server runs on the same machine as the user, i.e. local use.

    Returns:
        (success, message) tuple.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        elif system == "Windows":
            subprocess.run(["clip"], input=text.encode("utf-8"), check=True)
        else:
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode("utf-8"),
                    check=True,
                )
            except (FileNotFoundError, subprocess.CalledProcessError):
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode("utf-8"),
                    check=True,
                )
        return True, "Copied to clipboard"
    except (OSError, subprocess.CalledProcessError) as e:
        return False, f"Could not copy to clipboard: {e}"


def open_in_gwyddion(file_path, gwyddion_path=None):
    """
    Launch Gwyddion with the given file already open.

    Args:
        file_path: Path to the TIFF (or other Gwyddion-readable) file.
        gwyddion_path: Optional explicit path to the Gwyddion executable
            or (on macOS) .app bundle. Auto-detected if not provided.

    Returns:
        (success, message) tuple.
    """
    if not os.path.isfile(file_path):
        return False, f"File not found: {file_path}"

    gwyddion_path = gwyddion_path or find_gwyddion_executable()
    if not gwyddion_path:
        return False, (
            "Could not find Gwyddion. Install it or set the path manually "
            "in the sidebar."
        )

    system = platform.system()
    try:
        if system == "Darwin" and gwyddion_path.endswith(".app"):
            subprocess.Popen(["open", "-a", gwyddion_path, file_path])
        else:
            subprocess.Popen([gwyddion_path, file_path])
        return True, f"Opened {os.path.basename(file_path)} in Gwyddion"
    except OSError as e:
        return False, f"Failed to launch Gwyddion: {e}"
