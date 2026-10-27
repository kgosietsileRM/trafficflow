"""Incident detection over recorded lane telemetry."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

DEFAULT_POLICY: dict[str, float] = {
    "stopped_speed": 55.0,
    "stopped_seconds": 1.5,
    "queue_count": 2.0,
    "queue_seconds": 2.0,
    "congestion_watch": 45.0,
    "congestion_high": 70.0,
    "imbalance": 35.0,
    "sustained_seconds": 1.5,
}

Incident = dict[str, Any]
History = Sequence[dict[str, Any]]


def policy_with_defaults(policy: dict[str, Any] | None = None) -> dict[str, float]:
    """Merge a partial policy into the console defaults."""
    merged = dict(DEFAULT_POLICY)
    if policy:
        for key, value in policy.items():
            if key in merged:
                merged[key] = float(value)
    return merged


def detect_incidents(history: History, policy: dict[str, Any] | None = None) -> list[Incident]:
    """Detect stopped vehicles, queue buildup, congestion, and imbalance."""
    if not history:
        return []
    settings = policy_with_defaults(policy)
    incidents: list[Incident] = []
    incidents.extend(_stopped_vehicles(history, settings))
    for lane in history[-1]["lanes"]:
        incidents.extend(_lane_incidents(history, lane, settings))
    incidents.extend(_network_congestion(history, settings))
    incidents.extend(_lane_imbalance(history, settings))
    incidents.sort(key=lambda item: (item["start_frame"], item["lane"], item["type"]))
    for index, incident in enumerate(incidents, start=1):
        incident["id"] = f"inc-{index:03d}"
    return incidents


def _stopped_vehicles(history: History, settings: dict[str, float]) -> list[Incident]:
    runs: dict[str, dict[str, Any]] = {}
    incidents: list[Incident] = []
    last_frame = history[-1]["frame"]
    for entry in history:
        current: dict[str, dict[str, Any]] = {}
        for track in entry.get("tracks") or []:
            if int(track.get("observations", 0)) < 3:
                continue
            if float(track.get("speed", 0.0)) <= settings["stopped_speed"]:
                current[str(track["id"])] = track
        for track_id, track in current.items():
            if track_id not in runs:
                runs[track_id] = {
                    "type": "stopped_vehicle",
                    "lane": track["lane"],
                    "start_frame": entry["frame"],
                    "start_time": entry["t"],
                    "end_frame": entry["frame"],
                    "end_time": entry["t"],
                    "peak": float(track.get("speed", 0.0)),
                    "track_ids": [track_id],
                }
            run = runs[track_id]
            run["end_frame"] = entry["frame"]
            run["end_time"] = entry["t"]
            run["lane"] = track["lane"]
            run["peak"] = min(run["peak"], float(track.get("speed", 0.0)))
        for track_id in list(runs):
            if track_id not in current and entry["t"] - runs[track_id]["end_time"] > 0.8:
                incident = _finish(runs.pop(track_id), settings["stopped_seconds"])
                if incident is not None:
                    incident["open"] = False
                    incidents.append(incident)
    for run in runs.values():
        incident = _finish(run, settings["stopped_seconds"])
        if incident is not None:
            incident["open"] = run["end_frame"] == last_frame
            incidents.append(incident)
    return incidents


def _lane_incidents(history: History, lane: str, settings: dict[str, float]) -> list[Incident]:
    queue_runs = _scan(
        history,
        lambda entry: (
            float(entry["lanes"].get(lane, {}).get("queue", 0)) >= settings["queue_count"]
        ),
        lambda entry: float(entry["lanes"].get(lane, {}).get("queue", 0)),
        settings["queue_seconds"],
    )
    incidents: list[Incident] = []
    for run in queue_runs:
        incidents.append(
            {
                "type": "queue_buildup",
                "lane": lane,
                "start_frame": run["start_frame"],
                "start_time": run["start_time"],
                "end_frame": run["end_frame"],
                "end_time": run["end_time"],
                "peak": run["peak"],
                "track_ids": [],
                "open": run["open"],
                "severity": "medium",
                "message": f"Queue buildup in Lane {lane}",
            }
        )
    return incidents


def _network_congestion(history: History, settings: dict[str, float]) -> list[Incident]:
    runs = _scan(
        history,
        lambda entry: float(entry.get("congestion", 0.0)) >= settings["congestion_high"],
        lambda entry: float(entry.get("congestion", 0.0)),
        settings["sustained_seconds"],
    )
    return [
        {
            "type": "network_congestion",
            "lane": "ALL",
            "start_frame": run["start_frame"],
            "start_time": run["start_time"],
            "end_frame": run["end_frame"],
            "end_time": run["end_time"],
            "peak": run["peak"],
            "track_ids": [],
            "open": run["open"],
            "severity": "high",
            "message": "Sustained mainline congestion",
        }
        for run in runs
    ]


def _lane_imbalance(history: History, settings: dict[str, float]) -> list[Incident]:
    def spread(entry: dict[str, Any]) -> float:
        values = [float(item.get("congestion", 0.0)) for item in entry["lanes"].values()]
        return max(values) - min(values) if len(values) >= 2 else 0.0

    runs = _scan(
        history,
        lambda entry: spread(entry) >= settings["imbalance"],
        spread,
        settings["sustained_seconds"],
    )
    incidents: list[Incident] = []
    for run in runs:
        worst_lane = max(
            history[run["start_index"]]["lanes"],
            key=lambda lane: float(history[run["start_index"]]["lanes"][lane]["congestion"]),
        )
        incidents.append(
            {
                "type": "lane_imbalance",
                "lane": worst_lane,
                "start_frame": run["start_frame"],
                "start_time": run["start_time"],
                "end_frame": run["end_frame"],
                "end_time": run["end_time"],
                "peak": run["peak"],
                "track_ids": [],
                "open": run["open"],
                "severity": "low",
                "message": f"Lane imbalance, highest pressure in Lane {worst_lane}",
            }
        )
    return incidents


def _scan(
    history: History,
    predicate: Callable[[dict[str, Any]], bool],
    measure: Callable[[dict[str, Any]], float],
    minimum_seconds: float,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    for index, entry in enumerate(history):
        if predicate(entry):
            if active is None:
                active = {
                    "start_index": index,
                    "start_frame": entry["frame"],
                    "start_time": entry["t"],
                    "end_frame": entry["frame"],
                    "end_time": entry["t"],
                    "peak": measure(entry),
                }
            active["end_frame"] = entry["frame"]
            active["end_time"] = entry["t"]
            active["peak"] = max(active["peak"], measure(entry))
        elif active is not None:
            active["open"] = False
            runs.append(active)
            active = None
    if active is not None:
        active["open"] = True
        runs.append(active)
    return [run for run in runs if (run["end_time"] - run["start_time"]) + 1e-9 >= minimum_seconds]


def _finish(run: dict[str, Any], minimum_seconds: float) -> Incident | None:
    duration = run["end_time"] - run["start_time"]
    if duration + 1e-9 < minimum_seconds:
        return None
    run["duration"] = round(duration, 2)
    run["severity"] = "critical" if duration >= 4.0 else "high"
    run["peak"] = round(float(run["peak"]), 1)
    run["message"] = f"Stopped vehicle in Lane {run['lane']}"
    return run


def summarize_incidents(incidents: Iterable[Incident]) -> dict[str, int]:
    """Return counts by severity and open incidents."""
    payload = {"total": 0, "open": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
    for incident in incidents:
        payload["total"] += 1
        payload["open"] += 1 if incident.get("open") else 0
        severity = str(incident.get("severity", "low"))
        if severity in payload:
            payload[severity] += 1
    return payload
