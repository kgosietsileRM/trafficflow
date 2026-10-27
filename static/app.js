import { renderCongestionChart, renderLegend, renderQueueChart } from "./charts.js";
import { Playback } from "./playback.js";

const $ = (id) => document.getElementById(id);

const dom = {
  clock: $("clock"),
  feedName: $("feed-name"),
  detectionLabel: $("detection-label"),
  camCount: $("camCount"),
  camList: $("camList"),
  viewTabs: document.querySelector(".view-tabs"),
  infoView: $("info-view"),
  aboutView: $("about-view"),
  chips: $("chips"),
  searchInput: $("searchInput"),
  alertCount: $("alertCount"),
  headerAlerts: $("header-alerts"),
  wallGrid: $("wallGrid"),
  wallSub: $("wallSub"),
  featuredTile: $("featuredTile"),
  featuredId: $("featured-id"),
  featuredLive: $("featured-live"),
  featuredName: $("featured-name"),
  featuredArea: $("featured-area"),
  cycleButton: $("cycleBtn"),
  fullscreenButton: $("fsBtn"),
  eventStatus: $("event-status"),
  eventStatusDot: $("event-status-dot"),
  assignmentTag: $("assignment-tag"),
  connection: $("connection"),
  connectionLabel: $("connection-label"),
  form: $("run-form"),
  formError: $("form-error"),
  source: $("source"),
  preset: $("preset"),
  frames: $("frames"),
  framesLabel: $("frames-label"),
  fps: $("fps"),
  mode: $("mode"),
  url: $("url"),
  model: $("model"),
  confidence: $("confidence"),
  run: $("run"),
  cancel: $("cancel"),
  runState: $("run-state"),
  progressText: $("progress-text"),
  progressBar: $("progress-bar"),
  runFrames: $("run-frames"),
  runElapsed: $("run-elapsed"),
  stageEyebrow: $("stage-eyebrow"),
  stageTitle: $("stage-title"),
  stageChip: $("stage-chip"),
  reportLink: $("report-link"),
  frame: $("frame"),
  placeholder: $("placeholder"),
  frameOverlay: $("frame-overlay"),
  overlayCamera: $("overlay-camera"),
  overlayTime: $("overlay-time"),
  overlayFrame: $("overlay-frame"),
  overlaySource: $("overlay-source"),
  playToggle: $("play-toggle"),
  playIcon: $("play-icon"),
  scrubber: $("scrubber"),
  incidentMarks: $("incident-marks"),
  playClock: $("play-clock"),
  laneSummary: $("lane-summary"),
  congestionChart: $("congestion-chart"),
  queueChart: $("queue-chart"),
  congestionLegend: $("congestion-legend"),
  kpiActive: $("kpi-active"),
  kpiActiveSub: $("kpi-active-sub"),
  kpiCongestion: $("kpi-congestion"),
  kpiCongestionSub: $("kpi-congestion-sub"),
  kpiSpeed: $("kpi-speed"),
  advisoryState: $("advisory-state"),
  advisoryPriority: $("advisory-priority"),
  advisoryMetering: $("advisory-metering"),
  advisoryActions: $("advisory-actions"),
  advisoryReasons: $("advisory-reasons"),
  incidentList: $("incident-list"),
  incidentCount: $("incident-count"),
  sessionList: $("session-list"),
  sessionCount: $("session-count"),
  stopped: $("policy-stopped"),
  stoppedOut: $("policy-stopped-out"),
  duration: $("policy-duration"),
  durationOut: $("policy-duration-out"),
  congestion: $("policy-congestion"),
  congestionOut: $("policy-congestion-out"),
  policyApply: $("policy-apply"),
};

const state = {
  sessionId: null,
  session: null,
  status: "idle",
  history: [],
  incidents: [],
  advisory: {},
  policy: { stopped_speed: 55, stopped_seconds: 1.5, congestion_high: 70 },
  presets: {},
  sources: {},
  cameraFilter: "all",
  searchTerm: "",
  cycleTimer: null,
  view: "info-view",
  fps: 5,
  startedAt: null,
  finishedAt: null,
  pollTimer: null,
  cursor: 0,
  syncedPolicy: null,
  busy: false,
};

const playback = new Playback({
  frame: dom.frame,
  scrubber: dom.scrubber,
  clock: dom.playClock,
  button: dom.playToggle,
  icon: dom.playIcon,
  marks: dom.incidentMarks,
  onFrame: (item, index) => {
    state.cursor = index;
    renderSnapshot(item);
    renderCharts();
  },
});

function clock(value) {
  const seconds = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function timestamp(value) {
  if (!value) return "—";
  const date = new Date(Number(value) * 1000);
  return `${date.toLocaleDateString([], { month: "short", day: "2-digit" })} ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function localClock() {
  return new Date().toLocaleTimeString([], { hour12: false });
}

function setConnection(kind, label) {
  dom.connection.className = `connection ${kind || ""}`.trim();
  dom.connectionLabel.textContent = label;
}

function showFormError(message) {
  if (!message) {
    dom.formError.hidden = true;
    dom.formError.textContent = "";
    return;
  }
  dom.formError.hidden = false;
  dom.formError.textContent = message;
}

function setStatusChip(label, kind) {
  dom.stageChip.textContent = label;
  dom.stageChip.className = `status-chip ${kind || ""}`.trim();
}

function setStage(eyebrow, title, label, kind) {
  dom.stageEyebrow.textContent = eyebrow;
  dom.stageTitle.textContent = title;
  setStatusChip(label, kind);
}

function switchView(viewId) {
  const showAbout = viewId === "about-view";
  state.view = showAbout ? "about-view" : "info-view";
  dom.infoView.hidden = showAbout;
  dom.aboutView.hidden = !showAbout;
  dom.viewTabs.querySelectorAll("[data-view]").forEach((button) => {
    const selected = button.dataset.view === state.view;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function setRunningControls(running) {
  for (const element of [dom.source, dom.preset, dom.frames, dom.fps, dom.mode, dom.url, dom.model, dom.confidence]) {
    element.disabled = running;
  }
  dom.run.disabled = running;
  dom.cancel.hidden = !running;
}

function statusLabel(status) {
  return {
    queued: "Queued",
    running: "Monitoring",
    done: "Complete",
    error: "Fault",
    cancelled: "Stopped",
    idle: "Standby",
  }[status] || "Standby";
}

function updateProgress(session) {
  const isLive = Boolean(session?.live);
  const percent = Math.max(0, Math.min(100, Number(session?.progress) || 0));
  dom.progressBar.classList.toggle("live", isLive);
  dom.progressBar.style.width = isLive ? "100%" : `${percent}%`;
  dom.progressText.textContent = isLive ? "LIVE" : `${Math.round(percent)}%`;
  dom.runFrames.textContent = isLive
    ? `${session?.sampled_frames || 0} frames logged`
    : `${session?.sampled_frames || 0} / ${session?.frames_total || 0} frames`;
  dom.runState.textContent = statusLabel(session?.status || "idle");
  dom.runState.classList.toggle("run-state-error", session?.status === "error");
  updateElapsed();
}

function updateElapsed() {
  if (!state.startedAt) {
    dom.runElapsed.textContent = "00:00";
    return;
  }
  const end = state.finishedAt || Date.now() / 1000;
  dom.runElapsed.textContent = clock(end - state.startedAt);
}

function selectedPreset() {
  return state.presets[dom.preset.value] || state.session?.preset || { rows: 2, columns: 3 };
}

function sourceDisplayName(key) {
  const match = /^cam(\d+)$/i.exec(key || "");
  if (match) return `Traffic Camera ${String(Number(match[1])).padStart(2, "0")}`;
  if ((key || "").toLowerCase() === "video") return "Recorded Traffic Sample";
  return String(key || "Camera").replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function sourceCameraId(key) {
  const match = /^cam(\d+)$/i.exec(key || "");
  return match ? `CAM-${String(Number(match[1])).padStart(2, "0")}` : "REC-01";
}

function sourceIsLive(key) {
  const value = state.sources[key] || "";
  return /^(rtsp|rtmp|rtmps|udp):\/\//i.test(value) || /youtube\.com|youtu\.be/i.test(value) || /\.(m3u8|mpd)(\?|$)/i.test(value);
}

function youtubeId(key) {
  const value = state.sources[key];
  if (!value) return null;
  try {
    const url = new URL(value);
    const id = url.hostname.endsWith("youtu.be")
      ? url.pathname.slice(1)
      : url.searchParams.get("v") || url.pathname.match(/\/(?:live|embed|shorts)\/([^/?]+)/)?.[1];
    return id && /^[\w-]{6,20}$/.test(id) ? id : null;
  } catch {
    return null;
  }
}

function sourceThumbnail(key) {
  const id = youtubeId(key);
  return id ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : "";
}

function selectedSourceLabel() {
  return sourceDisplayName(dom.source.value);
}

function selectedSourceIsLive() {
  return sourceIsLive(dom.source.value);
}

function updateFramesLabel() {
  if (!dom.framesLabel) return;
  dom.framesLabel.textContent = selectedSourceIsLive() ? "Live buffer" : "Sample frames";
}

async function refreshPreview() {
  if (state.status === "running" || state.status === "queued") return;
  const source = dom.source.value;
  const sourceLabel = selectedSourceLabel();
  const isLive = selectedSourceIsLive();
  const presetName = dom.preset.value;
  const preset = state.presets[presetName] || { name: presetName };
  syncFeaturedCamera();
  dom.placeholder.hidden = false;
  dom.placeholder.querySelector("p").textContent = isLive ? "Resolving live camera feed…" : "Loading camera preview…";
  dom.frame.classList.remove("visible");
  dom.frame.alt = `Preview of ${sourceLabel}`;
  dom.frame.src = `/api/preview?source=${encodeURIComponent(source)}&preset=${encodeURIComponent(presetName)}&t=${Date.now()}`;
  dom.frame.onload = () => {
    dom.frame.classList.add("visible");
    dom.placeholder.hidden = true;
  };
  dom.frame.onerror = () => {
    dom.placeholder.hidden = false;
    dom.placeholder.querySelector("p").textContent = "Preview unavailable for this feed.";
  };
  dom.feedName.textContent = sourceLabel;
  dom.detectionLabel.textContent = dom.mode.value === "local" ? "Local" : "Engine";
  dom.overlayCamera.textContent = `${sourceCameraId(source)} · ${sourceLabel}`;
  dom.overlayTime.textContent = localClock();
  dom.overlayFrame.textContent = isLive ? "LIVE" : "PREVIEW";
  dom.overlaySource.textContent = `${preset.name || presetName} · ${preset.columns || "—"} lanes`;
  setStage("Preview", `${preset.name || presetName} camera preview`, isLive ? "LIVE" : "PREVIEW", "preview");
}

async function loadSources() {
  try {
    const data = await fetchJson("/api/sources");
    state.sources = data.sources || {};
    const selected = dom.source.value;
    dom.source.innerHTML = Object.keys(state.sources)
      .map((key) => `<option value="${escapeText(key)}">${escapeText(sourceDisplayName(key))}</option>`)
      .join("");
    dom.source.value = state.sources[selected] ? selected : Object.keys(state.sources)[0] || "";
    renderCameraList();
    renderWall();
    updateFramesLabel();
    syncFeaturedCamera();
  } catch (error) {
    dom.camList.innerHTML = `<p class="empty">Could not load camera links: ${escapeText(error.message)}</p>`;
  }
}

function renderCameraList() {
  const selected = dom.source.value;
  const entries = Object.entries(state.sources).filter(([key]) => {
    const live = sourceIsLive(key);
    if (state.cameraFilter === "live" && !live) return false;
    if (state.cameraFilter === "recorded" && live) return false;
    if (state.cameraFilter === "alerts") {
      return key === selected && state.incidents.some((incident) => incident.open);
    }
    const text = `${sourceDisplayName(key)} ${key}`.toLowerCase();
    return !state.searchTerm || text.includes(state.searchTerm);
  });
  dom.camCount.textContent = String(Object.keys(state.sources).length);
  if (!entries.length) {
    dom.camList.innerHTML = '<p class="empty">No cameras match this filter.</p>';
    return;
  }
  dom.camList.innerHTML = entries.map(([key]) => {
    const label = sourceDisplayName(key);
    const id = sourceCameraId(key);
    const thumbnail = sourceThumbnail(key);
    const preview = thumbnail
      ? `<img loading="lazy" src="${thumbnail}" alt=""><span class="thumb-live">STILL</span>`
      : '<span class="mini-road" aria-hidden="true"></span>';
    return `<button type="button" class="cam-item${key === selected ? " selected" : ""}" data-source="${escapeText(key)}" aria-pressed="${key === selected}">
      <span class="thumb">${preview}</span>
      <span class="ci-body"><span class="ci-name">${escapeText(label)}</span><span class="ci-sub">${id} · ${sourceIsLive(key) ? "YouTube live" : "Recorded sample"}</span></span>
      <span class="status-dot ${key === selected ? "selected" : "online"}"></span>
    </button>`;
  }).join("");
  dom.camList.querySelectorAll("[data-source]").forEach((button) => {
    button.addEventListener("click", () => selectSource(button.dataset.source));
  });
}

function renderWall() {
  if (!dom.wallGrid) return;
  dom.wallGrid.querySelectorAll(".mini-tile").forEach((tile) => tile.remove());
  const entries = Object.keys(state.sources).filter((key) => key !== dom.source.value);
  for (const key of entries) {
    const label = sourceDisplayName(key);
    const thumbnail = sourceThumbnail(key);
    const media = thumbnail
      ? `<div class="mini-media"><img loading="lazy" src="${thumbnail}" alt=""></div>`
      : '<div class="mini-media"><span class="mini-road" aria-hidden="true"></span></div>';
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "tile mini-tile";
    tile.dataset.source = key;
    tile.setAttribute("aria-label", `Feature ${label}`);
    tile.innerHTML = `${media}<span class="mini-tile-overlay">
      <span class="mini-tile-top"><span class="cam-id">${sourceCameraId(key)}</span><span class="mini-state">${sourceIsLive(key) ? "STILL" : "REC"}</span></span>
      <span class="mini-tile-bottom"><span class="mini-name">${escapeText(label)}</span></span>
    </span>`;
    tile.addEventListener("click", () => selectSource(key));
    dom.wallGrid.appendChild(tile);
  }
  dom.wallSub.textContent = `${Object.keys(state.sources).length} feeds · selected camera analyzed live`;
  syncFeaturedCamera();
}

function syncFeaturedCamera() {
  if (!dom.featuredTile) return;
  const key = dom.source.value;
  const label = sourceDisplayName(key);
  const active = state.status === "running" && state.session?.source?.key === key;
  dom.featuredTile.dataset.source = key;
  dom.featuredId.textContent = sourceCameraId(key);
  dom.featuredName.textContent = label;
  dom.featuredArea.textContent = sourceIsLive(key) ? "LIVE CAMERA" : "RECORDED SAMPLE";
  dom.featuredLive.className = `live-badge ${active ? "active" : "ready"}`;
  dom.featuredLive.innerHTML = `<i class="rec"></i>${active ? "LIVE" : sourceIsLive(key) ? "READY" : "RECORDED"}`;
  dom.assignmentTag.textContent = sourceCameraId(key);
  dom.camList.querySelectorAll("[data-source]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.source === key);
    const dot = button.querySelector(".status-dot");
    if (dot) dot.className = `status-dot ${button.dataset.source === key ? "selected" : "online"}`;
  });
}

function resetMonitoringView() {
  state.sessionId = null;
  state.session = null;
  state.status = "idle";
  state.history = [];
  state.incidents = [];
  state.advisory = {};
  state.startedAt = null;
  state.finishedAt = null;
  state.cursor = 0;
  state.syncedPolicy = null;
  playback.clear();
  dom.reportLink.hidden = true;
  dom.policyApply.disabled = true;
  dom.kpiActive.textContent = "0";
  dom.kpiActiveSub.textContent = "No feed";
  dom.kpiCongestion.textContent = "0";
  dom.kpiCongestionSub.textContent = "Normal range";
  dom.kpiSpeed.textContent = "0";
  dom.laneSummary.textContent = "Awaiting telemetry";
  updateProgress({ status: "idle", progress: 0, sampled_frames: 0, frames_total: 0, live: false });
  setConnection("", "Standby");
  renderAdvisory();
  renderIncidents();
  renderCharts();
}

function selectSource(key) {
  if (!state.sources[key]) return;
  if (state.busy) {
    showFormError("Stop the current monitoring session before switching cameras.");
    return;
  }
  if (state.session && state.session.source?.key !== key) resetMonitoringView();
  dom.source.value = key;
  showFormError("");
  updateFramesLabel();
  renderCameraList();
  renderWall();
  refreshPreview();
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function applySession(data, { loadReplay = true, syncPolicy = false } = {}) {
  const previousId = state.sessionId;
  state.sessionId = data.id;
  state.session = data;
  state.status = data.status;
  state.history = Array.isArray(data.history) ? data.history : [];
  state.incidents = Array.isArray(data.incidents) ? data.incidents : [];
  state.advisory = data.advisory || {};
  state.startedAt = data.started_at || null;
  state.finishedAt = data.finished_at || null;
  state.fps = Number(data.fps) || state.fps;
  if (data.source?.key && state.sources[data.source.key]) dom.source.value = data.source.key;
  if (syncPolicy || state.syncedPolicy !== data.id) {
    state.syncedPolicy = data.id;
    state.policy = { ...state.policy, ...(data.policy || {}) };
    applyPolicyControls();
  }

  const sourceName = sourceDisplayName(data.source?.key || dom.source.value);
  dom.feedName.textContent = sourceName;
  dom.detectionLabel.textContent = data.mode === "server" ? "Engine" : "Local";
  dom.overlayCamera.textContent = `${sourceCameraId(data.source?.key || dom.source.value)} · ${sourceName}`;
  dom.overlaySource.textContent = data.source?.width
    ? `${data.source.width}×${data.source.height} · ${data.source.fps} FPS`
    : "—";
  updateFramesLabel();
  renderCameraList();
  renderWall();
  updateProgress(data);
  updateHeaderStats();

  if (data.status === "running" || data.status === "queued") {
    setRunningControls(true);
    setConnection("busy", "Monitoring");
    setStage("Live monitoring", `${data.preset?.name || "Mainline"} · ${sourceName}`, "LIVE", "running");
    syncFeaturedCamera();
    playback.setLive(data.id);
    const latest = state.history[state.history.length - 1];
    if (latest) {
      state.cursor = state.history.length - 1;
      playback.showLive(`/api/sessions/${data.id}/frame?t=${latest.frame}`);
      renderSnapshot(latest);
    } else {
      dom.playClock.textContent = "LIVE";
    }
    renderAdvisory();
    renderIncidents();
    renderCharts();
    return;
  }

  setRunningControls(false);
  dom.cancel.hidden = true;
  syncFeaturedCamera();
  if (data.status === "done") {
    setConnection("live", "Complete");
    setStage("Replay", `${data.preset?.name || "Mainline"} · ${sourceName}`, "COMPLETE", "done");
    dom.reportLink.hidden = false;
    dom.reportLink.href = `/api/sessions/${data.id}/report`;
    renderAdvisory();
    renderIncidents();
    if (loadReplay && (previousId !== data.id || playback.mode !== "replay")) {
      playback.load({
        sessionId: data.id,
        history: state.history,
        incidents: state.incidents,
        fps: state.fps,
      });
    } else {
      playback.updateIncidents(state.incidents);
      playback.seek(Math.min(state.cursor, Math.max(0, state.history.length - 1)));
    }
    refreshSessions();
  } else if (data.status === "error") {
    setConnection("error", "Fault");
    setStage("Fault", "Monitoring interrupted", "FAULT", "error");
    showFormError(data.error || "The analysis stopped unexpectedly.");
    if (data.report_ready) {
      dom.reportLink.hidden = false;
      dom.reportLink.href = `/api/sessions/${data.id}/report`;
    } else {
      dom.reportLink.hidden = true;
    }
    playback.pause();
    renderCharts();
    renderAdvisory();
    renderIncidents();
    refreshSessions();
  } else if (data.status === "cancelled") {
    setConnection("", "Stopped");
    setStage("Stopped", "Monitoring stopped by operator", "STOPPED", "cancelled");
    if (data.report_ready) {
      dom.reportLink.hidden = false;
      dom.reportLink.href = `/api/sessions/${data.id}/report`;
    } else {
      dom.reportLink.hidden = true;
    }
    playback.pause();
    if (state.history.length) {
      playback.load({ sessionId: data.id, history: state.history, incidents: state.incidents, fps: state.fps });
    }
    renderCharts();
    renderAdvisory();
    renderIncidents();
    refreshSessions();
  }
}

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  const lanes = snapshot.lanes || {};
  const active = Number(snapshot.active) || 0;
  const congestion = Number(snapshot.congestion) || 0;
  dom.kpiActive.textContent = String(active);
  dom.kpiActiveSub.textContent = `${Object.keys(lanes).length} lane${Object.keys(lanes).length === 1 ? "" : "s"} monitored`;
  dom.kpiCongestion.textContent = String(Math.round(congestion));
  dom.kpiCongestionSub.textContent = congestionLabel(congestion);
  dom.kpiSpeed.textContent = String(Math.round(Number(snapshot.speed) || 0));

  dom.overlayTime.textContent = `T+${clock(snapshot.t)}`;
  dom.overlayFrame.textContent = `FRAME ${String(snapshot.frame || 0).padStart(4, "0")}`;
  dom.laneSummary.textContent = `${active} vehicles · queue ${snapshot.queue || 0} · congestion ${Math.round(congestion)}`;
}

function updateHeaderStats() {
  const open = (state.incidents || []).filter((incident) => incident.open).length;
  dom.alertCount.textContent = String(open);
  dom.headerAlerts.classList.toggle("has-alerts", open > 0);
}

function congestionLabel(value) {
  if (value >= 70) return "Congested";
  if (value >= 45) return "Elevated";
  if (value >= 20) return "Guarded";
  return "Normal range";
}

function renderAdvisory() {
  const advisory = state.advisory || {};
  const kind = advisory.state || "normal";
  dom.advisoryState.textContent = kind.toUpperCase();
  dom.advisoryState.className = `status-chip ${kind}`;
  dom.advisoryPriority.textContent = String(advisory.priority || "low").toUpperCase();
  dom.advisoryMetering.textContent = String(advisory.metering || "OPEN");
  const actions = advisory.actions || ["Maintain normal operations"];
  dom.advisoryActions.innerHTML = actions.map((item) => `<li>${escapeText(item)}</li>`).join("");
  dom.advisoryReasons.textContent = (advisory.reasons || []).join(" · ") || "No active alerts; flow within normal range";
}

function renderIncidents() {
  const incidents = [...(state.incidents || [])].sort((a, b) => (b.start_frame || 0) - (a.start_frame || 0));
  const open = incidents.filter((item) => item.open).length;
  dom.incidentCount.textContent = String(incidents.length);
  updateHeaderStats();
  const running = state.status === "running" || state.status === "queued";
  dom.eventStatus.textContent = running
    ? `${open} open incident${open === 1 ? "" : "s"} · monitoring selected camera`
    : "Awaiting monitoring session";
  dom.eventStatusDot.className = `status-dot ${running ? "online" : ""}`;
  renderCameraList();
  if (!incidents.length) {
    dom.incidentList.innerHTML = `<p class="empty">${running ? "No incidents detected on this feed." : "Start monitoring to populate the log."}</p>`;
    return;
  }
  dom.incidentList.innerHTML = incidents
    .map(
      (incident, index) => `<button type="button" class="incident-item ${incident.severity || "low"}" data-incident="${index}">
        <span class="incident-top">
          <span class="incident-type">${incidentLabel(incident.type)} · ${incident.lane === "ALL" ? "MAINLINE" : `LANE ${incident.lane}`}</span>
          <span class="incident-sev">${String(incident.severity || "low").toUpperCase()}</span>
        </span>
        <span class="incident-msg">${escapeText(incident.message || "")}</span>
        <span class="incident-meta"><span>${clock(incident.start_time)} – ${clock(incident.end_time)}</span><span>${incident.open ? "OPEN" : `T+${clock(incident.end_time)}`}</span></span>
      </button>`
    )
    .join("");
  dom.incidentList.querySelectorAll("[data-incident]").forEach((button) => {
    button.addEventListener("click", () => {
      const incident = incidents[Number(button.dataset.incident)];
      jumpToFrame(incident?.start_frame);
    });
  });
}

function renderCharts() {
  const lanes = Object.keys(state.history[state.history.length - 1]?.lanes || state.session?.lanes || {});
  renderLegend(dom.congestionLegend, lanes);
  renderCongestionChart(dom.congestionChart, state.history, state.incidents, state.cursor);
  renderQueueChart(dom.queueChart, state.history, state.cursor);
}

function jumpToFrame(frameNumber) {
  if (!Number.isFinite(frameNumber)) return;
  const index = state.history.findIndex((item) => item.frame === frameNumber);
  if (index < 0) return;
  playback.pause();
  playback.seek(index);
}

function applyPolicyControls() {
  const stopped = Number(state.policy.stopped_speed ?? 55);
  const duration = Number(state.policy.stopped_seconds ?? 1.5);
  const congestion = Number(state.policy.congestion_high ?? 70);
  dom.stopped.value = String(stopped);
  dom.duration.value = String(duration);
  dom.congestion.value = String(congestion);
  updatePolicyLabels();
}

function updatePolicyLabels() {
  dom.stoppedOut.textContent = dom.stopped.value;
  dom.durationOut.textContent = `${dom.duration.value} s`;
  dom.congestionOut.textContent = dom.congestion.value;
}

async function applyPolicy() {
  if (!state.sessionId) return;
  dom.policyApply.disabled = true;
  try {
    const policy = {
      stopped_speed: Number(dom.stopped.value),
      stopped_seconds: Number(dom.duration.value),
      congestion_high: Number(dom.congestion.value),
    };
    const data = await fetchJson(`/api/sessions/${state.sessionId}/what-if`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy }),
    });
    state.policy = data.policy;
    state.incidents = data.incidents || [];
    state.advisory = data.advisory || {};
    playback.updateIncidents(state.incidents);
    renderAdvisory();
    renderIncidents();
    renderCharts();
    showFormError("");
  } catch (error) {
    showFormError(error.message);
  } finally {
    dom.policyApply.disabled = false;
  }
}

async function startMonitoring(event) {
  event.preventDefault();
  if (state.busy) return;
  showFormError("");
  state.busy = true;
  state.sessionId = null;
  state.session = null;
  state.status = "queued";
  state.history = [];
  state.incidents = [];
  state.advisory = {};
  state.cursor = 0;
  state.syncedPolicy = null;
  dom.reportLink.hidden = true;
  dom.playToggle.disabled = true;
  dom.incidentList.innerHTML = '<p class="empty">Monitoring in progress.</p>';
  dom.incidentCount.textContent = "0";
  setRunningControls(true);
  dom.cancel.hidden = true;
  setConnection("busy", "Starting");
  setStage("Deployment", "Requesting camera feed", "QUEUED", "running");
  dom.placeholder.hidden = false;
  dom.placeholder.querySelector("p").textContent = selectedSourceIsLive()
    ? "Resolving live camera feed…"
    : "Preparing camera feed…";
  dom.frame.classList.remove("visible");
  updateProgress({
    progress: 0,
    sampled_frames: 0,
    frames_total: Number(dom.frames.value),
    status: "queued",
    live: selectedSourceIsLive(),
  });
  dom.runElapsed.textContent = "00:00";

  const payload = {
    source: dom.source.value,
    preset: dom.preset.value,
    frames: Number(dom.frames.value),
    fps: Number(dom.fps.value),
    mode: dom.mode.value,
    url: dom.url.value,
    model: dom.model.value,
    confidence: Number(dom.confidence.value),
  };
  try {
    const data = await fetchJson("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.sessionId = data.id;
    state.startedAt = Date.now() / 1000;
    state.finishedAt = null;
    playback.setLive(data.id);
    dom.cancel.hidden = false;
    poll();
    state.pollTimer = setInterval(poll, 600);
    refreshSessions();
  } catch (error) {
    state.busy = false;
    state.status = "error";
    setRunningControls(false);
    setConnection("error", "Fault");
    setStage("Fault", "Could not start monitoring", "FAULT", "error");
    showFormError(error.message);
  }
}

async function poll() {
  if (!state.sessionId) return;
  try {
    const data = await fetchJson(`/api/sessions/${state.sessionId}`);
    const finished = ["done", "error", "cancelled"].includes(data.status);
    applySession(data, { loadReplay: true });
    state.busy = data.status === "running" || data.status === "queued";
    if (finished) {
      stopPolling();
      state.busy = false;
    }
  } catch (error) {
    stopPolling();
    state.busy = false;
    setRunningControls(false);
    setConnection("error", "Link lost");
    showFormError(error.message);
  }
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function cancelMonitoring() {
  if (!state.sessionId) return;
  dom.cancel.disabled = true;
  try {
    await fetchJson(`/api/sessions/${state.sessionId}/cancel`, { method: "POST" });
    dom.cancel.disabled = false;
  } catch (error) {
    dom.cancel.disabled = false;
    showFormError(error.message);
  }
}

async function loadSession(sessionId) {
  if (state.busy) {
    showFormError("Stop the current deployment before opening another session.");
    return;
  }
  try {
    const data = await fetchJson(`/api/sessions/${sessionId}`);
    state.busy = false;
    state.cursor = 0;
    applySession(data, { loadReplay: true, syncPolicy: true });
    if (data.status === "running" || data.status === "queued") {
      state.busy = true;
      poll();
      state.pollTimer = setInterval(poll, 600);
    }
    refreshSessions();
  } catch (error) {
    showFormError(error.message);
  }
}

async function refreshSessions() {
  try {
    const data = await fetchJson("/api/sessions");
    const sessions = data.sessions || [];
    dom.sessionCount.textContent = String(sessions.length);
    if (!sessions.length) {
      dom.sessionList.innerHTML = '<p class="empty">No completed deployments.</p>';
      return;
    }
    dom.sessionList.innerHTML = sessions
      .map((session) => {
        const total = session.incident_summary?.total || 0;
        const active = session.id === state.sessionId ? " active" : "";
        return `<button type="button" class="session-item${active}" data-session="${session.id}">
          <span class="session-meta"><strong>${escapeText(session.source?.name || session.source?.key || "Feed")}</strong><span>${statusLabel(session.status)}</span></span>
          <span>${escapeText(session.preset?.name || "Lane preset")} · ${timestamp(session.started_at)}</span>
          <span>${total} incident${total === 1 ? "" : "s"} · ${Math.round(session.summary?.congestion || 0)} congestion</span>
        </button>`;
      })
      .join("");
    dom.sessionList.querySelectorAll("[data-session]").forEach((button) => {
      button.addEventListener("click", () => loadSession(button.dataset.session));
    });
  } catch {
    // Session history is secondary; polling errors are surfaced elsewhere.
  }
}

async function loadPresets() {
  try {
    const data = await fetchJson("/api/presets");
    state.presets = data.presets || {};
  } catch {
    state.presets = {};
  }
}

function escapeText(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function incidentLabel(type) {
  return {
    stopped_vehicle: "STOPPED VEHICLE",
    queue_buildup: "QUEUE BUILDUP",
    network_congestion: "CONGESTION",
    lane_imbalance: "IMBALANCE",
  }[type] || String(type || "ALERT").toUpperCase();
}

dom.form.addEventListener("submit", startMonitoring);
dom.run.addEventListener("click", () => dom.form.requestSubmit());
dom.cancel.addEventListener("click", cancelMonitoring);
dom.source.addEventListener("change", () => {
  selectSource(dom.source.value);
});
dom.preset.addEventListener("change", refreshPreview);
dom.viewTabs.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-view]");
  if (tab) switchView(tab.dataset.view);
});
document.querySelectorAll(".header-metric").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".header-metric").forEach((metric) => {
      const selected = metric === button;
      metric.classList.toggle("selected", selected);
      metric.setAttribute("aria-pressed", String(selected));
    });
    document.getElementById("telemetry-card")?.classList.add("metric-focused");
  });
});
dom.chips.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-f]");
  if (!chip) return;
  dom.chips.querySelectorAll(".chip").forEach((item) => {
    const selected = item === chip;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
  state.cameraFilter = chip.dataset.f;
  renderCameraList();
});
dom.searchInput.addEventListener("input", () => {
  state.searchTerm = dom.searchInput.value.trim().toLowerCase();
  renderCameraList();
});
dom.cycleButton.addEventListener("click", () => {
  if (state.cycleTimer) {
    clearInterval(state.cycleTimer);
    state.cycleTimer = null;
    dom.cycleButton.classList.remove("on");
    dom.cycleButton.textContent = "Auto-cycle";
    return;
  }
  if (state.busy) {
    showFormError("Auto-cycle is unavailable during an active monitoring session.");
    return;
  }
  dom.cycleButton.classList.add("on");
  dom.cycleButton.textContent = "Cycling…";
  state.cycleTimer = setInterval(() => {
    const keys = Object.keys(state.sources);
    if (keys.length < 2 || state.busy) return;
    const index = keys.indexOf(dom.source.value);
    selectSource(keys[(index + 1) % keys.length]);
  }, 4500);
});
dom.fullscreenButton.addEventListener("click", () => {
  const target = dom.wallGrid;
  if (target?.requestFullscreen) target.requestFullscreen().catch(() => {});
  else if (target?.webkitRequestFullscreen) target.webkitRequestFullscreen();
});
dom.mode.addEventListener("change", () => {
  dom.detectionLabel.textContent = dom.mode.value === "local" ? "Local" : "Engine";
});
dom.stopped.addEventListener("input", updatePolicyLabels);
dom.duration.addEventListener("input", updatePolicyLabels);
dom.congestion.addEventListener("input", updatePolicyLabels);
dom.policyApply.addEventListener("click", applyPolicy);

document.addEventListener("keydown", (event) => {
  const tag = event.target?.tagName?.toLowerCase();
  if (["input", "select", "textarea", "button"].includes(tag)) return;
  if (event.code === "Space") {
    event.preventDefault();
    playback.toggle();
  } else if (event.code === "ArrowRight") {
    event.preventDefault();
    playback.pause();
    playback.seek(state.cursor + 1);
  } else if (event.code === "ArrowLeft") {
    event.preventDefault();
    playback.pause();
    playback.seek(state.cursor - 1);
  }
});

setInterval(() => {
  dom.clock.textContent = localClock();
  updateElapsed();
}, 1000);
dom.clock.textContent = localClock();

renderAdvisory();
renderIncidents();
renderCharts();
updatePolicyLabels();
updateFramesLabel();
const requestedView = new URLSearchParams(window.location.search).get("view");
switchView(requestedView === "about" ? "about-view" : "info-view");
Promise.all([loadSources(), loadPresets()]).then(() => {
  renderCameraList();
  renderWall();
  refreshPreview();
  refreshSessions();
});
