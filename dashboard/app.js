/* ── AgentLens Dashboard — App Logic ──────────────────────────────────── */

const API_BASE = window.location.origin;

let currentSession = null;
let compareSelection = []; // Array of {id, name} for comparison
let analyticsData = null;  // Cached analytics data
let analyticsVisible = false;

// ── Initialization ──────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  loadSessions();

  document.getElementById("statusFilter").addEventListener("change", () => {
    loadSessions();
  });
});

// ── Session List ────────────────────────────────────────────────────

async function loadSessions() {
  const listEl = document.getElementById("sessionList");
  listEl.innerHTML = '<div class="loading">Loading sessions...</div>';

  const status = document.getElementById("statusFilter").value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);

  try {
    const res = await fetch(`${API_BASE}/sessions?${params}`);
    const data = await res.json();

    if (!data.sessions || data.sessions.length === 0) {
      listEl.innerHTML = '<div class="loading">No sessions found. Run the demo agent or seed the database.</div>';
      return;
    }

    listEl.innerHTML = data.sessions.map((s) => `
      <div class="session-card ${compareSelection.some(c => c.id === s.session_id) ? 'selected' : ''}" data-session-id="${s.session_id}">
        <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
          <input type="checkbox" class="compare-checkbox"
            ${compareSelection.some(c => c.id === s.session_id) ? 'checked' : ''}
            onclick="event.stopPropagation(); toggleCompare('${s.session_id}', '${escHtml(s.agent_name)}')"
            title="Select for comparison">
          <div class="session-card-left" onclick="loadSessionDetail('${s.session_id}')">
            <div class="session-agent">${escHtml(s.agent_name)}</div>
            <div class="session-meta">
              <span>🆔 ${s.session_id.slice(0, 8)}…</span>
              <span>🕐 ${formatTime(s.started_at)}</span>
              ${s.metadata?.version ? `<span>📦 v${escHtml(s.metadata.version)}</span>` : ""}
              ${s.metadata?.environment ? `<span>🌍 ${escHtml(s.metadata.environment)}</span>` : ""}
            </div>
          </div>
        </div>
        <div class="session-card-right" onclick="loadSessionDetail('${s.session_id}')">
          <div class="session-tokens">
            <div class="count">${(s.total_tokens_in + s.total_tokens_out).toLocaleString()}</div>
            <div>tokens</div>
          </div>
          <span class="status-badge ${s.status}">${s.status}</span>
        </div>
      </div>
    `).join("");
  } catch (err) {
    listEl.innerHTML = `<div class="loading">Error loading sessions: ${escHtml(err.message)}</div>`;
  }
}

// ── Session Detail ──────────────────────────────────────────────────

async function loadSessionDetail(sessionId) {
  document.getElementById("sessionListView").classList.remove("active");
  document.getElementById("sessionDetailView").classList.add("active");
  costData = null; // Reset cost data for new session

  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
    currentSession = await res.json();

    document.getElementById("sessionTitle").textContent = currentSession.agent_name;
    renderSessionInfo(currentSession);
    renderTimeline(currentSession.events);
    renderTokenCharts(currentSession.events);
    loadExplanation(sessionId);
  } catch (err) {
    console.error("Error loading session:", err);
  }
}

function showSessionList() {
  document.getElementById("sessionDetailView").classList.remove("active");
  document.getElementById("sessionCompareView").classList.remove("active");
  document.getElementById("sessionListView").classList.add("active");
  currentSession = null;
}

function renderSessionInfo(session) {
  const totalTokens = session.total_tokens_in + session.total_tokens_out;
  const eventCount = session.events ? session.events.length : 0;
  const duration = session.ended_at
    ? formatDuration(new Date(session.started_at), new Date(session.ended_at))
    : "ongoing";

  const modelsUsed = [...new Set(
    session.events.filter(e => e.model).map(e => e.model)
  )];

  document.getElementById("sessionInfo").innerHTML = `
    <div class="info-card">
      <div class="label">Status</div>
      <div class="value"><span class="status-badge ${session.status}">${session.status}</span></div>
    </div>
    <div class="info-card">
      <div class="label">Duration</div>
      <div class="value">${duration}</div>
    </div>
    <div class="info-card">
      <div class="label">Events</div>
      <div class="value">${eventCount}</div>
    </div>
    <div class="info-card">
      <div class="label">Total Tokens</div>
      <div class="value">${totalTokens.toLocaleString()}</div>
    </div>
    <div class="info-card">
      <div class="label">Models</div>
      <div class="value" style="font-size:0.85rem">${modelsUsed.join(", ") || "—"}</div>
    </div>
    <div class="info-card">
      <div class="label">Session ID</div>
      <div class="value" style="font-size:0.75rem;word-break:break-all">${session.session_id}</div>
    </div>
  `;
}

// ── Timeline ────────────────────────────────────────────────────────

function renderTimeline(events) {
  const el = document.getElementById("timeline");

  if (!events || events.length === 0) {
    el.innerHTML = '<div class="loading">No events in this session.</div>';
    return;
  }

  el.innerHTML = events.map((event, i) => {
    const typeClass = event.event_type;
    const time = new Date(event.timestamp).toLocaleTimeString();

    let ioHtml = "";
    if (event.input_data || event.output_data) {
      ioHtml = `<div class="event-io">`;
      if (event.input_data) {
        ioHtml += `
          <div class="event-io-block">
            <div class="io-label">Input</div>
            ${formatJson(event.input_data)}
          </div>`;
      }
      if (event.output_data) {
        ioHtml += `
          <div class="event-io-block">
            <div class="io-label">Output</div>
            ${formatJson(event.output_data)}
          </div>`;
      }
      ioHtml += `</div>`;
    }

    // Tool call details
    let toolHtml = "";
    if (event.tool_call) {
      const tc = event.tool_call;
      toolHtml = `
        <div class="event-io">
          <div class="event-io-block">
            <div class="io-label">Tool: ${escHtml(tc.tool_name)} — Input</div>
            ${formatJson(tc.tool_input)}
          </div>
          <div class="event-io-block">
            <div class="io-label">Tool Output</div>
            ${tc.tool_output ? formatJson(tc.tool_output) : '<em>none</em>'}
          </div>
        </div>`;
    }

    // Reasoning
    let reasoningHtml = "";
    if (event.decision_trace && event.decision_trace.reasoning) {
      reasoningHtml = `
        <div class="event-reasoning">
          <div class="reasoning-label">💡 Decision Reasoning</div>
          ${escHtml(event.decision_trace.reasoning)}
        </div>`;
    }

    // Stats
    let statsHtml = "";
    const stats = [];
    if (event.tokens_in || event.tokens_out) {
      stats.push(`<span>📊 ${event.tokens_in} → ${event.tokens_out} tokens</span>`);
    }
    if (event.duration_ms) {
      stats.push(`<span>⏱ ${event.duration_ms.toFixed(0)}ms</span>`);
    }
    if (event.model) {
      stats.push(`<span>🤖 ${escHtml(event.model)}</span>`);
    }
    if (stats.length > 0) {
      statsHtml = `<div class="event-stats">${stats.join("")}</div>`;
    }

    return `
      <div class="timeline-event ${typeClass}">
        <div class="event-header">
          <div class="event-type">
            <span class="event-type-badge ${typeClass}">${formatEventType(event.event_type)}</span>
            ${event.model ? `<span class="event-model">${escHtml(event.model)}</span>` : ""}
          </div>
          <span class="event-time">${time}</span>
        </div>
        ${ioHtml}
        ${toolHtml}
        ${reasoningHtml}
        ${statsHtml}
      </div>
    `;
  }).join("");
}

// ── Token Charts (Canvas-based) ─────────────────────────────────────

function renderTokenCharts(events) {
  if (!events || events.length === 0) return;

  // Per-event bar chart
  const canvas1 = document.getElementById("tokenChart");
  const ctx1 = canvas1.getContext("2d");
  drawBarChart(ctx1, canvas1, events);

  // Cumulative line chart
  const canvas2 = document.getElementById("cumulativeChart");
  const ctx2 = canvas2.getContext("2d");
  drawCumulativeChart(ctx2, canvas2, events);

  // Token summary table
  renderTokenSummary(events);
}

function drawBarChart(ctx, canvas, events) {
  const w = canvas.width;
  const h = canvas.height;
  const padding = { top: 20, right: 20, bottom: 40, left: 50 };

  ctx.clearRect(0, 0, w, h);

  const tokenEvents = events.filter(e => e.tokens_in > 0 || e.tokens_out > 0);
  if (tokenEvents.length === 0) {
    ctx.fillStyle = "#6e7681";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No token usage data", w / 2, h / 2);
    return;
  }

  const maxTokens = Math.max(...tokenEvents.map(e => e.tokens_in + e.tokens_out));
  const barWidth = Math.max(8, (w - padding.left - padding.right) / tokenEvents.length - 4);
  const chartH = h - padding.top - padding.bottom;

  // Grid lines
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = "#6e7681";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    const val = Math.round(maxTokens * (1 - i / 4));
    ctx.fillText(val.toString(), padding.left - 8, y + 4);
  }

  // Bars
  tokenEvents.forEach((e, i) => {
    const x = padding.left + i * (barWidth + 4);
    const totalH = ((e.tokens_in + e.tokens_out) / maxTokens) * chartH;
    const inH = (e.tokens_in / maxTokens) * chartH;
    const outH = (e.tokens_out / maxTokens) * chartH;

    // Input tokens (bottom)
    ctx.fillStyle = "#58a6ff";
    ctx.fillRect(x, h - padding.bottom - inH, barWidth / 2, inH);

    // Output tokens (stacked)
    ctx.fillStyle = "#3fb950";
    ctx.fillRect(x + barWidth / 2, h - padding.bottom - outH, barWidth / 2, outH);

    // Label
    ctx.fillStyle = "#6e7681";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(`E${i + 1}`, x + barWidth / 2, h - padding.bottom + 14);
  });

  // Legend
  ctx.fillStyle = "#58a6ff";
  ctx.fillRect(w - 140, 8, 10, 10);
  ctx.fillStyle = "#8b949e";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("Input", w - 126, 17);

  ctx.fillStyle = "#3fb950";
  ctx.fillRect(w - 70, 8, 10, 10);
  ctx.fillStyle = "#8b949e";
  ctx.fillText("Output", w - 56, 17);
}

function drawCumulativeChart(ctx, canvas, events) {
  const w = canvas.width;
  const h = canvas.height;
  const padding = { top: 20, right: 20, bottom: 40, left: 50 };

  ctx.clearRect(0, 0, w, h);

  const tokenEvents = events.filter(e => e.tokens_in > 0 || e.tokens_out > 0);
  if (tokenEvents.length === 0) {
    ctx.fillStyle = "#6e7681";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No token usage data", w / 2, h / 2);
    return;
  }

  // Calculate cumulative values
  let cumIn = 0, cumOut = 0;
  const points = tokenEvents.map(e => {
    cumIn += e.tokens_in;
    cumOut += e.tokens_out;
    return { cumIn, cumOut, total: cumIn + cumOut };
  });

  const maxTotal = points[points.length - 1].total;
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;

  // Grid
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = "#6e7681";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(Math.round(maxTotal * (1 - i / 4)).toString(), padding.left - 8, y + 4);
  }

  // Draw line - cumulative input
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = padding.left + (i / (points.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (p.cumIn / maxTotal) * chartH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Draw line - cumulative total
  ctx.strokeStyle = "#3fb950";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = padding.left + (i / (points.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (p.total / maxTotal) * chartH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots
  points.forEach((p, i) => {
    const x = padding.left + (i / (points.length - 1 || 1)) * chartW;

    ctx.fillStyle = "#58a6ff";
    ctx.beginPath();
    ctx.arc(x, padding.top + chartH - (p.cumIn / maxTotal) * chartH, 3, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#3fb950";
    ctx.beginPath();
    ctx.arc(x, padding.top + chartH - (p.total / maxTotal) * chartH, 3, 0, Math.PI * 2);
    ctx.fill();
  });

  // Legend
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(w - 160, 13);
  ctx.lineTo(w - 145, 13);
  ctx.stroke();
  ctx.fillStyle = "#8b949e";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("Cum. Input", w - 140, 17);

  ctx.strokeStyle = "#3fb950";
  ctx.beginPath();
  ctx.moveTo(w - 70, 13);
  ctx.lineTo(w - 55, 13);
  ctx.stroke();
  ctx.fillStyle = "#8b949e";
  ctx.fillText("Total", w - 50, 17);
}

function renderTokenSummary(events) {
  const el = document.getElementById("tokenSummary");

  // Group by model
  const byModel = {};
  events.forEach(e => {
    if (!e.model) return;
    if (!byModel[e.model]) byModel[e.model] = { input: 0, output: 0, calls: 0 };
    byModel[e.model].input += e.tokens_in || 0;
    byModel[e.model].output += e.tokens_out || 0;
    byModel[e.model].calls++;
  });

  const totalIn = events.reduce((sum, e) => sum + (e.tokens_in || 0), 0);
  const totalOut = events.reduce((sum, e) => sum + (e.tokens_out || 0), 0);

  let rows = Object.entries(byModel).map(([model, data]) => `
    <tr>
      <td>${escHtml(model)}</td>
      <td>${data.calls}</td>
      <td>${data.input.toLocaleString()}</td>
      <td>${data.output.toLocaleString()}</td>
      <td>${(data.input + data.output).toLocaleString()}</td>
    </tr>
  `).join("");

  el.innerHTML = `
    <h3>Token Usage by Model</h3>
    <table class="token-table">
      <thead>
        <tr>
          <th>Model</th>
          <th>Calls</th>
          <th>Input Tokens</th>
          <th>Output Tokens</th>
          <th>Total</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
        <tr style="font-weight: 600; border-top: 2px solid var(--border);">
          <td>Total</td>
          <td>${events.filter(e => e.model).length}</td>
          <td>${totalIn.toLocaleString()}</td>
          <td>${totalOut.toLocaleString()}</td>
          <td>${(totalIn + totalOut).toLocaleString()}</td>
        </tr>
      </tbody>
    </table>
  `;
}

// ── Explainability ──────────────────────────────────────────────────

async function loadExplanation(sessionId) {
  const el = document.getElementById("explainContent");
  el.innerHTML = '<div class="loading">Generating explanation...</div>';

  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/explain`);
    const data = await res.json();
    el.innerHTML = renderMarkdown(data.explanation);
  } catch (err) {
    el.innerHTML = `<div class="loading">Error: ${escHtml(err.message)}</div>`;
  }
}

// ── Export ───────────────────────────────────────────────────────────

function toggleExportMenu() {
  const dropdown = document.getElementById("exportDropdown");
  dropdown.classList.toggle("open");
}

// Close export menu when clicking outside
document.addEventListener("click", (e) => {
  const dropdown = document.getElementById("exportDropdown");
  if (dropdown && !dropdown.contains(e.target)) {
    dropdown.classList.remove("open");
  }
});

async function exportSession(format) {
  if (!currentSession) return;

  const dropdown = document.getElementById("exportDropdown");
  dropdown.classList.remove("open");

  try {
    const res = await fetch(
      `${API_BASE}/sessions/${currentSession.session_id}/export?format=${format}`
    );

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Export failed");
    }

    // Get filename from Content-Disposition header or generate one
    const disposition = res.headers.get("Content-Disposition");
    let filename = `agentlens-export.${format}`;
    if (disposition) {
      const match = disposition.match(/filename="?([^";\n]+)"?/);
      if (match) filename = match[1];
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showToast(`✅ Exported as ${format.toUpperCase()}`);
  } catch (err) {
    showToast(`❌ Export failed: ${err.message}`);
  }
}

function showToast(message) {
  // Remove any existing toast
  const existing = document.querySelector(".export-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.className = "export-toast";
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    if (toast.parentNode) toast.remove();
  }, 2500);
}

// ── Tab Switching ───────────────────────────────────────────────────

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));

  document.querySelector(`.tab[data-tab="${tabName}"]`).classList.add("active");
  document.getElementById(`${tabName}Tab`).classList.add("active");

  // Lazy-load costs when tab is first opened
  if (tabName === "costs" && currentSession && !costData) {
    loadCosts(currentSession.session_id);
  }
}

// ── Compare Selection ───────────────────────────────────────────────

function toggleCompare(sessionId, agentName) {
  const idx = compareSelection.findIndex(c => c.id === sessionId);
  if (idx >= 0) {
    compareSelection.splice(idx, 1);
  } else if (compareSelection.length < 2) {
    compareSelection.push({ id: sessionId, name: agentName });
  } else {
    // Replace oldest selection
    compareSelection.shift();
    compareSelection.push({ id: sessionId, name: agentName });
  }
  updateCompareUI();
}

function updateCompareUI() {
  const btn = document.getElementById("compareBtn");
  const clearBtn = document.getElementById("compareClearBtn");
  const countEl = document.getElementById("compareCount");
  const hint = document.getElementById("compareHint");

  const count = compareSelection.length;
  countEl.textContent = count;

  btn.style.display = count > 0 ? "inline-flex" : "none";
  clearBtn.style.display = count > 0 ? "inline-flex" : "none";
  btn.disabled = count < 2;
  hint.style.display = count === 0 ? "block" : "none";

  // Update card selection state
  document.querySelectorAll(".session-card").forEach(card => {
    const sid = card.dataset.sessionId;
    const cb = card.querySelector(".compare-checkbox");
    const isSelected = compareSelection.some(c => c.id === sid);
    card.classList.toggle("selected", isSelected);
    if (cb) cb.checked = isSelected;
  });
}

function clearCompareSelection() {
  compareSelection = [];
  updateCompareUI();
}

async function openCompareView() {
  if (compareSelection.length !== 2) return;

  document.getElementById("sessionListView").classList.remove("active");
  document.getElementById("sessionDetailView").classList.remove("active");
  document.getElementById("sessionCompareView").classList.add("active");

  document.getElementById("compareLabelA").textContent =
    `${compareSelection[0].name} (${compareSelection[0].id.slice(0, 8)}…)`;
  document.getElementById("compareLabelB").textContent =
    `${compareSelection[1].name} (${compareSelection[1].id.slice(0, 8)}…)`;

  try {
    const res = await fetch(`${API_BASE}/sessions/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_a: compareSelection[0].id,
        session_b: compareSelection[1].id,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Comparison failed");
    }

    const data = await res.json();
    renderCompareOverview(data);
    renderCompareTokenChart(data);
    renderCompareDistChart(data);
    renderCompareModelTable(data);
    renderCompareEventChart(data);
    renderCompareDurationChart(data);
    renderCompareToolTable(data);
  } catch (err) {
    document.getElementById("compareOverview").innerHTML =
      `<div class="loading">Error: ${escHtml(err.message)}</div>`;
  }
}

function renderCompareOverview(data) {
  const a = data.session_a;
  const b = data.session_b;
  const d = data.deltas;

  const metrics = [
    { label: "Total Tokens", a: a.total_tokens, b: b.total_tokens, delta: d.total_tokens, fmt: v => v.toLocaleString() },
    { label: "Input Tokens", a: a.tokens_in, b: b.tokens_in, delta: d.tokens_in, fmt: v => v.toLocaleString() },
    { label: "Output Tokens", a: a.tokens_out, b: b.tokens_out, delta: d.tokens_out, fmt: v => v.toLocaleString() },
    { label: "Events", a: a.event_count, b: b.event_count, delta: d.event_count, fmt: v => v.toString() },
    { label: "Errors", a: a.error_count, b: b.error_count, delta: d.error_count, fmt: v => v.toString() },
    { label: "Processing Time", a: a.total_processing_ms, b: b.total_processing_ms, delta: d.total_processing_ms, fmt: v => `${v.toFixed(0)}ms` },
    { label: "Avg Event Time", a: a.avg_event_duration_ms, b: b.avg_event_duration_ms, delta: d.avg_event_duration_ms, fmt: v => `${v.toFixed(1)}ms` },
  ];

  document.getElementById("compareOverview").innerHTML = metrics.map(m => {
    const deltaClass = m.delta.percent > 0 ? "higher" : m.delta.percent < 0 ? "negative" : "neutral";
    const deltaSign = m.delta.percent > 0 ? "+" : "";
    return `
      <div class="compare-card">
        <div class="compare-card-title">${m.label}</div>
        <div class="compare-values">
          <div class="compare-val val-a">
            <div class="val-num">${m.fmt(m.a)}</div>
            <div class="val-label">A</div>
          </div>
          <div class="compare-delta ${deltaClass}">
            ${deltaSign}${m.delta.percent.toFixed(1)}%
          </div>
          <div class="compare-val val-b">
            <div class="val-num">${m.fmt(m.b)}</div>
            <div class="val-label">B</div>
          </div>
        </div>
      </div>`;
  }).join("");
}

function renderCompareTokenChart(data) {
  const canvas = document.getElementById("compareTokenChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const padding = { top: 30, right: 20, bottom: 50, left: 60 };
  ctx.clearRect(0, 0, w, h);

  const a = data.session_a;
  const b = data.session_b;
  const groups = [
    { label: "Input", a: a.tokens_in, b: b.tokens_in },
    { label: "Output", a: a.tokens_out, b: b.tokens_out },
    { label: "Total", a: a.total_tokens, b: b.total_tokens },
  ];

  const maxVal = Math.max(...groups.map(g => Math.max(g.a, g.b)));
  if (maxVal === 0) {
    ctx.fillStyle = "#6e7681"; ctx.font = "14px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("No token data", w / 2, h / 2);
    return;
  }

  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const groupW = chartW / groups.length;
  const barW = groupW * 0.3;
  const gap = groupW * 0.1;

  // Grid
  ctx.strokeStyle = "#21262d"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(w - padding.right, y); ctx.stroke();
    ctx.fillStyle = "#6e7681"; ctx.font = "11px sans-serif"; ctx.textAlign = "right";
    ctx.fillText(Math.round(maxVal * (1 - i / 4)).toLocaleString(), padding.left - 8, y + 4);
  }

  groups.forEach((g, i) => {
    const groupX = padding.left + i * groupW + gap;
    const hA = (g.a / maxVal) * chartH;
    const hB = (g.b / maxVal) * chartH;

    // Bar A
    ctx.fillStyle = "#58a6ff";
    ctx.fillRect(groupX, padding.top + chartH - hA, barW, hA);

    // Bar B
    ctx.fillStyle = "#f0883e";
    ctx.fillRect(groupX + barW + 4, padding.top + chartH - hB, barW, hB);

    // Label
    ctx.fillStyle = "#8b949e"; ctx.font = "12px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(g.label, groupX + barW + 2, h - padding.bottom + 18);
  });

  // Legend
  ctx.fillStyle = "#58a6ff"; ctx.fillRect(w - 120, 8, 10, 10);
  ctx.fillStyle = "#8b949e"; ctx.font = "11px sans-serif"; ctx.textAlign = "left";
  ctx.fillText("Session A", w - 106, 17);
  ctx.fillStyle = "#f0883e"; ctx.fillRect(w - 120, 24, 10, 10);
  ctx.fillStyle = "#8b949e"; ctx.fillText("Session B", w - 106, 33);
}

function renderCompareDistChart(data) {
  const canvas = document.getElementById("compareDistChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const a = data.session_a;
  const b = data.session_b;

  // Stacked horizontal bar chart showing input/output ratio
  const bars = [
    { label: "Session A", input: a.tokens_in, output: a.tokens_out, color: "#58a6ff" },
    { label: "Session B", input: b.tokens_in, output: b.tokens_out, color: "#f0883e" },
  ];

  const padding = { top: 30, right: 20, bottom: 30, left: 90 };
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const barH = Math.min(50, chartH / bars.length - 20);
  const maxTotal = Math.max(...bars.map(b => b.input + b.output));

  if (maxTotal === 0) {
    ctx.fillStyle = "#6e7681"; ctx.font = "14px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("No token data", w / 2, h / 2);
    return;
  }

  bars.forEach((bar, i) => {
    const y = padding.top + i * (chartH / bars.length) + (chartH / bars.length - barH) / 2;
    const total = bar.input + bar.output;
    const wIn = (bar.input / maxTotal) * chartW;
    const wOut = (bar.output / maxTotal) * chartW;

    // Input portion (darker)
    ctx.fillStyle = bar.color;
    ctx.globalAlpha = 0.9;
    ctx.fillRect(padding.left, y, wIn, barH);

    // Output portion (lighter)
    ctx.globalAlpha = 0.5;
    ctx.fillRect(padding.left + wIn, y, wOut, barH);
    ctx.globalAlpha = 1;

    // Label
    ctx.fillStyle = "#e6edf3"; ctx.font = "12px sans-serif"; ctx.textAlign = "right";
    ctx.fillText(bar.label, padding.left - 10, y + barH / 2 + 4);

    // Value
    ctx.fillStyle = "#8b949e"; ctx.font = "11px sans-serif"; ctx.textAlign = "left";
    ctx.fillText(`${total.toLocaleString()} (${bar.input.toLocaleString()} in / ${bar.output.toLocaleString()} out)`, padding.left + wIn + wOut + 8, y + barH / 2 + 4);
  });

  // Legend
  ctx.globalAlpha = 0.9;
  ctx.fillStyle = "#8b949e"; ctx.fillRect(w - 140, 8, 10, 10);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#8b949e"; ctx.font = "11px sans-serif"; ctx.textAlign = "left";
  ctx.fillText("Input", w - 126, 17);
  ctx.globalAlpha = 0.5;
  ctx.fillStyle = "#8b949e"; ctx.fillRect(w - 70, 8, 10, 10);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#8b949e"; ctx.fillText("Output", w - 56, 17);
}

function renderCompareModelTable(data) {
  const el = document.getElementById("compareModelTable");
  const a = data.session_a;
  const b = data.session_b;
  const models = data.shared.models;

  if (models.length === 0) {
    el.innerHTML = "<h3>Model Usage</h3><p style='color:var(--text-muted);padding:12px'>No model data available.</p>";
    return;
  }

  const rows = models.map(m => {
    const ma = a.models[m] || { calls: 0, tokens_in: 0, tokens_out: 0 };
    const mb = b.models[m] || { calls: 0, tokens_in: 0, tokens_out: 0 };
    const totalA = ma.tokens_in + ma.tokens_out;
    const totalB = mb.tokens_in + mb.tokens_out;
    const maxT = Math.max(totalA, totalB) || 1;
    return `<tr>
      <td>${escHtml(m)}</td>
      <td class="col-a">${ma.calls}</td>
      <td class="col-b">${mb.calls}</td>
      <td class="col-a">${totalA.toLocaleString()}</td>
      <td class="col-b">${totalB.toLocaleString()}</td>
      <td>
        <div class="compare-bar-container">
          <div class="compare-bar bar-a" style="width:${(totalA/maxT)*100}%"></div>
          <div class="compare-bar bar-b" style="width:${(totalB/maxT)*100}%"></div>
        </div>
      </td>
    </tr>`;
  }).join("");

  el.innerHTML = `<h3>Model Usage Comparison</h3>
    <table class="compare-table">
      <thead><tr><th>Model</th><th class="col-a">Calls A</th><th class="col-b">Calls B</th><th class="col-a">Tokens A</th><th class="col-b">Tokens B</th><th>Relative</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderCompareEventChart(data) {
  const canvas = document.getElementById("compareEventChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const padding = { top: 30, right: 20, bottom: 60, left: 50 };
  ctx.clearRect(0, 0, w, h);

  const types = data.shared.event_types.filter(t => t !== "session_start" && t !== "session_end");
  if (types.length === 0) {
    ctx.fillStyle = "#6e7681"; ctx.font = "14px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("No events to compare", w / 2, h / 2);
    return;
  }

  const aTypes = data.session_a.event_types;
  const bTypes = data.session_b.event_types;
  const maxVal = Math.max(...types.map(t => Math.max(aTypes[t] || 0, bTypes[t] || 0)));
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const groupW = chartW / types.length;
  const barW = groupW * 0.3;

  // Grid
  ctx.strokeStyle = "#21262d"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(w - padding.right, y); ctx.stroke();
    ctx.fillStyle = "#6e7681"; ctx.font = "11px sans-serif"; ctx.textAlign = "right";
    ctx.fillText(Math.round(maxVal * (1 - i / 4)).toString(), padding.left - 8, y + 4);
  }

  types.forEach((t, i) => {
    const x = padding.left + i * groupW + groupW * 0.15;
    const hA = ((aTypes[t] || 0) / maxVal) * chartH;
    const hB = ((bTypes[t] || 0) / maxVal) * chartH;

    ctx.fillStyle = "#58a6ff";
    ctx.fillRect(x, padding.top + chartH - hA, barW, hA);
    ctx.fillStyle = "#f0883e";
    ctx.fillRect(x + barW + 2, padding.top + chartH - hB, barW, hB);

    // Label (rotated)
    ctx.save();
    ctx.fillStyle = "#8b949e"; ctx.font = "10px sans-serif"; ctx.textAlign = "right";
    ctx.translate(x + barW, h - padding.bottom + 8);
    ctx.rotate(-Math.PI / 4);
    ctx.fillText(t.replace(/_/g, " "), 0, 0);
    ctx.restore();
  });

  // Legend
  ctx.fillStyle = "#58a6ff"; ctx.fillRect(w - 120, 8, 10, 10);
  ctx.fillStyle = "#8b949e"; ctx.font = "11px sans-serif"; ctx.textAlign = "left";
  ctx.fillText("Session A", w - 106, 17);
  ctx.fillStyle = "#f0883e"; ctx.fillRect(w - 120, 24, 10, 10);
  ctx.fillStyle = "#8b949e"; ctx.fillText("Session B", w - 106, 33);
}

function renderCompareDurationChart(data) {
  const canvas = document.getElementById("compareDurationChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const a = data.session_a;
  const b = data.session_b;

  // Simple stats comparison
  const padding = { top: 30, right: 20, bottom: 40, left: 60 };
  const items = [
    { label: "Total Processing", a: a.total_processing_ms, b: b.total_processing_ms },
    { label: "Avg per Event", a: a.avg_event_duration_ms, b: b.avg_event_duration_ms },
  ];

  if (a.session_duration_ms != null) items.unshift({ label: "Session Duration", a: a.session_duration_ms, b: b.session_duration_ms || 0 });

  const maxVal = Math.max(...items.map(i => Math.max(i.a, i.b)));
  if (maxVal === 0) {
    ctx.fillStyle = "#6e7681"; ctx.font = "14px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("No duration data", w / 2, h / 2);
    return;
  }

  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const groupW = chartW / items.length;
  const barW = groupW * 0.3;

  // Grid
  ctx.strokeStyle = "#21262d"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(w - padding.right, y); ctx.stroke();
    ctx.fillStyle = "#6e7681"; ctx.font = "11px sans-serif"; ctx.textAlign = "right";
    ctx.fillText(`${Math.round(maxVal * (1 - i / 4))}ms`, padding.left - 8, y + 4);
  }

  items.forEach((item, i) => {
    const x = padding.left + i * groupW + groupW * 0.15;
    const hA = (item.a / maxVal) * chartH;
    const hB = (item.b / maxVal) * chartH;

    ctx.fillStyle = "#58a6ff";
    ctx.fillRect(x, padding.top + chartH - hA, barW, hA);
    ctx.fillStyle = "#f0883e";
    ctx.fillRect(x + barW + 4, padding.top + chartH - hB, barW, hB);

    ctx.fillStyle = "#8b949e"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(item.label, x + barW + 2, h - padding.bottom + 16);
  });
}

function renderCompareToolTable(data) {
  const el = document.getElementById("compareToolTable");
  const a = data.session_a;
  const b = data.session_b;
  const tools = data.shared.tools;

  if (tools.length === 0) {
    el.innerHTML = "<h3>Tool Usage Comparison</h3><p style='color:var(--text-muted);padding:12px'>No tools used in either session.</p>";
    return;
  }

  const rows = tools.map(t => {
    const ta = a.tools[t] || { calls: 0, total_duration: 0 };
    const tb = b.tools[t] || { calls: 0, total_duration: 0 };
    const maxCalls = Math.max(ta.calls, tb.calls) || 1;
    return `<tr>
      <td><strong>${escHtml(t)}</strong></td>
      <td class="col-a">${ta.calls}</td>
      <td class="col-b">${tb.calls}</td>
      <td class="col-a">${ta.total_duration.toFixed(0)}ms</td>
      <td class="col-b">${tb.total_duration.toFixed(0)}ms</td>
      <td>
        <div class="compare-bar-container">
          <div class="compare-bar bar-a" style="width:${(ta.calls/maxCalls)*100}%"></div>
          <div class="compare-bar bar-b" style="width:${(tb.calls/maxCalls)*100}%"></div>
        </div>
      </td>
    </tr>`;
  }).join("");

  el.innerHTML = `<h3>Tool Usage Comparison</h3>
    <table class="compare-table">
      <thead><tr><th>Tool</th><th class="col-a">Calls A</th><th class="col-b">Calls B</th><th class="col-a">Duration A</th><th class="col-b">Duration B</th><th>Relative</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function switchCompareTab(tabName) {
  // Reset all compare tabs
  document.querySelectorAll("#sessionCompareView .tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll("#sessionCompareView .tab-content").forEach(t => t.classList.remove("active"));

  document.querySelector(`#sessionCompareView .tab[data-tab="${tabName}"]`).classList.add("active");
  document.getElementById(`${tabName}Tab`).classList.add("active");
}

// ── Analytics Overview ───────────────────────────────────────────────

function toggleAnalytics() {
  const panel = document.getElementById("analyticsPanel");
  const btn = document.getElementById("analyticsToggleBtn");

  analyticsVisible = !analyticsVisible;

  if (analyticsVisible) {
    panel.style.display = "block";
    btn.classList.add("active");
    loadAnalytics();
  } else {
    panel.style.display = "none";
    btn.classList.remove("active");
  }
}

async function loadAnalytics() {
  const loadingEl = document.getElementById("analyticsLoading");
  const contentEl = document.getElementById("analyticsContent");

  loadingEl.style.display = "block";
  contentEl.style.display = "none";

  try {
    const res = await fetch(`${API_BASE}/analytics`);
    analyticsData = await res.json();

    loadingEl.style.display = "none";
    contentEl.style.display = "block";

    renderAnalyticsCards(analyticsData);
    renderSessionsTimeChart(analyticsData.sessions_over_time);
    renderHourlyActivityChart(analyticsData.hourly_activity);
    renderModelUsageTable(analyticsData.model_usage);
    renderTopAgentsTable(analyticsData.top_agents);
  } catch (err) {
    loadingEl.textContent = `Error loading analytics: ${escHtml(err.message)}`;
  }
}

function renderAnalyticsCards(data) {
  const o = data.overview;
  const d = data.duration;

  const cards = [
    { value: o.total_sessions, label: "Total Sessions", color: "accent" },
    { value: o.total_events.toLocaleString(), label: "Total Events", color: "purple" },
    { value: formatTokenCount(o.total_tokens), label: "Total Tokens", color: "green" },
    { value: formatTokenCount(o.avg_tokens_per_session), label: "Avg Tokens/Session", color: "yellow" },
    { value: o.active_sessions, label: "Active", color: "green" },
    { value: o.completed_sessions, label: "Completed", color: "purple" },
    { value: o.error_sessions, label: "Errors", color: "red" },
    { value: `${o.error_rate}%`, label: "Error Rate", color: o.error_rate > 10 ? "red" : o.error_rate > 5 ? "yellow" : "green" },
    { value: formatDurationShort(d.avg_ms), label: "Avg Duration", color: "orange" },
  ];

  document.getElementById("analyticsCards").innerHTML = cards
    .map(
      (c) => `
    <div class="analytics-stat">
      <div class="stat-value ${c.color}">${c.value}</div>
      <div class="stat-label">${c.label}</div>
    </div>`
    )
    .join("");
}

function renderSessionsTimeChart(sessionsOverTime) {
  const canvas = document.getElementById("sessionsTimeChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const padding = { top: 16, right: 16, bottom: 36, left: 40 };

  ctx.clearRect(0, 0, w, h);

  if (!sessionsOverTime || sessionsOverTime.length === 0) {
    ctx.fillStyle = "#6e7681";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No session data yet", w / 2, h / 2);
    return;
  }

  const data = sessionsOverTime;
  const maxSessions = Math.max(...data.map((d) => d.session_count), 1);
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;

  // Grid lines
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const y = padding.top + (chartH / 3) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = "#6e7681";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(Math.round(maxSessions * (1 - i / 3)).toString(), padding.left - 6, y + 3);
  }

  // Area fill
  ctx.beginPath();
  data.forEach((d, i) => {
    const x = padding.left + (i / (data.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (d.session_count / maxSessions) * chartH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.lineTo(padding.left + chartW, padding.top + chartH);
  ctx.lineTo(padding.left, padding.top + chartH);
  ctx.closePath();
  ctx.fillStyle = "rgba(188, 140, 255, 0.1)";
  ctx.fill();

  // Line
  ctx.beginPath();
  data.forEach((d, i) => {
    const x = padding.left + (i / (data.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (d.session_count / maxSessions) * chartH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#bc8cff";
  ctx.lineWidth = 2;
  ctx.stroke();

  // Dots
  data.forEach((d, i) => {
    const x = padding.left + (i / (data.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (d.session_count / maxSessions) * chartH;
    ctx.fillStyle = "#bc8cff";
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });

  // X-axis labels (show first, last, and a few in between)
  ctx.fillStyle = "#6e7681";
  ctx.font = "9px sans-serif";
  ctx.textAlign = "center";
  const labelInterval = Math.max(1, Math.floor(data.length / 5));
  data.forEach((d, i) => {
    if (i === 0 || i === data.length - 1 || i % labelInterval === 0) {
      const x = padding.left + (i / (data.length - 1 || 1)) * chartW;
      const label = d.day.slice(5); // MM-DD
      ctx.fillText(label, x, h - padding.bottom + 14);
    }
  });
}

function renderHourlyActivityChart(hourlyActivity) {
  const canvas = document.getElementById("hourlyActivityChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const padding = { top: 16, right: 16, bottom: 36, left: 40 };

  ctx.clearRect(0, 0, w, h);

  if (!hourlyActivity || hourlyActivity.length === 0) {
    ctx.fillStyle = "#6e7681";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No activity data yet", w / 2, h / 2);
    return;
  }

  // Fill in missing hours with 0
  const hourMap = {};
  hourlyActivity.forEach((h) => (hourMap[h.hour] = h.event_count));
  const fullData = Array.from({ length: 24 }, (_, i) => ({
    hour: i,
    count: hourMap[i] || 0,
  }));

  const maxCount = Math.max(...fullData.map((d) => d.count), 1);
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;
  const barW = chartW / 24 - 2;

  // Grid lines
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const y = padding.top + (chartH / 3) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = "#6e7681";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(Math.round(maxCount * (1 - i / 3)).toString(), padding.left - 6, y + 3);
  }

  // Bars with gradient intensity
  fullData.forEach((d, i) => {
    const x = padding.left + (i / 24) * chartW + 1;
    const barH = (d.count / maxCount) * chartH;
    const intensity = d.count / maxCount;

    // Color intensity: dim for low, bright for high
    const r = Math.round(88 + (167 * intensity));
    const g = Math.round(166 - (30 * intensity));
    const b = 255;
    ctx.fillStyle = `rgba(${Math.min(r, 255)}, ${Math.min(g, 255)}, ${b}, ${0.3 + intensity * 0.7})`;
    ctx.fillRect(x, padding.top + chartH - barH, barW, barH);
  });

  // X-axis labels
  ctx.fillStyle = "#6e7681";
  ctx.font = "9px sans-serif";
  ctx.textAlign = "center";
  for (let i = 0; i < 24; i += 3) {
    const x = padding.left + (i / 24) * chartW + barW / 2;
    ctx.fillText(`${i}h`, x, h - padding.bottom + 14);
  }
}

function renderModelUsageTable(modelUsage) {
  const el = document.getElementById("modelUsageTable");

  if (!modelUsage || modelUsage.length === 0) {
    el.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">No model data yet.</p>';
    return;
  }

  const maxTokens = Math.max(...modelUsage.map((m) => m.total_tokens), 1);

  el.innerHTML = `
    <table class="analytics-table">
      <thead>
        <tr>
          <th>Model</th>
          <th>Calls</th>
          <th>Tokens</th>
          <th>Avg Time</th>
          <th style="width:80px"></th>
        </tr>
      </thead>
      <tbody>
        ${modelUsage
          .map(
            (m) => `
          <tr>
            <td class="model-name">${escHtml(m.model)}</td>
            <td>${m.call_count.toLocaleString()}</td>
            <td>${formatTokenCount(m.total_tokens)}</td>
            <td>${m.avg_duration_ms.toFixed(0)}ms</td>
            <td>
              <div class="analytics-bar-bg">
                <div class="analytics-bar-fill accent" style="width:${(m.total_tokens / maxTokens) * 100}%"></div>
              </div>
            </td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function renderTopAgentsTable(topAgents) {
  const el = document.getElementById("topAgentsTable");

  if (!topAgents || topAgents.length === 0) {
    el.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">No agent data yet.</p>';
    return;
  }

  const maxTokens = Math.max(...topAgents.map((a) => a.total_tokens), 1);

  el.innerHTML = `
    <table class="analytics-table">
      <thead>
        <tr>
          <th>Agent</th>
          <th>Sessions</th>
          <th>Tokens</th>
          <th>Avg/Session</th>
          <th style="width:80px"></th>
        </tr>
      </thead>
      <tbody>
        ${topAgents
          .map(
            (a) => `
          <tr>
            <td class="agent-name">${escHtml(a.agent_name)}</td>
            <td>${a.session_count}</td>
            <td>${formatTokenCount(a.total_tokens)}</td>
            <td>${formatTokenCount(a.avg_tokens)}</td>
            <td>
              <div class="analytics-bar-bg">
                <div class="analytics-bar-fill purple" style="width:${(a.total_tokens / maxTokens) * 100}%"></div>
              </div>
            </td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function formatTokenCount(count) {
  if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`;
  if (count >= 1000) return `${(count / 1000).toFixed(1)}K`;
  return count.toString();
}

function formatDurationShort(ms) {
  if (!ms || ms === 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}

// ── Cost Estimation ─────────────────────────────────────────────────

let costData = null;
let pricingData = null;

async function loadCosts(sessionId) {
  const loadingEl = document.getElementById("costsLoading");
  const contentEl = document.getElementById("costsContent");

  loadingEl.style.display = "block";
  contentEl.style.display = "none";

  try {
    const res = await fetch(`${API_BASE}/pricing/costs/${sessionId}`);
    if (!res.ok) throw new Error("Failed to load costs");
    costData = await res.json();

    // Also load pricing config
    const pricingRes = await fetch(`${API_BASE}/pricing`);
    if (pricingRes.ok) pricingData = await pricingRes.json();

    loadingEl.style.display = "none";
    contentEl.style.display = "block";

    renderCostOverview(costData);
    renderCostBarChart(costData);
    renderCostCumulativeChart(costData);
    renderCostModelTable(costData);
    renderCostEventList(costData);
    renderCostUnmatched(costData);
    renderPricingEditor(pricingData);
  } catch (err) {
    loadingEl.textContent = `Error: ${escHtml(err.message)}`;
  }
}

function formatCost(value) {
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(6)}`;
  if (value < 1) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function renderCostOverview(data) {
  const el = document.getElementById("costOverview");
  const costPerEvent = data.event_costs.length > 0 ? data.total_cost / data.event_costs.length : 0;
  const pctInput = data.total_cost > 0 ? (data.total_input_cost / data.total_cost * 100) : 0;
  const pctOutput = data.total_cost > 0 ? (data.total_output_cost / data.total_cost * 100) : 0;

  el.innerHTML = `
    <div class="info-card">
      <div class="label">Total Cost</div>
      <div class="value" style="color:var(--green)">${formatCost(data.total_cost)}</div>
    </div>
    <div class="info-card">
      <div class="label">Input Cost</div>
      <div class="value">${formatCost(data.total_input_cost)} <span style="font-size:0.7rem;color:var(--text-muted)">(${pctInput.toFixed(0)}%)</span></div>
    </div>
    <div class="info-card">
      <div class="label">Output Cost</div>
      <div class="value">${formatCost(data.total_output_cost)} <span style="font-size:0.7rem;color:var(--text-muted)">(${pctOutput.toFixed(0)}%)</span></div>
    </div>
    <div class="info-card">
      <div class="label">Cost/Event</div>
      <div class="value">${formatCost(costPerEvent)}</div>
    </div>
    <div class="info-card">
      <div class="label">Models Priced</div>
      <div class="value">${Object.keys(data.model_costs).length}</div>
    </div>
    <div class="info-card">
      <div class="label">Currency</div>
      <div class="value">${data.currency}</div>
    </div>
  `;
}

function renderCostBarChart(data) {
  const canvas = document.getElementById("costBarChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const padding = { top: 20, right: 20, bottom: 40, left: 60 };

  ctx.clearRect(0, 0, w, h);

  const costEvents = data.event_costs.filter(e => e.total_cost > 0);
  if (costEvents.length === 0) {
    ctx.fillStyle = "#6e7681";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No cost data (models may not have pricing)", w / 2, h / 2);
    return;
  }

  const maxCost = Math.max(...costEvents.map(e => e.total_cost));
  const barWidth = Math.max(8, (w - padding.left - padding.right) / costEvents.length - 4);
  const chartH = h - padding.top - padding.bottom;

  // Grid lines
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = "#6e7681";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    const val = maxCost * (1 - i / 4);
    ctx.fillText(formatCost(val), padding.left - 8, y + 4);
  }

  // Bars
  costEvents.forEach((e, i) => {
    const x = padding.left + i * (barWidth + 4);
    const inH = (e.input_cost / maxCost) * chartH;
    const outH = (e.output_cost / maxCost) * chartH;

    // Input cost (bottom)
    ctx.fillStyle = "#58a6ff";
    ctx.fillRect(x, h - padding.bottom - inH, barWidth / 2, inH);

    // Output cost (stacked)
    ctx.fillStyle = "#3fb950";
    ctx.fillRect(x + barWidth / 2, h - padding.bottom - outH, barWidth / 2, outH);

    // Label
    ctx.fillStyle = "#6e7681";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(`E${i + 1}`, x + barWidth / 2, h - padding.bottom + 14);
  });

  // Legend
  ctx.fillStyle = "#58a6ff";
  ctx.fillRect(w - 140, 8, 10, 10);
  ctx.fillStyle = "#8b949e";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("Input Cost", w - 126, 17);

  ctx.fillStyle = "#3fb950";
  ctx.fillRect(w - 140, 24, 10, 10);
  ctx.fillStyle = "#8b949e";
  ctx.fillText("Output Cost", w - 126, 33);
}

function renderCostCumulativeChart(data) {
  const canvas = document.getElementById("costCumulativeChart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  const padding = { top: 20, right: 20, bottom: 40, left: 60 };

  ctx.clearRect(0, 0, w, h);

  const costEvents = data.event_costs.filter(e => e.total_cost > 0);
  if (costEvents.length === 0) {
    ctx.fillStyle = "#6e7681";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No cost data", w / 2, h / 2);
    return;
  }

  // Calculate cumulative values
  let cumInput = 0, cumOutput = 0;
  const points = costEvents.map(e => {
    cumInput += e.input_cost;
    cumOutput += e.output_cost;
    return { cumInput, cumOutput, total: cumInput + cumOutput };
  });

  const maxTotal = points[points.length - 1].total;
  const chartW = w - padding.left - padding.right;
  const chartH = h - padding.top - padding.bottom;

  // Grid
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = "#6e7681";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(formatCost(maxTotal * (1 - i / 4)), padding.left - 8, y + 4);
  }

  // Area fill for total
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = padding.left + (i / (points.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (p.total / maxTotal) * chartH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.lineTo(padding.left + chartW, padding.top + chartH);
  ctx.lineTo(padding.left, padding.top + chartH);
  ctx.closePath();
  ctx.fillStyle = "rgba(63, 185, 80, 0.1)";
  ctx.fill();

  // Draw line - cumulative input
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = padding.left + (i / (points.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (p.cumInput / maxTotal) * chartH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Draw line - cumulative total
  ctx.strokeStyle = "#3fb950";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = padding.left + (i / (points.length - 1 || 1)) * chartW;
    const y = padding.top + chartH - (p.total / maxTotal) * chartH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots
  points.forEach((p, i) => {
    const x = padding.left + (i / (points.length - 1 || 1)) * chartW;

    ctx.fillStyle = "#58a6ff";
    ctx.beginPath();
    ctx.arc(x, padding.top + chartH - (p.cumInput / maxTotal) * chartH, 3, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#3fb950";
    ctx.beginPath();
    ctx.arc(x, padding.top + chartH - (p.total / maxTotal) * chartH, 3, 0, Math.PI * 2);
    ctx.fill();
  });

  // Legend
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(w - 160, 13);
  ctx.lineTo(w - 145, 13);
  ctx.stroke();
  ctx.fillStyle = "#8b949e";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("Input Cost", w - 140, 17);

  ctx.strokeStyle = "#3fb950";
  ctx.beginPath();
  ctx.moveTo(w - 70, 13);
  ctx.lineTo(w - 55, 13);
  ctx.stroke();
  ctx.fillStyle = "#8b949e";
  ctx.fillText("Total", w - 50, 17);
}

function renderCostModelTable(data) {
  const el = document.getElementById("costModelTable");
  const models = Object.entries(data.model_costs);

  if (models.length === 0) {
    el.innerHTML = '<h3>💲 Cost by Model</h3><p style="color:var(--text-muted);padding:12px">No priced model usage.</p>';
    return;
  }

  // Sort by total cost descending
  models.sort((a, b) => b[1].total_cost - a[1].total_cost);
  const maxCost = models[0][1].total_cost || 1;

  const rows = models.map(([model, mc]) => `
    <tr>
      <td>${escHtml(model)} ${!mc.matched ? '<span style="color:var(--yellow);font-size:0.75rem" title="No exact pricing match">⚠️</span>' : ''}</td>
      <td>${mc.calls}</td>
      <td>${mc.tokens_in.toLocaleString()}</td>
      <td>${mc.tokens_out.toLocaleString()}</td>
      <td style="color:#58a6ff">${formatCost(mc.input_cost)}</td>
      <td style="color:#3fb950">${formatCost(mc.output_cost)}</td>
      <td style="font-weight:600;color:var(--green)">${formatCost(mc.total_cost)}</td>
      <td>
        <div class="analytics-bar-bg">
          <div class="analytics-bar-fill green" style="width:${(mc.total_cost / maxCost) * 100}%"></div>
        </div>
      </td>
    </tr>
  `).join("");

  el.innerHTML = `
    <h3>💲 Cost by Model</h3>
    <table class="token-table">
      <thead>
        <tr>
          <th>Model</th>
          <th>Calls</th>
          <th>Input Tokens</th>
          <th>Output Tokens</th>
          <th>Input Cost</th>
          <th>Output Cost</th>
          <th>Total Cost</th>
          <th style="width:80px"></th>
        </tr>
      </thead>
      <tbody>
        ${rows}
        <tr style="font-weight:600;border-top:2px solid var(--border);">
          <td>Total</td>
          <td>${models.reduce((s, [, m]) => s + m.calls, 0)}</td>
          <td>${models.reduce((s, [, m]) => s + m.tokens_in, 0).toLocaleString()}</td>
          <td>${models.reduce((s, [, m]) => s + m.tokens_out, 0).toLocaleString()}</td>
          <td style="color:#58a6ff">${formatCost(data.total_input_cost)}</td>
          <td style="color:#3fb950">${formatCost(data.total_output_cost)}</td>
          <td style="color:var(--green)">${formatCost(data.total_cost)}</td>
          <td></td>
        </tr>
      </tbody>
    </table>
  `;
}

function renderCostEventList(data) {
  const el = document.getElementById("costEventList");
  const events = data.event_costs.filter(e => e.total_cost > 0 || e.model);

  if (events.length === 0) {
    el.innerHTML = "";
    return;
  }

  // Show top 20 costliest events
  const sorted = [...events].sort((a, b) => b.total_cost - a.total_cost).slice(0, 20);

  const rows = sorted.map((e, i) => `
    <tr>
      <td>${i + 1}</td>
      <td><span class="event-type-badge ${e.event_type}">${formatEventType(e.event_type)}</span></td>
      <td>${escHtml(e.model || '—')}</td>
      <td>${e.tokens_in.toLocaleString()} / ${e.tokens_out.toLocaleString()}</td>
      <td style="font-weight:600;color:var(--green)">${formatCost(e.total_cost)}</td>
      <td style="font-size:0.8rem;color:var(--text-muted)">${new Date(e.timestamp).toLocaleTimeString()}</td>
    </tr>
  `).join("");

  el.innerHTML = `
    <h3>📋 Top ${sorted.length} Costliest Events</h3>
    <table class="token-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Type</th>
          <th>Model</th>
          <th>Tokens (in/out)</th>
          <th>Cost</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderCostUnmatched(data) {
  const el = document.getElementById("costUnmatched");
  if (!data.unmatched_models || data.unmatched_models.length === 0) {
    el.style.display = "none";
    return;
  }

  el.style.display = "block";
  el.innerHTML = `
    <div style="background:rgba(210,153,34,0.1);border:1px solid var(--yellow);border-radius:8px;padding:12px 16px;margin-top:12px">
      <strong style="color:var(--yellow)">⚠️ Unmatched Models</strong>
      <p style="color:var(--text-muted);font-size:0.85rem;margin:4px 0">
        The following models have no pricing configured. Their costs show as $0.
        Add pricing below to include them in cost calculations.
      </p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
        ${data.unmatched_models.map(m => `<code style="background:var(--card);padding:2px 8px;border-radius:4px;font-size:0.85rem">${escHtml(m)}</code>`).join("")}
      </div>
    </div>
  `;
}

function renderPricingEditor(data) {
  const el = document.getElementById("pricingEditor");
  if (!data || !data.pricing) {
    el.innerHTML = '<p style="color:var(--text-muted)">Could not load pricing data.</p>';
    return;
  }

  const models = Object.entries(data.pricing).sort((a, b) => a[0].localeCompare(b[0]));

  el.innerHTML = `
    <table class="token-table" id="pricingTable">
      <thead>
        <tr>
          <th>Model</th>
          <th>Input ($/1M tokens)</th>
          <th>Output ($/1M tokens)</th>
        </tr>
      </thead>
      <tbody>
        ${models.map(([model, p]) => `
          <tr>
            <td style="font-family:monospace;font-size:0.85rem">${escHtml(model)}</td>
            <td><input type="number" step="0.01" min="0" class="pricing-input" data-model="${escHtml(model)}" data-type="input" value="${p.input_cost_per_1m}"></td>
            <td><input type="number" step="0.01" min="0" class="pricing-input" data-model="${escHtml(model)}" data-type="output" value="${p.output_cost_per_1m}"></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

async function savePricing() {
  const inputs = document.querySelectorAll(".pricing-input");
  const pricing = {};

  inputs.forEach(input => {
    const model = input.dataset.model;
    const type = input.dataset.type;
    if (!pricing[model]) pricing[model] = {};
    if (type === "input") pricing[model].input_cost_per_1m = parseFloat(input.value) || 0;
    if (type === "output") pricing[model].output_cost_per_1m = parseFloat(input.value) || 0;
  });

  try {
    const res = await fetch(`${API_BASE}/pricing`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pricing }),
    });

    if (!res.ok) throw new Error("Failed to save pricing");
    const result = await res.json();
    showToast(`✅ Pricing updated (${result.updated} models)`);

    // Refresh costs
    if (currentSession) {
      loadCosts(currentSession.session_id);
    }
  } catch (err) {
    showToast(`❌ Failed to save pricing: ${err.message}`);
  }
}

async function resetPricing() {
  try {
    // Fetch defaults and save them
    const res = await fetch(`${API_BASE}/pricing`);
    if (!res.ok) throw new Error("Failed to load defaults");
    const data = await res.json();

    const pricing = {};
    for (const [model, prices] of Object.entries(data.defaults)) {
      pricing[model] = { input_cost_per_1m: prices.input, output_cost_per_1m: prices.output };
    }

    const saveRes = await fetch(`${API_BASE}/pricing`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pricing }),
    });

    if (!saveRes.ok) throw new Error("Failed to save defaults");
    showToast("✅ Pricing reset to defaults");

    if (currentSession) {
      loadCosts(currentSession.session_id);
    }
  } catch (err) {
    showToast(`❌ Failed to reset pricing: ${err.message}`);
  }
}

// ── Utilities ───────────────────────────────────────────────────────

function escHtml(str) {
  if (!str) return "";
  const el = document.createElement("span");
  el.textContent = String(str);
  return el.innerHTML;
}

function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

function formatDuration(start, end) {
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function formatEventType(type) {
  return type.replace(/_/g, " ");
}

function formatJson(obj) {
  if (!obj) return "";
  try {
    const str = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
    return escHtml(str);
  } catch {
    return escHtml(String(obj));
  }
}

function renderMarkdown(text) {
  if (!text) return "";
  // ── Security: Escape HTML first, then apply markdown formatting ──
  // This prevents XSS via injected HTML in explanation text
  const escaped = escHtml(text);
  return escaped
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/💡/g, '💡')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
    .replace(/<\/ul>\s*<ul>/g, '')
    .replace(/\n\n/g, '<br><br>')
    .replace(/\n/g, '<br>');
}
