"""Highway incident console using the SPE SDK and local traffic video."""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import advisory as advisory_logic
import core
import cv2
import incidents as incident_logic
import live as live_media
import metrics
from flask import Flask, Response, jsonify, render_template, request, send_file
from presets import load_presets, preset_points

from spe import LineCrossingCounter

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs" / "sessions"
SESSIONS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
PREVIEW_CACHE: dict[tuple[str, str], bytes] = {}
PREVIEW_LOCK = threading.Lock()
SESSION_ID = re.compile(r"^[a-f0-9]{12}$")
MAX_REPLAY_FRAMES = 300
LIVE_HISTORY_FRAMES = 300
LIVE_MAX_FRAMES = 2_000_000
REPLAY_MAX_WIDTH = 1280
KEEP_SESSIONS = 16

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


class AnalysisCancelledError(Exception):
    """Raised when an operator cancels a running analysis."""


def session_view(session: dict[str, Any]) -> dict[str, Any]:
    """Return the public session payload, omitting internal track history."""
    history = [
        {
            "frame": item["frame"],
            "t": item["t"],
            "lanes": item["lanes"],
            "active": item["active"],
            "queue": item["queue"],
            "speed": item["speed"],
            "congestion": item["congestion"],
            "zones": item.get("zones", {}),
        }
        for item in session.get("history", [])
    ]
    return {
        "id": session["id"],
        "status": session["status"],
        "progress": session["progress"],
        "sampled_frames": session["sampled_frames"],
        "frames_total": session.get("frames_total", 0),
        "live": bool(session.get("live")),
        "report_ready": bool(
            session.get("report_ready")
            or (OUTPUT_DIR / str(session["id"]) / "report.json").exists()
        ),
        "error": session.get("error"),
        "source": session.get("source"),
        "preset": session.get("preset"),
        "mode": session.get("mode"),
        "fps": session.get("fps", 5),
        "frame_name": session.get("frame_name"),
        "lanes": session.get("lanes", {}),
        "incidents": session.get("incidents", []),
        "incident_summary": incident_logic.summarize_incidents(session.get("incidents", [])),
        "advisory": session.get("advisory", {}),
        "policy": session.get("policy", {}),
        "history": history,
        "started_at": session.get("started_at"),
        "finished_at": session.get("finished_at"),
    }


def update_session(session_id: str, **values: Any) -> None:
    with LOCK:
        SESSIONS[session_id].update(values)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _persist_session(session: dict[str, Any]) -> None:
    with LOCK:
        snapshot = dict(session)
        snapshot["history"] = list(session.get("history", []))
    directory = OUTPUT_DIR / str(snapshot["id"])
    _write_json(directory / "session.json", snapshot)


def _load_session(session_id: str) -> dict[str, Any] | None:
    if not SESSION_ID.match(session_id):
        return None
    with LOCK:
        session = SESSIONS.get(session_id)
    if session is not None:
        return session
    path = OUTPUT_DIR / session_id / "session.json"
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(session, dict) or session.get("id") != session_id:
        return None
    with LOCK:
        SESSIONS[session_id] = session
    return session


def _save_frame(output_dir: Path, frame_name: str, image: Any) -> None:
    height, width = image.shape[:2]
    if width > REPLAY_MAX_WIDTH:
        scale = REPLAY_MAX_WIDTH / width
        image = cv2.resize(
            image,
            (REPLAY_MAX_WIDTH, max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if ok:
        (output_dir / frame_name).write_bytes(buffer.tobytes())


def _summarize(latest: dict[str, Any], incidents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sampled_frames": latest.get("frame", 0),
        "duration_seconds": latest.get("t", 0.0),
        "congestion": latest.get("congestion", 0.0),
        "queue": latest.get("queue", 0),
        "speed": latest.get("speed", 0.0),
        "incidents": incident_logic.summarize_incidents(incidents),
    }


def _resolve_source(source: str) -> tuple[Path | str, str, bool]:
    """Return a readable media input, a display name, and a live flag."""
    links = core.load_links()
    if source not in links:
        raise ValueError(f"Unknown source: {source}")
    value = links[source]
    if live_media.is_live_source(value):
        url, title = live_media.resolve_live(value)
        return url, title, True
    path = core.resolve_media(source, links)
    return path, path.name, False


def _prune_frames(directory: Path, keep: int) -> None:
    """Remove the oldest replay frames while a live session runs."""
    frames = sorted(directory.glob("frame-*.jpg"))
    for frame in frames[:-keep]:
        with contextlib.suppress(OSError):
            frame.unlink()


def _compact_history(
    history: list[dict[str, Any]], session_id: str, status: str
) -> list[dict[str, Any]]:
    return session_view(
        {
            "id": session_id,
            "status": status,
            "progress": 100,
            "sampled_frames": history[-1]["frame"] if history else 0,
            "history": history,
        }
    )["history"]


def _finalize_session(
    session_id: str,
    output_dir: Path,
    settings: dict[str, Any],
    info: core.VideoInfo | None,
    history: list[dict[str, Any]],
    telemetry: metrics.Telemetry,
    base_report: dict[str, Any] | None,
    status: str,
    error: str | None = None,
) -> None:
    """Build and persist the report for completed, stopped, or failed runs."""
    latest = (
        history[-1]
        if history
        else {
            "frame": 0,
            "t": 0.0,
            "lanes": {},
            "active": 0,
            "queue": 0,
            "speed": 0.0,
            "congestion": 0.0,
            "tracks": [],
        }
    )
    detected = incident_logic.detect_incidents(history, settings["policy"])
    current_advisory = advisory_logic.build_advisory(history, detected, settings["policy"])
    report = dict(base_report or {})
    report["source"] = info.to_dict() if info is not None else None
    report["live"] = bool(settings.get("live"))
    report["final_status"] = status
    report["mode"] = settings["mode"]
    report["preset"] = {"key": settings["preset_name"], **settings["preset"]}
    report["policy"] = settings["policy"]
    report["sampled_frames"] = latest.get("frame", 0)
    report["duration_seconds"] = latest.get("t", 0.0)
    report.setdefault("tracks", [])
    report.setdefault("occupancy", {})
    report["lanes"] = latest["lanes"]
    report["lane_crossings"] = telemetry.crossings()
    report["incidents"] = detected
    report["incident_summary"] = incident_logic.summarize_incidents(detected)
    report["advisory"] = current_advisory
    report["summary"] = _summarize(latest, detected)
    report["history"] = _compact_history(history, session_id, status)
    report["finished_at"] = time.time()
    if error:
        report["error"] = error
    core.save_report(report, output_dir / "report.json")
    updates: dict[str, Any] = {
        "status": status,
        "error": error,
        "sampled_frames": latest.get("frame", 0),
        "lanes": latest["lanes"],
        "incidents": detected,
        "advisory": current_advisory,
        "summary": report["summary"],
        "report_ready": True,
        "finished_at": time.time(),
    }
    if status == "done":
        updates["progress"] = 100
    update_session(session_id, **updates)


def process_session(session_id: str, settings: dict[str, Any]) -> None:
    """Analyze one source and maintain telemetry, incidents, and replay frames."""
    output_dir = OUTPUT_DIR / session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    info: core.VideoInfo | None = None
    telemetry: metrics.Telemetry | None = None
    result_report: dict[str, Any] | None = None
    try:
        path, source_name, is_live = _resolve_source(settings["source"])
        settings["live"] = is_live
        info = core.probe(path)
        preset = settings["preset"]
        points = preset_points(preset, info.width, info.height)
        telemetry = metrics.Telemetry(int(preset["columns"]))
        update_session(
            session_id,
            status="running",
            live=is_live,
            frames_total=0 if is_live else settings["frames"],
            source={
                "key": settings["source"],
                "name": source_name,
                "width": info.width,
                "height": info.height,
                "fps": round(info.fps, 2),
                "live": is_live,
            },
        )
        counter = 0

        def on_frame(
            _frame_index: int,
            annotated: Any,
            spatial: list[dict[str, Any]],
            occupancy: dict[str, Any],
            _lanes: dict[str, Any],
        ) -> None:
            nonlocal counter
            with LOCK:
                cancelled = bool(SESSIONS[session_id].get("cancel_requested"))
            if cancelled:
                raise AnalysisCancelledError
            counter += 1
            timestamp = _frame_index / (info.fps or 25.0)
            snapshot = telemetry.update(timestamp, spatial)
            frame_name = f"frame-{counter:06d}.jpg"
            _save_frame(output_dir, frame_name, annotated)
            snapshot["zones"] = {
                name: int(data.get("occupancy", 0))
                for name, data in (occupancy.get("zones") or {}).items()
                if isinstance(data, dict)
            }
            history.append(snapshot)
            if is_live and len(history) > LIVE_HISTORY_FRAMES:
                del history[:-LIVE_HISTORY_FRAMES]
            if is_live and counter % 15 == 0:
                _prune_frames(output_dir, LIVE_HISTORY_FRAMES)
            detected = incident_logic.detect_incidents(history, settings["policy"])
            current_advisory = advisory_logic.build_advisory(history, detected, settings["policy"])
            update_session(
                session_id,
                progress=0 if is_live else min(99, round(counter / settings["frames"] * 100)),
                sampled_frames=counter,
                frame_name=frame_name,
                lanes=snapshot["lanes"],
                incidents=detected,
                advisory=current_advisory,
                history=history,
                summary=_summarize(snapshot, detected),
            )

        result = core.analyze(
            path,
            mode=settings["mode"],
            max_frames=LIVE_MAX_FRAMES if is_live else settings["frames"],
            sample_fps=settings["fps"],
            rows=int(preset["rows"]),
            columns=int(preset["columns"]),
            url=settings["url"],
            model=settings["model"],
            confidence=settings["confidence"],
            points=points,
            on_frame=on_frame,
        )
        result_report = result.report
        if is_live:
            _finalize_session(
                session_id,
                output_dir,
                settings,
                info,
                history,
                telemetry,
                result_report,
                status="error",
                error="Live feed ended.",
            )
            return
        _finalize_session(
            session_id,
            output_dir,
            settings,
            info,
            history,
            telemetry,
            result_report,
            status="done",
        )
    except AnalysisCancelledError:
        if info is not None and telemetry is not None:
            _finalize_session(
                session_id,
                output_dir,
                settings,
                info,
                history,
                telemetry,
                result_report,
                status="cancelled",
            )
        else:
            update_session(
                session_id,
                status="cancelled",
                error=None,
                finished_at=time.time(),
            )
    except Exception as error:
        if info is not None and telemetry is not None and history:
            _finalize_session(
                session_id,
                output_dir,
                settings,
                info,
                history,
                telemetry,
                result_report,
                status="error",
                error=str(error),
            )
        else:
            update_session(
                session_id,
                status="error",
                error=str(error),
                finished_at=time.time(),
            )
    finally:
        with LOCK:
            session = SESSIONS.get(session_id)
        if session is not None:
            with contextlib.suppress(OSError):
                _persist_session(session)


def _policy_from_payload(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("policy")
    if raw is None:
        raw = payload
    policy = {
        key: raw[key]
        for key in incident_logic.DEFAULT_POLICY
        if isinstance(raw, dict) and key in raw
    }
    for key, value in policy.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number")
        if value < 0:
            raise ValueError(f"{key} must be non-negative")
    return incident_logic.policy_with_defaults(policy)


def _default_source(sources: dict[str, str]) -> str:
    return next(
        (key for key, value in sources.items() if live_media.is_live_source(value)),
        next(iter(sources)),
    )


def source_display_name(key: str) -> str:
    """Give link.json keys a concise camera-wall label."""
    match = re.fullmatch(r"cam(\d+)", key.lower())
    if match:
        return f"Traffic Camera {int(match.group(1)):02d}"
    if key.lower() == "video":
        return "Recorded Traffic Sample"
    return key.replace("_", " ").replace("-", " ").title()


def _default_preset(presets: dict[str, dict[str, Any]]) -> str:
    return "boulevard" if "boulevard" in presets else next(iter(presets))


def settings_from_request() -> dict[str, Any]:
    payload = request.get_json(silent=True) or request.form.to_dict()
    presets = load_presets()
    sources = core.load_links()
    preset_name = str(payload.get("preset", _default_preset(presets)))
    if preset_name not in presets:
        raise ValueError(f"Unknown preset {preset_name!r}")
    source = str(payload.get("source", _default_source(sources)))
    if source not in sources:
        raise ValueError(f"Unknown media source {source!r}")
    mode = str(payload.get("mode", "local"))
    if mode not in {"local", "server"}:
        raise ValueError(f"Unknown detection mode {mode!r}")
    return {
        "source": source,
        "preset_name": preset_name,
        "preset": presets[preset_name],
        "mode": mode,
        "frames": max(1, min(MAX_REPLAY_FRAMES, int(payload.get("frames", 120)))),
        "fps": max(0.2, min(30.0, float(payload.get("fps", 5)))),
        "url": str(payload.get("url", "http://127.0.0.1:5000")),
        "model": str(payload.get("model", "model-a")),
        "confidence": max(0.0, min(1.0, float(payload.get("confidence", 0.4)))),
        "policy": _policy_from_payload(payload),
        "live": live_media.is_live_source(sources[source]),
    }


def _preview_bytes(source: str, preset_name: str) -> bytes:
    key = (source, preset_name)
    with PREVIEW_LOCK:
        cached = PREVIEW_CACHE.get(key)
    if cached is not None:
        return cached
    presets = load_presets()
    if preset_name not in presets:
        raise ValueError(f"Unknown preset {preset_name!r}")
    path, _name, _is_live = _resolve_source(source)
    preset = presets[preset_name]
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        if not ok:
            raise ValueError(f"Could not read media: {path}")
        height, width = frame.shape[:2]
        points = preset_points(preset, width, height)
        engine = core.build_engine(
            width,
            height,
            rows=int(preset["rows"]),
            columns=int(preset["columns"]),
            points=points,
        )
        letters = metrics.lane_letters(int(preset["columns"]))
        counters = {
            letter: LineCrossingCounter(
                [index / len(letters) * 1000.0, core.STOP_LINE_Y],
                [(index + 1) / len(letters) * 1000.0, core.STOP_LINE_Y],
                name=f"lane-{letter}",
            )
            for index, letter in enumerate(letters)
        }
        annotated = core.annotate(engine, frame, [], counters)
    finally:
        capture.release()
    height, width = annotated.shape[:2]
    if width > REPLAY_MAX_WIDTH:
        scale = REPLAY_MAX_WIDTH / width
        annotated = cv2.resize(
            annotated,
            (REPLAY_MAX_WIDTH, max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 84])
    if not ok:
        raise ValueError("Could not encode preview")
    encoded = buffer.tobytes()
    with PREVIEW_LOCK:
        PREVIEW_CACHE[key] = encoded
    return encoded


def session_summaries() -> list[dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for path in OUTPUT_DIR.glob("*/session.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "id" not in data:
            continue
        summaries[str(data["id"])] = {
            "id": data["id"],
            "status": data.get("status"),
            "live": bool(data.get("live")),
            "source": data.get("source", {}),
            "preset": data.get("preset", {}),
            "mode": data.get("mode"),
            "started_at": data.get("started_at"),
            "finished_at": data.get("finished_at"),
            "summary": data.get("summary", {}),
            "incident_summary": incident_logic.summarize_incidents(data.get("incidents", [])),
        }
    with LOCK:
        for session_id, data in SESSIONS.items():
            summaries[session_id] = {
                "id": session_id,
                "status": data.get("status"),
                "live": bool(data.get("live")),
                "source": data.get("source", {}),
                "preset": data.get("preset", {}),
                "mode": data.get("mode"),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "summary": data.get("summary", {}),
                "incident_summary": incident_logic.summarize_incidents(data.get("incidents", [])),
            }
    return sorted(
        summaries.values(),
        key=lambda item: float(item.get("started_at") or 0),
        reverse=True,
    )[:8]


def cleanup_outputs() -> None:
    directories = sorted(
        (path for path in OUTPUT_DIR.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in directories[KEEP_SESSIONS:]:
        with contextlib.suppress(OSError):
            shutil.rmtree(path)


@app.get("/")
def index() -> str:
    sources = core.load_links()
    presets = load_presets()
    return render_template(
        "index.html",
        sources=sources,
        presets=presets,
        default_source=_default_source(sources),
        default_preset=_default_preset(presets),
        source_label=source_display_name,
    )


@app.get("/api/sources")
def sources() -> Response:
    return jsonify({"sources": core.load_links()})


@app.get("/api/presets")
def presets_api() -> Response:
    return jsonify({"presets": load_presets()})


@app.get("/api/preview")
def preview() -> Any:
    sources = core.load_links()
    presets = load_presets()
    source = str(request.args.get("source", _default_source(sources)))
    preset_name = str(request.args.get("preset", _default_preset(presets)))
    try:
        data = _preview_bytes(source, preset_name)
    except Exception as error:
        return jsonify({"error": str(error)}), 400
    response = Response(data, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "max-age=60"
    return response


@app.get("/api/sessions")
def list_sessions() -> Response:
    return jsonify({"sessions": session_summaries()})


@app.post("/api/sessions")
def create_session() -> Response:
    try:
        settings = settings_from_request()
    except Exception as error:
        return jsonify({"error": str(error)}), 400
    session_id = uuid.uuid4().hex[:12]
    is_live = bool(settings.get("live"))
    with LOCK:
        SESSIONS[session_id] = {
            "id": session_id,
            "status": "queued",
            "progress": 0,
            "sampled_frames": 0,
            "frames_total": 0 if is_live else settings["frames"],
            "live": is_live,
            "report_ready": False,
            "error": None,
            "source": {"key": settings["source"], "name": settings["source"], "live": is_live},
            "preset": {"key": settings["preset_name"], **settings["preset"]},
            "mode": settings["mode"],
            "fps": settings["fps"],
            "frame_name": None,
            "lanes": {},
            "incidents": [],
            "advisory": {},
            "policy": settings["policy"],
            "history": [],
            "summary": {},
            "started_at": time.time(),
            "finished_at": None,
            "cancel_requested": False,
        }
    thread = threading.Thread(target=process_session, args=(session_id, settings), daemon=True)
    thread.start()
    return jsonify({"id": session_id}), 202


@app.get("/api/sessions/<session_id>")
def get_session(session_id: str) -> Response:
    session = _load_session(session_id)
    if session is None:
        return jsonify({"error": "unknown session"}), 404
    return jsonify(session_view(session))


@app.post("/api/sessions/<session_id>/cancel")
def cancel_session(session_id: str) -> Response:
    session = _load_session(session_id)
    if session is None:
        return jsonify({"error": "unknown session"}), 404
    update_session(session_id, cancel_requested=True)
    return jsonify({"cancelled": True})


@app.get("/api/sessions/<session_id>/frame")
def get_frame(session_id: str) -> Any:
    session = _load_session(session_id)
    if session is None:
        return Response("Unknown session.", status=404)
    with LOCK:
        frame_name = session.get("frame_name")
    if not frame_name:
        return Response("No frame yet.", status=404)
    path = OUTPUT_DIR / session_id / str(frame_name)
    if not path.exists():
        return Response("No frame yet.", status=404)
    response = send_file(path, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/sessions/<session_id>/frames/<int:index>")
def get_replay_frame(session_id: str, index: int) -> Any:
    if not SESSION_ID.match(session_id) or index < 1:
        return Response("Unknown frame.", status=404)
    path = OUTPUT_DIR / session_id / f"frame-{index:06d}.jpg"
    if not path.exists():
        return Response("Unknown frame.", status=404)
    response = send_file(path, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.get("/api/sessions/<session_id>/report")
def get_report(session_id: str) -> Any:
    path = OUTPUT_DIR / session_id / "report.json"
    if not SESSION_ID.match(session_id) or not path.exists():
        return jsonify({"error": "report not ready"}), 404
    return send_file(
        path,
        mimetype="application/json",
        as_attachment=True,
        download_name="incident-report.json",
    )


@app.post("/api/sessions/<session_id>/what-if")
def what_if(session_id: str) -> Response:
    session = _load_session(session_id)
    if session is None:
        return jsonify({"error": "unknown session"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        policy = _policy_from_payload(payload)
    except Exception as error:
        return jsonify({"error": str(error)}), 400
    history = session.get("history") or []
    detected = incident_logic.detect_incidents(history, policy)
    current_advisory = advisory_logic.build_advisory(history, detected, policy)
    return jsonify(
        {
            "policy": policy,
            "incidents": detected,
            "incident_summary": incident_logic.summarize_incidents(detected),
            "advisory": current_advisory,
        }
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_outputs()
    app.run(host="127.0.0.1", port=5002, debug=False, threaded=True)
