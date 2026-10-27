"""Spatial presets for the highway incident console."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PRESET_DIR = Path(__file__).resolve().parent / "presets"


def load_presets() -> dict[str, dict[str, Any]]:
    presets: dict[str, dict[str, Any]] = {}
    for path in sorted(PRESET_DIR.glob("*.json")):
        presets[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return presets


def preset_points(preset: dict[str, Any], width: int, height: int) -> list[list[float]]:
    points: list[list[float]] = []
    for fraction_x, fraction_y in preset["points_fraction"]:
        x = min(int(float(fraction_x) * width), width - 1)
        y = min(int(float(fraction_y) * height), height - 1)
        points.append([float(max(0, x)), float(max(0, y))])
    return points
