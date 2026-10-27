const PLAY_PATH = "M1 1l10 6-10 6z";
const PAUSE_PATH = "M1 1h4v12H1zM7 1h4v12H7z";

export class Playback {
  constructor({ frame, scrubber, clock, button, icon, marks, onFrame }) {
    this.frame = frame;
    this.scrubber = scrubber;
    this.clock = clock;
    this.button = button;
    this.icon = icon;
    this.marks = marks;
    this.onFrame = onFrame;
    this.sessionId = null;
    this.history = [];
    this.incidents = [];
    this.index = 0;
    this.fps = 5;
    this.mode = "idle";
    this.timer = null;
    this.imageCache = new Map();

    this.button.addEventListener("click", () => this.toggle());
    this.scrubber.addEventListener("input", () => this.seek(Number(this.scrubber.value)));
    this.step = this.step.bind(this);
  }

  setLive(sessionId) {
    this.pause();
    this.sessionId = sessionId;
    this.mode = "live";
    this.history = [];
    this.incidents = [];
    this.index = 0;
    this.scrubber.disabled = true;
    this.scrubber.max = "0";
    this.scrubber.value = "0";
    this.button.disabled = true;
    this.button.classList.remove("playing");
    this.icon.setAttribute("d", PLAY_PATH);
    this.clock.textContent = "LIVE";
    this.marks.innerHTML = "";
    this.frame.onload = null;
    this.frame.onerror = null;
  }

  clear() {
    this.pause();
    this.sessionId = null;
    this.history = [];
    this.incidents = [];
    this.index = 0;
    this.mode = "idle";
    this.imageCache.clear();
    this.scrubber.disabled = true;
    this.scrubber.max = "0";
    this.scrubber.value = "0";
    this.button.disabled = true;
    this.clock.textContent = "00:00 / 00:00";
    this.marks.innerHTML = "";
    this.frame.onload = null;
    this.frame.onerror = null;
  }

  showLive(url) {
    if (this.mode !== "live") return;
    if (this.frame.src.endsWith(url)) return;
    this.frame.onload = null;
    this.frame.onerror = null;
    this.frame.src = url;
    this.frame.classList.add("visible");
  }

  load({ sessionId, history, incidents, fps, autoplay = false }) {
    this.pause();
    this.sessionId = sessionId;
    this.mode = "replay";
    this.history = Array.isArray(history) ? history : [];
    this.incidents = Array.isArray(incidents) ? incidents : [];
    this.fps = Math.max(0.5, Number(fps) || 5);
    this.imageCache.clear();
    this.scrubber.disabled = this.history.length < 2;
    this.scrubber.max = String(Math.max(0, this.history.length - 1));
    this.button.disabled = this.history.length < 2;
    this.renderMarks();
    this.seek(Math.max(0, this.history.length - 1));
    if (autoplay && this.history.length > 1) this.play();
  }

  updateIncidents(incidents) {
    this.incidents = Array.isArray(incidents) ? incidents : [];
    this.renderMarks();
  }

  seek(index) {
    if (!this.history.length) return;
    const bounded = Math.max(0, Math.min(this.history.length - 1, Number(index) || 0));
    this.index = bounded;
    this.scrubber.value = String(bounded);
    const item = this.history[bounded];
    if (this.mode === "replay" && item) {
      this.setImage(`/api/sessions/${this.sessionId}/frames/${item.frame}`);
      this.preload(bounded + 1);
      this.clock.textContent = `${clock(item.t)} / ${clock(this.history[this.history.length - 1].t)}`;
      if (this.onFrame) this.onFrame(item, bounded);
    }
  }

  setImage(url) {
    this.frame.onload = null;
    this.frame.onerror = null;
    if (this.imageCache.has(url)) {
      this.frame.src = this.imageCache.get(url).src;
    } else {
      this.frame.src = url;
    }
    this.frame.classList.add("visible");
  }

  preload(index) {
    const item = this.history[index];
    if (!item) return;
    const url = `/api/sessions/${this.sessionId}/frames/${item.frame}`;
    if (this.imageCache.has(url)) return;
    const image = new Image();
    image.src = url;
    this.imageCache.set(url, image);
  }

  play() {
    if (this.mode !== "replay" || this.history.length < 2) return;
    if (this.index >= this.history.length - 1) this.seek(0);
    this.button.classList.add("playing");
    this.icon.setAttribute("d", PAUSE_PATH);
    this.button.setAttribute("aria-label", "Pause replay");
    clearInterval(this.timer);
    this.timer = setInterval(this.step, 1000 / this.fps);
  }

  pause() {
    clearInterval(this.timer);
    this.timer = null;
    this.button.classList.remove("playing");
    this.icon.setAttribute("d", PLAY_PATH);
    this.button.setAttribute("aria-label", "Play replay");
  }

  toggle() {
    if (this.timer) this.pause();
    else this.play();
  }

  step() {
    if (this.index >= this.history.length - 1) {
      this.pause();
      return;
    }
    this.seek(this.index + 1);
  }

  renderMarks() {
    if (!this.marks) return;
    this.marks.innerHTML = "";
    if (this.mode !== "replay" || !this.history.length) return;
    const frameIndex = new Map(this.history.map((item, index) => [item.frame, index]));
    for (const incident of this.incidents) {
      const index = frameIndex.get(incident.start_frame);
      if (index === undefined) continue;
      const left = (index / Math.max(1, this.history.length - 1)) * 100;
      const mark = document.createElement("button");
      mark.type = "button";
      mark.className = `incident-mark ${incident.severity || "low"}`;
      mark.style.left = `${left}%`;
      mark.title = `${incident.message} · ${clock(incident.start_time)}`;
      mark.setAttribute("aria-label", `${incident.message} at ${clock(incident.start_time)}`);
      mark.addEventListener("click", () => {
        this.pause();
        this.seek(index);
      });
      this.marks.appendChild(mark);
    }
  }
}

function clock(value) {
  const seconds = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
