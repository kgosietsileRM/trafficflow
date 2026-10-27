"""Lane and track telemetry for the highway incident console."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

STOP_LINE_Y = 420.0
QUEUE_Y = 400.0
FULL_SPEED = 350.0
SMOOTHING = 0.35
MIN_SPEED_OBSERVATIONS = 3


def lane_letters(columns: int) -> list[str]:
    """Return the lane labels used by the spatial grid."""
    return [chr(ord("A") + index) for index in range(columns)]


def _valid_position(detection: dict[str, Any]) -> tuple[str, float, float] | None:
    spatial = detection.get("spatial")
    if not isinstance(spatial, dict) or spatial.get("status") != "valid":
        return None
    cell = spatial.get("grid_cell")
    position = spatial.get("position")
    if not isinstance(cell, dict) or not isinstance(position, dict):
        return None
    try:
        lane = int(cell["column"])
        x = float(position["x"])
        y = float(position["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return str(lane), x, y


class Telemetry:
    """Turn per-frame spatial detections into lane-level operational metrics."""

    def __init__(self, columns: int) -> None:
        self.columns = columns
        self.letters = lane_letters(columns)
        self._previous: dict[str, tuple[float, float, float]] = {}
        self._track_speed: dict[str, float] = {}
        self._track_observations: dict[str, int] = {}
        self._track_lane: dict[str, str] = {}
        self._side: dict[str, dict[str, int]] = {letter: {} for letter in self.letters}
        self._crossings = {letter: {"positive": 0, "negative": 0} for letter in self.letters}
        self._smoothed = {
            letter: {"occupancy": 0.0, "queue": 0.0, "speed": 0.0, "congestion": 0.0}
            for letter in self.letters
        }
        self._start = 0.0
        self._last = 0.0
        self._frames = 0

    def update(self, timestamp: float, spatial: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Consume one sampled frame and return its telemetry snapshot."""
        if self._frames == 0:
            self._start = timestamp
        self._last = timestamp
        self._frames += 1

        lane_positions: dict[str, list[tuple[float, float]]] = {
            letter: [] for letter in self.letters
        }
        lane_speeds: dict[str, list[float]] = {letter: [] for letter in self.letters}
        tracks: list[dict[str, Any]] = []

        for detection in spatial:
            parsed = _valid_position(detection)
            track_id = str(detection.get("track_id") or detection.get("id") or "")
            if parsed is None or not track_id:
                continue
            column, x, y = parsed
            index = int(column)
            if index < 0 or index >= self.columns:
                continue
            lane = self.letters[index]
            lane_positions[lane].append((x, y))
            self._track_lane[track_id] = lane
            self._track_observations[track_id] = self._track_observations.get(track_id, 0) + 1

            previous = self._previous.get(track_id)
            speed = self._track_speed.get(track_id, 0.0)
            if previous is not None:
                elapsed = timestamp - previous[2]
                if 0.02 < elapsed <= 2.0:
                    instant = math.hypot(x - previous[0], y - previous[1]) / elapsed
                    speed = (
                        speed + SMOOTHING * (instant - speed)
                        if track_id in self._track_speed
                        else instant
                    )
            self._previous[track_id] = (x, y, timestamp)
            self._track_speed[track_id] = speed
            lane_speeds[lane].append(speed)

            side = 1 if y >= STOP_LINE_Y else -1
            previous_side = self._side[lane].get(track_id)
            self._side[lane][track_id] = side
            if previous_side is not None and previous_side != side:
                direction = "positive" if side > 0 else "negative"
                self._crossings[lane][direction] += 1

            tracks.append(
                {
                    "id": track_id,
                    "lane": lane,
                    "speed": round(speed, 2),
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "observations": self._track_observations[track_id],
                }
            )

        elapsed = max(timestamp - self._start, 1.0)
        lanes: dict[str, dict[str, Any]] = {}
        for lane in self.letters:
            occupancy = len(lane_positions[lane])
            queue = sum(1 for _x, y in lane_positions[lane] if y >= QUEUE_Y)
            speeds = [
                speed
                for track in tracks
                if track["lane"] == lane and track["observations"] >= MIN_SPEED_OBSERVATIONS
                for speed in [track["speed"]]
            ]
            speed = round(statistics.median(speeds), 2) if speeds else 0.0
            raw = _congestion(occupancy, queue, speed)
            previous_lane = self._smoothed[lane]
            smoothed = {
                "occupancy": _smooth(previous_lane["occupancy"], occupancy),
                "queue": _smooth(previous_lane["queue"], queue),
                "speed": _smooth(previous_lane["speed"], speed),
                "congestion": _smooth(previous_lane["congestion"], raw),
            }
            self._smoothed[lane] = smoothed
            crossings = self._crossings[lane]
            lanes[lane] = {
                "occupancy": occupancy,
                "queue": queue,
                "speed": round(smoothed["speed"], 1),
                "congestion": round(smoothed["congestion"], 1),
                "crossings": crossings["positive"] + crossings["negative"],
                "throughput": round(
                    (crossings["positive"] + crossings["negative"]) / elapsed * 60.0, 1
                ),
            }

        active = sum(lane["occupancy"] for lane in lanes.values())
        all_speeds = [
            track["speed"] for track in tracks if track["observations"] >= MIN_SPEED_OBSERVATIONS
        ]
        mean_speed = round(statistics.median(all_speeds), 1) if all_speeds else 0.0
        congestion = round(max((lane["congestion"] for lane in lanes.values()), default=0.0), 1)
        queue = sum(lane["queue"] for lane in lanes.values())

        return {
            "frame": self._frames,
            "t": round(timestamp, 3),
            "lanes": lanes,
            "active": active,
            "queue": queue,
            "speed": mean_speed,
            "congestion": congestion,
            "tracks": tracks,
        }

    def crossings(self) -> dict[str, dict[str, int]]:
        """Return cumulative directional crossing counts for each lane."""
        return {
            lane: {
                "positive": counts["positive"],
                "negative": counts["negative"],
                "total": counts["positive"] + counts["negative"],
            }
            for lane, counts in self._crossings.items()
        }


def _smooth(previous: float, current: float) -> float:
    if previous == 0.0:
        return float(current)
    return previous + SMOOTHING * (current - previous)


def _congestion(occupancy: int, queue: int, speed: float) -> float:
    occupancy_score = min(1.0, occupancy / 5.0)
    queue_score = min(1.0, queue / 4.0)
    flow_score = 0.0 if occupancy == 0 else 1.0 - min(1.0, speed / FULL_SPEED)
    return 100.0 * (0.30 * occupancy_score + 0.45 * queue_score + 0.25 * flow_score)
