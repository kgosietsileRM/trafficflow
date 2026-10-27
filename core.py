"""Shared, self-contained video analytics for the stand-alone demo apps.

This module is intentionally copied into each demo folder. The demos never
import from the SDK source tree; each environment installs the published
``spe-engine`` package from PyPI.
"""

from __future__ import annotations

import json
import math
import shutil
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from spe import (
    LineCrossingCounter,
    ModelAgnosticTracker,
    SpatialEngine,
    SPEClient,
    ZoneOccupancyTracker,
    summarize_tracks,
)

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
LINK_FILE = ASSETS_DIR / "link.json"
DEFAULT_ROWS = 2
DEFAULT_COLUMNS = 3
STOP_LINE_Y = 420.0
QUEUE_Y = 400.0

FrameCallback = Callable[
    [int, np.ndarray, list[dict[str, Any]], dict[str, Any], dict[str, Any]], None
]


@dataclass
class VideoInfo:
    """Basic metadata for one media source."""

    path: Path | str
    width: int
    height: int
    fps: float
    total_frames: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 3),
            "total_frames": self.total_frames,
        }


@dataclass
class AnalysisResult:
    """A serializable analysis report plus the last annotated frame."""

    report: dict[str, Any]
    last_frame: np.ndarray | None = None
    frames_seen: list[np.ndarray] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.report


def load_links(path: Path = LINK_FILE) -> dict[str, str]:
    """Load the media link table, dropping empty entries."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return {str(key): str(value) for key, value in data.items() if value}


def _cache_name(url: str) -> str:
    name = Path(urllib.parse.urlparse(url).path).name
    return name or "downloaded-video.mp4"


def resolve_media(key: str, links: dict[str, str] | None = None, *, download: bool = True) -> Path:
    """Resolve a link key to a local path, downloading remote media on demand."""
    table = links if links is not None else load_links()
    if key not in table:
        raise KeyError(f"Unknown media key {key!r}. Available: {', '.join(sorted(table))}")
    value = table[key]
    if value.startswith(("http://", "https://")):
        if not download:
            raise ValueError(f"{key!r} is a remote source; download it first")
        cache = ASSETS_DIR / ".cache"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / _cache_name(value)
        if not target.exists():
            partial = target.with_suffix(target.suffix + ".part")
            with (
                urllib.request.urlopen(value, timeout=60) as response,
                partial.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle)
            partial.replace(target)
        return target
    return (ASSETS_DIR / value).resolve()


def probe(path: Path | str) -> VideoInfo:
    """Read video dimensions, frame rate, and frame count."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {path}")
    try:
        return VideoInfo(
            path=path,
            width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(capture.get(cv2.CAP_PROP_FPS)) or 25.0,
            total_frames=max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT))),
        )
    finally:
        capture.release()


def calibration_points(width: int, height: int) -> list[list[float]]:
    """Return a broad road-plane ROI in source-pixel coordinates."""
    return [
        [float(int(width * 0.16)), float(int(height * 0.23))],
        [float(int(width * 0.91)), float(int(height * 0.22))],
        [float(width - 1), float(height - 1)],
        [0.0, float(height - 1)],
    ]


def build_engine(
    width: int,
    height: int,
    *,
    rows: int = DEFAULT_ROWS,
    columns: int = DEFAULT_COLUMNS,
    points: Sequence[Sequence[float]] | None = None,
) -> SpatialEngine:
    """Build and calibrate a spatial engine for a frame size."""
    engine = SpatialEngine(
        grid_rows=rows,
        grid_columns=columns,
        source_dimensions={"width": width, "height": height},
    )
    engine.calibrate(
        points if points is not None else calibration_points(width, height),
        width,
        height,
        rows=rows,
        columns=columns,
    )
    return engine


class MotionDetector:
    """Local, model-free detector based on background subtraction."""

    def __init__(self, width: int, height: int, *, min_area: int | None = None) -> None:
        self._background = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=25, detectShadows=False
        )
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._min_area = (
            min_area if min_area is not None else max(300, int(width * height * 0.0015))
        )
        self._index = 0

    def detect(
        self, frame: np.ndarray, _frame_index: int, _timestamp: float
    ) -> list[dict[str, Any]]:
        mask = self._background.apply(frame)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.dilate(mask, self._kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[dict[str, Any]] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self._min_area:
                continue
            x, y, width, height = cv2.boundingRect(contour)
            if width < 12 or height < 12:
                continue
            self._index += 1
            detections.append(
                {
                    "id": f"motion-{self._index}",
                    "class_name": "vehicle",
                    "confidence": round(
                        min(0.95, 0.4 + area / (frame.shape[0] * frame.shape[1])), 3
                    ),
                    "bbox": {
                        "x": float(x),
                        "y": float(y),
                        "width": float(width),
                        "height": float(height),
                    },
                }
            )
        return detections

    def close(self) -> None:
        """Release detector resources."""


class ServerDetector:
    """Detector backed by a running SPE engine's detect-frame endpoint."""

    def __init__(
        self,
        url: str,
        model_id: str,
        *,
        confidence: float = 0.4,
        timeout: float = 30.0,
    ) -> None:
        self._client = SPEClient(url, timeout=timeout)
        self._model_id = model_id
        self._confidence = confidence

    def detect(self, frame: np.ndarray, frame_index: int, timestamp: float) -> list[dict[str, Any]]:
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return []
        result = self._client.detect_frame(
            buffer.tobytes(),
            filename=f"frame-{frame_index:06d}.jpg",
            model_id=self._model_id,
            confidence=self._confidence,
            frame_index=frame_index,
            timestamp=timestamp,
        )
        return list(result.get("detections") or [])

    def close(self) -> None:
        self._client.close()


def build_detector(
    mode: str,
    *,
    width: int,
    height: int,
    url: str,
    model: str,
    confidence: float,
) -> MotionDetector | ServerDetector:
    """Create the detector selected by ``mode`` (``local`` or ``server``)."""
    if mode == "local":
        return MotionDetector(width, height)
    if mode == "server":
        return ServerDetector(url, model, confidence=confidence)
    raise ValueError(f"Unsupported detection mode: {mode!r}")


def frames(
    capture: cv2.VideoCapture, source_fps: float, sample_fps: float
) -> Iterator[tuple[int, float, np.ndarray]]:
    """Yield sampled ``(frame_index, timestamp, frame)`` tuples."""
    step = max(1, round(source_fps / sample_fps)) if sample_fps > 0 else 1
    raw_index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            return
        if raw_index % step == 0:
            yield raw_index, raw_index / source_fps, frame
        raw_index += 1


def lane_letters(columns: int) -> list[str]:
    """Return spreadsheet-style column labels for lanes."""
    return [chr(ord("A") + index) for index in range(columns)]


def lane_snapshot(
    spatial: Sequence[dict[str, Any]],
    counters: dict[str, LineCrossingCounter],
    columns: int,
) -> dict[str, dict[str, Any]]:
    """Build per-lane queue, occupancy, and crossing stats from spatial data."""
    payload: dict[str, dict[str, Any]] = {}
    for index, letter in enumerate(lane_letters(columns)):
        positions: list[dict[str, Any]] = []
        for detection in spatial:
            spatial_data = detection.get("spatial")
            if not isinstance(spatial_data, dict) or spatial_data.get("status") != "valid":
                continue
            cell = spatial_data.get("grid_cell")
            if not isinstance(cell, dict) or int(cell.get("column", -1)) != index:
                continue
            position = spatial_data.get("position")
            if isinstance(position, dict):
                positions.append(position)
        payload[letter] = {
            "occupancy": len(positions),
            "queue": sum(1 for item in positions if float(item.get("y", 0.0)) >= QUEUE_Y),
            "crossings": counters[letter].snapshot()["total"] if letter in counters else 0,
        }
    return payload


def annotate(
    engine: SpatialEngine,
    frame: np.ndarray,
    spatial: Sequence[dict[str, Any]],
    counters: dict[str, LineCrossingCounter],
) -> np.ndarray:
    """Draw calibrated zones, stop lines, and detection boxes onto a copy."""
    canvas = frame.copy()
    height, width = canvas.shape[:2]
    colors = ((90, 220, 120), (80, 180, 255), (255, 170, 90), (210, 130, 255))
    for index, (zone, polygon) in enumerate(engine.zone_polygons().items()):
        points = np.asarray(polygon, dtype=np.int32)
        if points.shape[0] < 3:
            continue
        points[:, 0] = np.clip(points[:, 0], 0, width - 1)
        points[:, 1] = np.clip(points[:, 1], 0, height - 1)
        color = colors[index % len(colors)]
        cv2.polylines(canvas, [points], True, color, 2, cv2.LINE_AA)
        label_at = points[0]
        cv2.putText(
            canvas,
            zone,
            (int(label_at[0]) + 4, int(label_at[1]) + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            1,
            cv2.LINE_AA,
        )
    for letter, counter in counters.items():
        start, end = counter.start, counter.end
        image_line = engine.reference_to_image([list(start), list(end)])
        p1 = (int(image_line[0][0]), int(image_line[0][1]))
        p2 = (int(image_line[1][0]), int(image_line[1][1]))
        cv2.line(canvas, p1, p2, (60, 230, 255), 2, cv2.LINE_AA)
        cv2.putText(
            canvas,
            f"lane {letter}",
            (p1[0] + 4, p1[1] - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (60, 230, 255),
            1,
            cv2.LINE_AA,
        )
    for detection in spatial:
        bbox = detection.get("bbox")
        if not isinstance(bbox, dict):
            continue
        x = int(bbox["x"])
        y = int(bbox["y"])
        w = int(bbox["width"])
        h = int(bbox["height"])
        spatial_data = detection.get("spatial") or {}
        zone = spatial_data.get("zone") if isinstance(spatial_data, dict) else None
        valid = isinstance(spatial_data, dict) and spatial_data.get("status") == "valid"
        color = (110, 230, 110) if valid else (90, 90, 245)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)
        label = f"{detection.get('track_id') or detection.get('id') or 'object'} {zone or '-'}"
        cv2.putText(
            canvas,
            label,
            (x, max(14, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )
    return canvas


def empty_engine_state() -> dict[str, Any]:
    """Return the default empty summary used when analysis cannot run."""
    return {"occupancy": {"active_tracks": 0, "zones": {}}, "lanes": {}, "tracks": []}


def analyze(
    path: Path | str,
    *,
    mode: str = "local",
    max_frames: int = 60,
    sample_fps: float = 5.0,
    rows: int = DEFAULT_ROWS,
    columns: int = DEFAULT_COLUMNS,
    url: str = "http://127.0.0.1:5000",
    model: str = "model-a",
    confidence: float = 0.4,
    points: Sequence[Sequence[float]] | None = None,
    on_frame: FrameCallback | None = None,
) -> AnalysisResult:
    """Analyze a video and return a JSON-ready report."""
    info = probe(path)
    engine = build_engine(info.width, info.height, rows=rows, columns=columns, points=points)
    detector = build_detector(
        mode,
        width=info.width,
        height=info.height,
        url=url,
        model=model,
        confidence=confidence,
    )
    tracker = ModelAgnosticTracker()
    occupancy = ZoneOccupancyTracker()
    letters = lane_letters(columns)
    counters = {
        letter: LineCrossingCounter(
            [index / columns * 1000.0, STOP_LINE_Y],
            [(index + 1) / columns * 1000.0, STOP_LINE_Y],
            name=f"lane-{letter}",
        )
        for index, letter in enumerate(letters)
    }
    tracked_frames: list[dict[str, Any]] = []
    last_spatial: list[dict[str, Any]] = []
    last_annotated: np.ndarray | None = None
    first_timestamp: float | None = None
    last_timestamp: float | None = None
    sampled = 0
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        detector.close()
        raise ValueError(f"Could not open video: {path}")
    try:
        for frame_index, timestamp, frame in frames(capture, info.fps, sample_fps):
            detections = detector.detect(frame, frame_index, timestamp)
            tracked = tracker.update(detections, timestamp)["detections"]
            last_spatial = engine.spatialize_result({"detections": tracked})["detections"]
            occupancy_snapshot = occupancy.update(last_spatial, timestamp)
            for counter in counters.values():
                counter.update(last_spatial)
            lanes = lane_snapshot(last_spatial, counters, columns)
            tracked_frames.append({"timestamp": timestamp, "detections": last_spatial})
            if len(tracked_frames) > 1200:
                del tracked_frames[:-1200]
            last_annotated = annotate(engine, frame, last_spatial, counters)
            first_timestamp = timestamp if first_timestamp is None else first_timestamp
            last_timestamp = timestamp
            sampled += 1
            if on_frame is not None:
                on_frame(frame_index, last_annotated, last_spatial, occupancy_snapshot, lanes)
            if sampled >= max_frames:
                break
    finally:
        capture.release()
        detector.close()

    tracks = summarize_tracks(tracked_frames)
    report = {
        "source": {**info.to_dict(), "kind": "file"},
        "mode": mode,
        "grid": {"rows": rows, "columns": columns},
        "sampled_frames": sampled,
        "duration_seconds": round((last_timestamp or 0.0) - (first_timestamp or 0.0), 3),
        "occupancy": occupancy.snapshot(),
        "lanes": lane_snapshot(last_spatial, counters, columns),
        "crossings": {letter: counter.snapshot() for letter, counter in counters.items()},
        "tracks": tracks,
    }
    return AnalysisResult(report=report, last_frame=last_annotated)


def save_report(report: dict[str, Any], path: Path) -> None:
    """Write a report as pretty JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def human_bytes(size: int) -> str:
    """Format a byte count for console output."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def distance(speed: float, duration: float) -> float:
    """Return distance travelled for a speed and duration."""
    return math.fsum([speed * duration])
