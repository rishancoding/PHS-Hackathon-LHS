"""Clearly labeled synthetic GeoTIFFs for offline demonstrations and integration tests."""

from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin

from process_solar_layers import metric_grid, process_layers
from solar_address import solar_parameters


def create_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    request = {
        "address": "Synthetic neighborhood — demo data", "state": "CA", "latitude": 37.422, "longitude": -122.084,
        "radius_meters": 100, "radius_feet": 100 / .3048,
        "solar_parameters": solar_parameters(37.422, -122.084, 100),
    }
    grid = metric_grid(37.422, -122.084, 100, 1)
    size = grid["width"]
    mask = np.zeros((size, size), dtype=np.uint8)
    rgb = np.full((3, size, size), 0, dtype=np.uint8)
    rgb[:] = np.array([87, 122, 90])[:, None, None]
    rgb[:, :, 96:105] = 145
    rgb[:, 96:104, :] = 145
    annual = np.full((size, size), 900, dtype=np.float32)
    dsm = np.full((size, size), 12, dtype=np.float32)
    rectangles = [(30, 55, 35, 60, 1450), (30, 52, 120, 149, 1250),
                  (120, 152, 30, 60, 1600), (122, 148, 120, 150, 1350),
                  (80, 92, 151, 177, 1050), (20, 38, 80, 93, 1100)]
    for top, bottom, left, right, flux in rectangles:
        mask[top:bottom, left:right] = 1
        rgb[:, top:bottom, left:right] = np.array([195, 174, 151])[:, None, None]
        annual[top:bottom, left:right] = flux
        dsm[top:bottom, left:right] = 18
    paths = {}
    for name, array in (("rgb", rgb), ("mask", mask[None]), ("annual_flux", annual[None]), ("dsm", dsm[None])):
        paths[name] = directory / f"{name}.tif"
        with rasterio.open(paths[name], "w", driver="GTiff", height=size, width=size,
                           count=array.shape[0], dtype=array.dtype, crs=grid["crs"],
                           transform=grid["transform"], nodata=None if array.dtype == np.uint8 else -9999) as output:
            output.write(array)
    return request, paths


def run_demo(directory, progress=lambda message: None):
    request, paths = create_demo(Path(directory) / "layers")
    request["solar_parameters"]["pixelSizeMeters"] = 1
    return process_layers(paths, request, directory, progress=progress, demo=True)


if __name__ == "__main__":
    print(run_demo("outputs/demo")["summary"])
