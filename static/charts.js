const LANE_COLORS = ["#42cf7f", "#4cb9e8", "#f0a83a", "#c084fc", "#f472b6", "#f87171", "#a3e635", "#22d3ee"];

export function laneColor(lane, index) {
  const offset = lane.charCodeAt(0) - 65;
  return LANE_COLORS[(Number.isFinite(offset) ? offset : index) % LANE_COLORS.length];
}

export function renderLegend(container, lanes) {
  if (!container) return;
  container.innerHTML = lanes
    .map((lane, index) => `<span><i class="swatch" style="background:${laneColor(lane, index)}"></i>Lane ${lane}</span>`)
    .join("");
}

export function renderCongestionChart(container, history, incidents, cursor) {
  if (!container) return;
  if (!history || history.length < 2) {
    container.innerHTML = '<p class="empty">Telemetry appears when monitoring starts.</p>';
    return;
  }
  const width = 720;
  const height = 178;
  const pad = { left: 32, right: 8, top: 12, bottom: 22 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const lanes = Object.keys(history[history.length - 1].lanes || {});
  const x = (index) => pad.left + (index / Math.max(1, history.length - 1)) * innerWidth;
  const y = (value) => pad.top + innerHeight - (Math.max(0, Math.min(100, value)) / 100) * innerHeight;
  const frameIndex = new Map(history.map((item, index) => [item.frame, index]));

  let svg = `<svg viewBox="0 0 ${width} ${height}" role="presentation">`;
  for (const incident of incidents || []) {
    const start = frameIndex.get(incident.start_frame);
    const end = frameIndex.get(incident.end_frame);
    if (start === undefined || end === undefined) continue;
    const left = x(start);
    const right = Math.max(x(end), left + 2);
    svg += `<rect class="incident-band ${incident.severity || "low"}" x="${left.toFixed(1)}" y="${pad.top}" width="${(right - left).toFixed(1)}" height="${innerHeight}"></rect>`;
  }
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = tick * 25;
    const yy = y(value);
    svg += `<line class="grid-line" x1="${pad.left}" y1="${yy.toFixed(1)}" x2="${width - pad.right}" y2="${yy.toFixed(1)}"></line>`;
    svg += `<text class="axis-label" x="${pad.left - 6}" y="${(yy + 3).toFixed(1)}" text-anchor="end">${value}</text>`;
  }
  for (let tick = 0; tick <= 4; tick += 1) {
    const index = Math.round((history.length - 1) * (tick / 4));
    const xx = x(index);
    const seconds = history[index]?.t ?? 0;
    svg += `<text class="axis-label" x="${xx.toFixed(1)}" y="${height - 6}" text-anchor="${tick === 0 ? "start" : tick === 4 ? "end" : "middle"}">${formatSeconds(seconds)}</text>`;
  }
  lanes.forEach((lane, index) => {
    const points = history.map((item, itemIndex) => `${x(itemIndex).toFixed(1)},${y(item.lanes[lane]?.congestion || 0).toFixed(1)}`);
    const color = laneColor(lane, index);
    const area = `M ${pad.left},${pad.top + innerHeight} L ${points.join(" L ")} L ${x(history.length - 1).toFixed(1)},${pad.top + innerHeight} Z`;
    svg += `<path class="series-area" d="${area}" fill="${color}"></path>`;
    svg += `<path class="series-line" d="M ${points.join(" L ")}" stroke="${color}"></path>`;
    const last = history[history.length - 1].lanes[lane]?.congestion || 0;
    svg += `<text class="axis-label" x="${width - pad.right}" y="${Math.max(10, y(last) - 4).toFixed(1)}" text-anchor="end" fill="${color}">${Math.round(last)}</text>`;
  });
  if (Number.isInteger(cursor) && cursor >= 0 && cursor < history.length) {
    const xx = x(cursor);
    svg += `<line class="cursor-line" x1="${xx.toFixed(1)}" y1="${pad.top}" x2="${xx.toFixed(1)}" y2="${pad.top + innerHeight}"></line>`;
  }
  svg += "</svg>";
  container.innerHTML = svg;
}

export function renderQueueChart(container, history, cursor) {
  if (!container) return;
  if (!history || history.length < 2) {
    container.innerHTML = '<p class="empty">Demand history appears when monitoring starts.</p>';
    return;
  }
  const width = 720;
  const height = 178;
  const pad = { left: 32, right: 8, top: 12, bottom: 22 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const maxQueue = Math.max(4, ...history.map((item) => Number(item.queue) || 0));
  const maxOccupancy = Math.max(4, ...history.map((item) => Number(item.active) || 0));
  const maxValue = Math.max(maxQueue, maxOccupancy);
  const x = (index) => pad.left + (index / Math.max(1, history.length - 1)) * innerWidth;
  const y = (value) => pad.top + innerHeight - (value / maxValue) * innerHeight;
  const barWidth = Math.max(1, innerWidth / history.length - 1);

  let svg = `<svg viewBox="0 0 ${width} ${height}" role="presentation">`;
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = (maxValue * tick) / 4;
    const yy = y(value);
    svg += `<line class="grid-line" x1="${pad.left}" y1="${yy.toFixed(1)}" x2="${width - pad.right}" y2="${yy.toFixed(1)}"></line>`;
    svg += `<text class="axis-label" x="${pad.left - 6}" y="${(yy + 3).toFixed(1)}" text-anchor="end">${Math.round(value)}</text>`;
  }
  history.forEach((item, index) => {
    const xx = x(index) - barWidth / 2;
    const top = y(Number(item.queue) || 0);
    svg += `<rect x="${xx.toFixed(1)}" y="${top.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${Math.max(0, pad.top + innerHeight - top).toFixed(1)}" fill="rgba(240,168,58,.38)"></rect>`;
  });
  const occupancyPoints = history.map((item, index) => `${x(index).toFixed(1)},${y(Number(item.active) || 0).toFixed(1)}`);
  svg += `<path class="series-line" d="M ${occupancyPoints.join(" L ")}" stroke="#4cb9e8"></path>`;
  for (let tick = 0; tick <= 4; tick += 1) {
    const index = Math.round((history.length - 1) * (tick / 4));
    const xx = x(index);
    const seconds = history[index]?.t ?? 0;
    svg += `<text class="axis-label" x="${xx.toFixed(1)}" y="${height - 6}" text-anchor="${tick === 0 ? "start" : tick === 4 ? "end" : "middle"}">${formatSeconds(seconds)}</text>`;
  }
  if (Number.isInteger(cursor) && cursor >= 0 && cursor < history.length) {
    const xx = x(cursor);
    svg += `<line class="cursor-line" x1="${xx.toFixed(1)}" y1="${pad.top}" x2="${xx.toFixed(1)}" y2="${pad.top + innerHeight}"></line>`;
  }
  svg += "</svg>";
  container.innerHTML = svg;
}

function formatSeconds(value) {
  const seconds = Math.max(0, Number(value) || 0);
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
