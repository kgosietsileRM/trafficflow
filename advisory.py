"""Operational state and response advisory for the incident console."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def build_advisory(
    history: Sequence[dict[str, Any]],
    incidents: Sequence[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a traffic-department style network advisory."""
    settings = policy or {}
    watch = float(settings.get("congestion_watch", 45))
    high = float(settings.get("congestion_high", 70))
    latest = history[-1] if history else {"lanes": {}, "congestion": 0, "queue": 0, "speed": 0}
    lanes = latest.get("lanes") or {}
    congestion = float(latest.get("congestion", 0))
    open_incidents = [item for item in incidents if item.get("open")]
    window_start = float(latest.get("t", 0)) - 10.0
    recent = [item for item in incidents if float(item.get("end_time", 0)) >= window_start][-3:]
    highest = max(
        (str(item.get("severity", "low")) for item in open_incidents),
        key=_severity_rank,
        default="low",
    )

    if open_incidents:
        state = "incident"
    elif congestion >= high:
        state = "congested"
    elif congestion >= watch or recent:
        state = "watch"
    else:
        state = "normal"

    if state == "incident":
        priority = "critical" if highest == "critical" else "high"
        metering = "HOLD"
    elif state == "congested":
        priority = "high"
        metering = "LEVEL 3"
    elif state == "watch":
        priority = "medium"
        metering = "LEVEL 2" if congestion >= (watch + high) / 2 else "LEVEL 1"
    else:
        priority = "low"
        metering = "OPEN"

    actions: list[str] = []
    for incident in open_incidents[:3]:
        if incident.get("type") == "stopped_vehicle":
            actions.append(f"Dispatch response to Lane {incident.get('lane')}")
        elif incident.get("type") == "queue_buildup":
            actions.append(f"Monitor queue in Lane {incident.get('lane')}")
        elif incident.get("type") == "lane_imbalance":
            actions.append(f"Rebalance demand away from Lane {incident.get('lane')}")
        else:
            actions.append("Review mainline congestion")
    if not actions:
        if recent:
            latest_incident = recent[-1]
            actions.append(
                f"Review recent {_incident_label(latest_incident.get('type'))} in Lane "
                f"{latest_incident.get('lane')}"
            )
        pressured = sorted(
            lanes.items(),
            key=lambda item: float(item[1].get("congestion", 0)),
            reverse=True,
        )
        if not recent and pressured and float(pressured[0][1].get("congestion", 0)) >= watch:
            actions.append(f"Monitor Lane {pressured[0][0]} pressure")
        if not recent and not actions:
            actions.append("Maintain normal operations")

    reasons: list[str] = []
    if open_incidents:
        reasons.append(f"{len(open_incidents)} active incident(s)")
    elif recent:
        reasons.append(f"{len(recent)} incident(s) in the recent review window")
    if congestion >= watch:
        reasons.append(f"Mainline congestion index {congestion:.0f}")
    if float(latest.get("queue", 0)) > 0:
        reasons.append(f"{int(latest.get('queue', 0))} queued vehicle(s)")
    if not reasons:
        reasons.append("No active alerts; flow within normal range")

    return {
        "state": state,
        "priority": priority,
        "metering": metering,
        "actions": actions,
        "reasons": reasons,
        "congestion": round(congestion, 1),
        "queue": latest.get("queue", 0),
        "speed": latest.get("speed", 0),
        "active_incidents": len(open_incidents),
    }


def _severity_rank(value: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(value, 0)


def _incident_label(value: Any) -> str:
    return {
        "stopped_vehicle": "stopped vehicle",
        "queue_buildup": "queue buildup",
        "network_congestion": "congestion",
        "lane_imbalance": "lane imbalance",
    }.get(str(value), "incident")
