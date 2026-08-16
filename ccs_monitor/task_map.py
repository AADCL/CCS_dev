from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .models import PgmMapMetadata
from .pgm_map import PgmMapLoader


class GridPointValidator:
    def __init__(self, metadata: PgmMapMetadata, image_path: str | Path) -> None:
        self.metadata = metadata
        self.pixels = PgmMapLoader().load_pgm(image_path)

    def cell(self, x: float, y: float) -> tuple[int, int] | None:
        dx, dy = x - self.metadata.origin_x, y - self.metadata.origin_y
        cosine, sine = math.cos(self.metadata.origin_yaw), math.sin(self.metadata.origin_yaw)
        local_x = cosine * dx + sine * dy
        local_y = -sine * dx + cosine * dy
        column = int(math.floor(local_x / self.metadata.resolution))
        row_from_bottom = int(math.floor(local_y / self.metadata.resolution))
        row = self.metadata.image_height - 1 - row_from_bottom
        if not 0 <= column < self.metadata.image_width or not 0 <= row < self.metadata.image_height:
            return None
        return row, column

    def is_free(self, x: float, y: float) -> bool:
        cell = self.cell(x, y)
        if cell is None:
            return False
        pixel = float(self.pixels[cell]) / 255.0
        occupancy = pixel if self.metadata.negate else 1.0 - pixel
        return occupancy <= self.metadata.free_thresh
