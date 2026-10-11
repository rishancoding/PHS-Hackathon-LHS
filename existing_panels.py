"""Color-based screening for possible existing solar panels on detected roofs.

Not a trained model: blue-tinted, mid-brightness, panel-shaped areas inside Google's
rooftop mask are flagged so new layouts avoid them. Tuned on 0.1 m Google Solar imagery.
"""

import math

import numpy as np
from rasterio.features import shapes
from scipy import ndimage
from shapely.geometry import shape

DETECTOR = "Color screening: blue-tinted, panel-shaped areas on roofs (not a trained model)"
MIN_BLUE_MINUS_RED = 20  # panels read slate blue; plain roofs and asphalt are nearer gray
MIN_BLUE_MINUS_GREEN = 13
MIN_BRIGHTNESS = 95  # darker blue areas are mostly shadows
MAX_BRIGHTNESS = 210  # lighter canopy panels read up to ~180
MIN_AREA_M2 = 4  # roughly two modules
MIN_WIDTH_M = 1.8
MIN_RECTANGULAR_FILL = 0.5  # area / minimum rotated rectangle
MIN_TEXTURE = 12  # module grid lines; shadows and plain surfaces are smooth
TEXTURE_MAX_RESOLUTION_M = 0.15


def detect_existing_panels(rgb, roof_labels, resolution, resampled=False):
    """Return [(pixel_polygon, roof_label, area_m2)] for possible existing panel areas.

    `resampled` means the photo was shrunk from finer source pixels (a memory-capped grid):
    smoothing fades the module grid lines the texture check needs, so it is skipped, as it is
    for coarse imagery.
    """
    if not np.isfinite(rgb).any():
        return []
    red, green, blue = (np.nan_to_num(band) for band in rgb)
    brightness = (red + green + blue) / 3
    window = max(3, round(1.0 / resolution))
    # Judge ~1 m neighborhoods, not single pixels, so module frames don't break up arrays.
    local_brightness = ndimage.uniform_filter(brightness, window)
    candidate = (
        (roof_labels > 0)
        & (ndimage.uniform_filter(blue - red, window) >= MIN_BLUE_MINUS_RED)
        & (ndimage.uniform_filter(blue - green, window) >= MIN_BLUE_MINUS_GREEN)
        & (local_brightness >= MIN_BRIGHTNESS)
        & (local_brightness <= MAX_BRIGHTNESS)
    )
    if not candidate.any():
        return []  # Most areas have none; skip the whole-image cleanup below.
    candidate = ndimage.binary_opening(candidate, np.ones((window, window)))
    candidate = ndimage.binary_closing(candidate, np.ones((max(3, window // 2),) * 2))
    if not candidate.any():
        return []
    texture = np.abs(brightness - ndimage.uniform_filter(brightness, 5))
    labels, _ = ndimage.label(candidate)
    boxes = ndimage.find_objects(labels)
    found = []
    for geometry, value in shapes(labels.astype(np.int32), mask=candidate):
        polygon = shape(geometry)
        area_m2 = polygon.area * resolution**2
        if area_m2 < MIN_AREA_M2:
            continue
        rectangle = polygon.minimum_rotated_rectangle
        corners = list(rectangle.exterior.coords)
        width_m = min(math.dist(corners[0], corners[1]), math.dist(corners[1], corners[2])) * resolution
        if width_m < MIN_WIDTH_M or polygon.area / max(rectangle.area, 1e-9) < MIN_RECTANGULAR_FILL:
            continue
        box = boxes[int(value) - 1]
        inside = labels[box] == int(value)
        if not resampled and resolution <= TEXTURE_MAX_RESOLUTION_M and texture[box][inside].mean() < MIN_TEXTURE:
            continue
        roofs = roof_labels[box][inside]
        roof_label = int(np.bincount(roofs[roofs > 0]).argmax())
        # Neighborhood smoothing trims about half a window from each edge; restore it so
        # new layouts don't clip the real array's border.
        polygon = polygon.buffer(window / 2, join_style="mitre")
        found.append((polygon, roof_label, polygon.area * resolution**2))
    return found
