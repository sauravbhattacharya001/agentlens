/* ── AgentLens Dashboard — App Logic ──────────────────────────────────── */

const API_BASE = window.location.origin;

let currentSession = null;
let compareSelection = []; // Array of {id, name} for comparison
let analyticsData = null;  // Cached analytics data
let analyticsVisible = false;
let costData = null;       // Cached cost data for current session
let pricingData = null;    // Cached pricing configuration
let errorData = null;      // Cached error analytics data
let postmortemData = null;  // Cached postmortem data for current session

// ── Initialization ──────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await loadBookmarks();
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

    let sessions = data.sessions || [];

    // Filter to bookmarked only if active
    if (showOnlyBookmarks) {
      sessions = sessions.filter(s => bookmarkedIds.has(s.session_id));
      if (sessions.length === 0) {
        listEl.innerHTML = '<div class="loading">No bookmarked sessions. Star a session to bookmark it.</div>';
        return;
      }
    }

    listEl.innerHTML = sessions.map((s) => `
      <div class="session-card ${compareSelection.some(c => c.id === s.session_id) ? 'selected' : ''}" data-session-id="${escHtml(s.session_id)}">
        <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
          <span class="bookmark-star" data-bookmark-id="${escHtml(s.session_id)}"
            onclick="toggleBookmark('${escHtml(s.session_id)}', event)"
            title="${bookmarkedIds.has(s.session_id) ? 'Remove bookmark' : 'Bookmark session'}"
            style="cursor:pointer;font-size:1.2em;user-select:none">${bookmarkedIds.has(s.session_id) ? '⭐' : '☆'}</span>
          <input type="checkbox" class="compare-checkbox"
            ${compareSelection.some(c => c.id === s.session_id) ? 'checked' : ''}
            onclick="event.stopPropagation(); toggleCompare('${escHtml(s.session_id)}', '${escHtml(s.agent_name)}')"
            title="Select for comparison">
          <div class="session-card-left" onclick="loadSessionDetail('${escHtml(s.session_id)}')">
            <div class="session-agent">${escHtml(s.agent_name)}</div>
            <div class="session-meta">
              <span>🆔 ${escHtml(s.session_id.slice(0, 8))}…</span>
              <span>🕐 ${formatTime(s.started_at)}</span>
              ${s.metadata?.version ? `<span>📦 v${escHtml(s.metadata.version)}</span>` : ""}
              ${s.metadata?.environment ? `<span>🌍 ${escHtml(s.metadata.environment)}</span>` : ""}
            </div>
          </div>
        </div>
        <div class="session-card-right" onclick="loadSessionDetail('${escHtml(s.session_id)}')">
          <div class="session-tokens">
            <div class="count">${(s.total_tokens_in + s.total_tokens_out).toLocaleString()}</div>
            <div>tokens</div>
          </div>
          <span class="status-badge ${escHtml(s.status)}">${escHtml(s.status)}</span>
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
  errorData = null; // Reset error data for new session
  postmortemData = null; // Reset postmortem data for new session

  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
    currentSession = await res.json();

    document.getElementById("sessionTitle").textContent = currentSession.agent_name;
    renderSessionInfo(currentSession);
    renderTimeline(currentSession.events);
    renderTokenCharts(currentSession.events);
    loadExplanation(sessionId);

    // Populate model filter for event search
    populateModelFilter(currentSession.events || []);
    // Reset any active filters
    resetEventFilters();
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
      <div class="value"><span class="status-badge ${escHtml(session.status)}">${escHtml(session.status)}</span></div>
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
  // Lazy-load annotations
  if (tabName === "annotations" && currentSession) {
    loadAnnotations();
  }
  // Lazy-load error analytics
  if (tabName === "errors" && !errorData) {
    loadErrors();
  }
  // Lazy-load postmortem
  if (tabName === "postmortem" && currentSession && !postmortemData) {
    loadPostmortem();
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
    loadHeatmap();
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

// ── Activity Heatmap ────────────────────────────────────────────────

async function loadHeatmap() {
  const container = document.getElementById("heatmapContainer");
  const peakEl = document.getElementById("heatmapPeak");
  if (!container) return;

  const metric = document.getElementById("heatmapMetric")?.value || "events";
  const days = document.getElementById("heatmapDays")?.value || "30";

  container.innerHTML = '<div style="color:var(--text-muted);font-size:13px">Loading heatmap...</div>';

  try {
    const res = await fetch(`${API_BASE}/analytics/heatmap?metric=${metric}&days=${days}`);
    const data = await res.json();
    renderHeatmap(container, data);

    if (data.peak && data.peak.value > 0) {
      const hourLabel = `${data.peak.hour}:00–${data.peak.hour}:59`;
      peakEl.textContent = `Peak: ${data.peak.day_name} ${hourLabel} (${data.peak.value.toLocaleString()} ${metric})`;
    } else {
      peakEl.textContent = "No activity in this period.";
    }
  } catch (err) {
    container.innerHTML = `<div style="color:#f85149;font-size:13px">Failed to load heatmap: ${escHtml(err.message)}</div>`;
  }
}

function renderHeatmap(container, data) {
  const matrix = data.matrix; // 7×24
  const maxVal = data.max_value || 1;
  const dayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  // Color scale: transparent → green
  function cellColor(value) {
    if (value === 0) return "rgba(255,255,255,0.04)";
    const intensity = value / maxVal;
    const r = Math.round(13 + (39 - 13) * (1 - intensity));
    const g = Math.round(17 + (166 - 17) * intensity);
    const b = Math.round(23 + (65 - 23) * (1 - intensity));
    const a = 0.3 + intensity * 0.7;
    return `rgba(${r},${g},${b},${a})`;
  }

  let html = '<div style="display:grid;grid-template-columns:40px repeat(24,1fr);gap:2px;font-size:11px">';

  // Header row (hours)
  html += '<div></div>';
  for (let h = 0; h < 24; h++) {
    const label = h % 3 === 0 ? `${h}` : "";
    html += `<div style="text-align:center;color:#8b949e;padding:2px 0">${label}</div>`;
  }

  // Data rows
  for (let d = 0; d < 7; d++) {
    html += `<div style="color:#8b949e;padding:4px 4px 4px 0;text-align:right;line-height:20px">${dayLabels[d]}</div>`;
    for (let h = 0; h < 24; h++) {
      const val = matrix[d][h];
      const bg = cellColor(val);
      const title = `${dayLabels[d]} ${h}:00 — ${val.toLocaleString()} ${data.metric}`;
      html += `<div style="background:${bg};border-radius:3px;height:20px;min-width:14px" title="${title}"></div>`;
    }
  }

  html += '</div>';

  // Legend
  html += '<div style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:11px;color:#8b949e">';
  html += '<span>Less</span>';
  for (let i = 0; i <= 4; i++) {
    const fakeVal = (i / 4) * maxVal;
    html += `<div style="width:14px;height:14px;border-radius:3px;background:${cellColor(fakeVal)}"></div>`;
  }
  html += '<span>More</span></div>';

  container.innerHTML = html;
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

// ── Event Search & Filter ────────────────────────────────────────────

let eventSearchTimeout = null;
let allSessionEvents = []; // Cache of all events for current session
let filteredEventIndices = null; // null means "no filter active"

function onEventSearchInput() {
  const input = document.getElementById("eventSearchInput");
  const clearBtn = document.getElementById("searchClearBtn");
  clearBtn.style.display = input.value ? "flex" : "none";

  // Debounce search
  clearTimeout(eventSearchTimeout);
  eventSearchTimeout = setTimeout(() => applyEventFilters(), 300);
}

function clearEventSearch() {
  document.getElementById("eventSearchInput").value = "";
  document.getElementById("searchClearBtn").style.display = "none";
  applyEventFilters();
}

function toggleAdvancedFilters() {
  const panel = document.getElementById("advancedFilters");
  const btn = document.getElementById("advancedFilterBtn");
  const visible = panel.style.display !== "none";
  panel.style.display = visible ? "none" : "flex";
  btn.classList.toggle("active", !visible);
}

function resetEventFilters() {
  document.getElementById("eventSearchInput").value = "";
  document.getElementById("searchClearBtn").style.display = "none";
  document.getElementById("eventTypeFilter").value = "";
  document.getElementById("eventModelFilter").value = "";
  document.getElementById("filterMinTokens").value = "";
  document.getElementById("filterMinDuration").value = "";
  document.getElementById("filterHasTools").checked = false;
  document.getElementById("filterHasReasoning").checked = false;
  document.getElementById("filterErrorsOnly").checked = false;
  applyEventFilters();
}

function populateModelFilter(events) {
  const select = document.getElementById("eventModelFilter");
  const currentVal = select.value;
  const models = [...new Set(events.filter((e) => e.model).map((e) => e.model))].sort();

  // Preserve existing selection
  select.innerHTML = '<option value="">All Models</option>';
  models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    select.appendChild(opt);
  });
  if (currentVal && models.includes(currentVal)) {
    select.value = currentVal;
  }
}

function hasActiveFilters() {
  return (
    document.getElementById("eventSearchInput").value.trim() !== "" ||
    document.getElementById("eventTypeFilter").value !== "" ||
    document.getElementById("eventModelFilter").value !== "" ||
    (parseInt(document.getElementById("filterMinTokens").value) > 0) ||
    (parseFloat(document.getElementById("filterMinDuration").value) > 0) ||
    document.getElementById("filterHasTools").checked ||
    document.getElementById("filterHasReasoning").checked ||
    document.getElementById("filterErrorsOnly").checked
  );
}

async function applyEventFilters() {
  if (!currentSession) return;

  const active = hasActiveFilters();
  const resultsBar = document.getElementById("searchResultsBar");

  if (!active) {
    // No filters — show all events normally
    filteredEventIndices = null;
    resultsBar.style.display = "none";
    renderTimeline(currentSession.events);
    return;
  }

  // Build query params from filter controls
  const params = new URLSearchParams();

  const q = document.getElementById("eventSearchInput").value.trim();
  if (q) params.set("q", q);

  const typeFilter = document.getElementById("eventTypeFilter").value;
  if (typeFilter) params.set("type", typeFilter);

  const modelFilter = document.getElementById("eventModelFilter").value;
  if (modelFilter) params.set("model", modelFilter);

  const minTokens = parseInt(document.getElementById("filterMinTokens").value);
  if (Number.isFinite(minTokens) && minTokens > 0) params.set("min_tokens", minTokens);

  const minDuration = parseFloat(document.getElementById("filterMinDuration").value);
  if (Number.isFinite(minDuration) && minDuration > 0) params.set("min_duration_ms", minDuration);

  if (document.getElementById("filterHasTools").checked) params.set("has_tools", "true");
  if (document.getElementById("filterHasReasoning").checked) params.set("has_reasoning", "true");
  if (document.getElementById("filterErrorsOnly").checked) params.set("errors", "true");

  params.set("limit", "500");

  try {
    const res = await fetch(
      `${API_BASE}/sessions/${currentSession.session_id}/events/search?${params}`
    );
    if (!res.ok) throw new Error("Search failed");

    const data = await res.json();

    // Build a set of matched event IDs for highlighting
    const matchedIds = new Set(data.events.map((e) => e.event_id));
    filteredEventIndices = matchedIds;

    // Show results bar
    resultsBar.style.display = "flex";
    document.getElementById("searchResultsText").textContent =
      `${data.matched} of ${data.total_events} events match`;

    const statsTokens = data.summary.total_tokens.toLocaleString();
    const statsDuration = data.summary.total_duration_ms > 0
      ? `${data.summary.total_duration_ms.toFixed(0)}ms`
      : "—";
    document.getElementById("searchResultsStats").textContent =
      `${statsTokens} tokens · ${statsDuration} processing`;

    // Re-render timeline with highlighting
    renderFilteredTimeline(currentSession.events, matchedIds, q);
  } catch (err) {
    console.error("Event search error:", err);
    resultsBar.style.display = "flex";
    document.getElementById("searchResultsText").textContent = "Search error";
    document.getElementById("searchResultsStats").textContent = "";
  }
}

function renderFilteredTimeline(events, matchedIds, searchQuery) {
  const el = document.getElementById("timeline");

  if (!events || events.length === 0) {
    el.innerHTML = '<div class="loading">No events in this session.</div>';
    return;
  }

  el.innerHTML = events
    .map((event, i) => {
      const isMatch = matchedIds.has(event.event_id);
      const highlightClass = isMatch ? "search-highlight" : "search-dimmed";

      const typeClass = event.event_type;
      const time = new Date(event.timestamp).toLocaleTimeString();

      let ioHtml = "";
      if (event.input_data || event.output_data) {
        ioHtml = `<div class="event-io">`;
        if (event.input_data) {
          ioHtml += `
          <div class="event-io-block">
            <div class="io-label">Input</div>
            ${highlightText(formatJson(event.input_data), searchQuery)}
          </div>`;
        }
        if (event.output_data) {
          ioHtml += `
          <div class="event-io-block">
            <div class="io-label">Output</div>
            ${highlightText(formatJson(event.output_data), searchQuery)}
          </div>`;
        }
        ioHtml += `</div>`;
      }

      let toolHtml = "";
      if (event.tool_call) {
        const tc = event.tool_call;
        toolHtml = `
        <div class="event-io">
          <div class="event-io-block">
            <div class="io-label">Tool: ${highlightText(escHtml(tc.tool_name), searchQuery)} — Input</div>
            ${highlightText(formatJson(tc.tool_input), searchQuery)}
          </div>
          <div class="event-io-block">
            <div class="io-label">Tool Output</div>
            ${tc.tool_output ? highlightText(formatJson(tc.tool_output), searchQuery) : "<em>none</em>"}
          </div>
        </div>`;
      }

      let reasoningHtml = "";
      if (event.decision_trace && event.decision_trace.reasoning) {
        reasoningHtml = `
        <div class="event-reasoning">
          <div class="reasoning-label">💡 Decision Reasoning</div>
          ${highlightText(escHtml(event.decision_trace.reasoning), searchQuery)}
        </div>`;
      }

      let statsHtml = "";
      const stats = [];
      if (event.tokens_in || event.tokens_out) {
        stats.push(
          `<span>📊 ${event.tokens_in} → ${event.tokens_out} tokens</span>`
        );
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
      <div class="timeline-event ${typeClass} ${highlightClass}">
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
    })
    .join("");
}

function highlightText(text, query) {
  if (!query || !text) return text;
  const terms = query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  if (terms.length === 0) return text;

  // Escape regex special characters in each term
  const escapedTerms = terms.map((t) =>
    t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  );
  const regex = new RegExp(`(${escapedTerms.join("|")})`, "gi");
  return text.replace(regex, '<span class="search-match-text">$1</span>');
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

// ── Alert Rules ─────────────────────────────────────────────────────

let alertRulesCache = [];
let alertMetricsCache = null;
let alertEditingRuleId = null;

async function openAlertsModal() {
  document.getElementById("alertsModal").style.display = "flex";
  await loadAlertMetrics();
  await loadAlertRules();
  await loadAlertSummary();
}

function closeAlertsModal() {
  document.getElementById("alertsModal").style.display = "none";
}

// Close on backdrop click
document.addEventListener("click", (e) => {
  if (e.target.id === "alertsModal") closeAlertsModal();
});

function switchAlertTab(tabName) {
  document.querySelectorAll(".alerts-modal-content .tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".alert-tab-content").forEach(t => {
    t.classList.remove("active");
    t.style.display = "none";
  });
  const tab = document.getElementById(`${tabName}Tab`);
  if (tab) { tab.classList.add("active"); tab.style.display = "block"; }
  document.querySelectorAll(`.alerts-modal-content .tab[data-tab="${tabName}"]`).forEach(t => t.classList.add("active"));

  if (tabName === "alertHistory") loadAlertHistory();
}

async function loadAlertMetrics() {
  if (alertMetricsCache) return;
  try {
    const res = await fetch(`${API_BASE}/alerts/metrics`);
    alertMetricsCache = await res.json();
    const sel = document.getElementById("alertFormMetric");
    sel.innerHTML = alertMetricsCache.metrics.map(m =>
      `<option value="${m.name}" title="${escHtml(m.description)}">${m.name}</option>`
    ).join("");
  } catch (e) {
    console.error("Failed to load alert metrics:", e);
  }
}

async function loadAlertRules() {
  const list = document.getElementById("alertRulesList");
  try {
    const res = await fetch(`${API_BASE}/alerts/rules`);
    const data = await res.json();
    alertRulesCache = data.rules || [];

    if (alertRulesCache.length === 0) {
      list.innerHTML = '<div class="loading">No alert rules configured. Click "+ New Rule" to create one.</div>';
      return;
    }

    list.innerHTML = alertRulesCache.map(r => `
      <div class="alert-rule-card ${r.enabled ? '' : 'disabled'}">
        <div class="rule-status-dot ${r.enabled ? 'active' : 'disabled'}" title="${r.enabled ? 'Enabled' : 'Disabled'}"></div>
        <div class="rule-info">
          <div class="rule-name">${escHtml(r.name)}</div>
          <div class="rule-condition">
            ${escHtml(r.metric)} ${escHtml(r.operator)} ${r.threshold.toLocaleString()}
            &nbsp;•&nbsp; ${r.window_minutes}min window
            ${r.agent_filter ? ` • agent: ${escHtml(r.agent_filter)}` : ''}
            &nbsp;•&nbsp; ${r.cooldown_minutes}min cooldown
          </div>
        </div>
        <div class="rule-actions">
          <button onclick="toggleAlertRule('${escHtml(r.rule_id)}', ${!r.enabled})" title="${r.enabled ? 'Disable' : 'Enable'}">
            ${r.enabled ? '⏸' : '▶'}
          </button>
          <button onclick="editAlertRule('${escHtml(r.rule_id)}')" title="Edit">✏️</button>
          <button class="btn-danger" onclick="deleteAlertRule('${escHtml(r.rule_id)}')" title="Delete">🗑</button>
        </div>
      </div>
    `).join("");
  } catch (e) {
    list.innerHTML = '<div class="loading">Failed to load alert rules.</div>';
    console.error("Error loading alert rules:", e);
  }
}

async function loadAlertSummary() {
  const summary = document.getElementById("alertsSummary");
  try {
    const [rulesRes, eventsRes] = await Promise.all([
      fetch(`${API_BASE}/alerts/rules`),
      fetch(`${API_BASE}/alerts/events?limit=200`),
    ]);
    const rulesData = await rulesRes.json();
    const eventsData = await eventsRes.json();

    const totalRules = rulesData.rules?.length || 0;
    const enabledRules = rulesData.rules?.filter(r => r.enabled).length || 0;
    const totalAlerts = eventsData.events?.length || 0;
    const unacked = eventsData.events?.filter(e => !e.acknowledged).length || 0;

    summary.innerHTML = `
      <div class="alert-stat-card">
        <div class="stat-value">${totalRules}</div>
        <div class="stat-label">Total Rules</div>
      </div>
      <div class="alert-stat-card">
        <div class="stat-value">${enabledRules}</div>
        <div class="stat-label">Enabled</div>
      </div>
      <div class="alert-stat-card">
        <div class="stat-value">${totalAlerts}</div>
        <div class="stat-label">Alerts Fired</div>
      </div>
      <div class="alert-stat-card">
        <div class="stat-value" style="color:${unacked > 0 ? '#f85149' : '#3fb950'}">${unacked}</div>
        <div class="stat-label">Unacknowledged</div>
      </div>
    `;

    // Update header badge
    const badge = document.getElementById("alertBadge");
    if (unacked > 0) {
      badge.textContent = unacked;
      badge.style.display = "inline";
    } else {
      badge.style.display = "none";
    }
  } catch (e) {
    summary.innerHTML = "";
  }
}

function showCreateRuleForm() {
  alertEditingRuleId = null;
  document.getElementById("alertFormTitle").textContent = "Create Alert Rule";
  document.getElementById("alertFormRuleId").value = "";
  document.getElementById("alertFormName").value = "";
  document.getElementById("alertFormMetric").value = "total_tokens";
  document.getElementById("alertFormOperator").value = ">";
  document.getElementById("alertFormThreshold").value = "";
  document.getElementById("alertFormWindow").value = "60";
  document.getElementById("alertFormCooldown").value = "15";
  document.getElementById("alertFormAgent").value = "";
  document.getElementById("alertRuleForm").style.display = "block";
}

function editAlertRule(ruleId) {
  const rule = alertRulesCache.find(r => r.rule_id === ruleId);
  if (!rule) return;

  alertEditingRuleId = ruleId;
  document.getElementById("alertFormTitle").textContent = "Edit Alert Rule";
  document.getElementById("alertFormRuleId").value = ruleId;
  document.getElementById("alertFormName").value = rule.name;
  document.getElementById("alertFormMetric").value = rule.metric;
  document.getElementById("alertFormOperator").value = rule.operator;
  document.getElementById("alertFormThreshold").value = rule.threshold;
  document.getElementById("alertFormWindow").value = rule.window_minutes;
  document.getElementById("alertFormCooldown").value = rule.cooldown_minutes;
  document.getElementById("alertFormAgent").value = rule.agent_filter || "";
  document.getElementById("alertRuleForm").style.display = "block";
}

function cancelAlertForm() {
  document.getElementById("alertRuleForm").style.display = "none";
  alertEditingRuleId = null;
}

async function saveAlertRule() {
  const name = document.getElementById("alertFormName").value.trim();
  const metric = document.getElementById("alertFormMetric").value;
  const operator = document.getElementById("alertFormOperator").value;
  const threshold = parseFloat(document.getElementById("alertFormThreshold").value);
  const window_minutes = parseInt(document.getElementById("alertFormWindow").value) || 60;
  const cooldown_minutes = parseInt(document.getElementById("alertFormCooldown").value) || 15;
  const agent_filter = document.getElementById("alertFormAgent").value.trim() || null;

  if (!name) { alert("Name is required"); return; }
  if (isNaN(threshold)) { alert("Threshold must be a number"); return; }

  const payload = { name, metric, operator, threshold, window_minutes, cooldown_minutes, agent_filter };

  try {
    if (alertEditingRuleId) {
      await fetch(`${API_BASE}/alerts/rules/${alertEditingRuleId}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
    } else {
      await fetch(`${API_BASE}/alerts/rules`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
    }
    cancelAlertForm();
    await loadAlertRules();
    await loadAlertSummary();
  } catch (e) {
    alert("Failed to save rule: " + e.message);
  }
}

async function deleteAlertRule(ruleId) {
  if (!confirm("Delete this alert rule?")) return;
  try {
    await fetch(`${API_BASE}/alerts/rules/${ruleId}`, { method: "DELETE" });
    await loadAlertRules();
    await loadAlertSummary();
  } catch (e) {
    alert("Failed to delete rule: " + e.message);
  }
}

async function toggleAlertRule(ruleId, enabled) {
  try {
    await fetch(`${API_BASE}/alerts/rules/${ruleId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    await loadAlertRules();
    await loadAlertSummary();
  } catch (e) {
    console.error("Error toggling rule:", e);
  }
}

async function evaluateAllAlerts() {
  const list = document.getElementById("alertRulesList");
  try {
    const res = await fetch(`${API_BASE}/alerts/evaluate`, { method: "POST" });
    const data = await res.json();

    // Show evaluation results as a toast above the rules list
    const resultsHtml = data.results.map(r => {
      const cls = r.status === "fired" ? "fired" : r.status === "cooldown" ? "cooldown" : "ok";
      const icon = r.status === "fired" ? "🔴" : r.status === "cooldown" ? "🟡" : "🟢";
      return `<div>${icon} <strong>${escHtml(r.name)}</strong>: ${r.current_value.toLocaleString()} ${escHtml(r.operator)} ${r.threshold.toLocaleString()} — <span class="${cls}">${r.status}</span></div>`;
    }).join("");

    const evalDiv = document.createElement("div");
    evalDiv.className = "alert-eval-result";
    evalDiv.innerHTML = `<strong>⚡ Evaluation Results</strong> (${data.fired} fired, ${data.cooldown} cooldown, ${data.ok} ok)<br>${resultsHtml}`;

    // Remove old eval results
    document.querySelectorAll(".alert-eval-result").forEach(e => e.remove());
    list.parentNode.insertBefore(evalDiv, list);

    await loadAlertSummary();
    await loadAlertRules();

    // Auto-remove after 10 seconds
    setTimeout(() => evalDiv.remove(), 10000);
  } catch (e) {
    alert("Failed to evaluate alerts: " + e.message);
  }
}

async function loadAlertHistory() {
  const list = document.getElementById("alertHistoryList");
  try {
    const res = await fetch(`${API_BASE}/alerts/events?limit=100`);
    const data = await res.json();

    if (!data.events || data.events.length === 0) {
      list.innerHTML = '<div class="loading">No alerts triggered yet.</div>';
      return;
    }

    list.innerHTML = data.events.map(e => `
      <div class="alert-event-card ${e.acknowledged ? 'acknowledged' : 'unacknowledged'}">
        <div class="alert-event-info">
          <div class="alert-event-rule">${e.acknowledged ? '✅' : '🔴'} ${escHtml(e.rule_name)}</div>
          <div class="alert-event-detail">
            ${escHtml(e.metric)} = ${e.metric_value.toLocaleString()} (threshold: ${e.operator} ${e.threshold.toLocaleString()})
          </div>
        </div>
        <div class="alert-event-time">${formatTime(e.triggered_at)}</div>
        ${!e.acknowledged ? `<button class="btn btn-secondary" style="font-size:0.75rem;padding:4px 8px" onclick="acknowledgeAlert('${escHtml(e.alert_id)}')">Ack</button>` : ''}
      </div>
    `).join("");
  } catch (e) {
    list.innerHTML = '<div class="loading">Failed to load alert history.</div>';
  }
}

async function acknowledgeAlert(alertId) {
  try {
    await fetch(`${API_BASE}/alerts/events/${alertId}/acknowledge`, { method: "PUT" });
    await loadAlertHistory();
    await loadAlertSummary();
  } catch (e) {
    alert("Failed to acknowledge alert: " + e.message);
  }
}

// ── Session Search ──────────────────────────────────────────────────

let sessionSearchActive = false;

function toggleSessionAdvFilters() {
  const el = document.getElementById("sessionAdvFilters");
  el.style.display = el.style.display === "none" ? "flex" : "none";
}

async function searchSessions() {
  const q = document.getElementById("sessionSearchInput").value.trim();
  const agent = document.getElementById("sessionAgentFilter").value.trim();
  const status = document.getElementById("statusFilter").value;
  const after = document.getElementById("sessionAfterDate").value;
  const before = document.getElementById("sessionBeforeDate").value;
  const minTokens = document.getElementById("sessionMinTokens").value;
  const maxTokens = document.getElementById("sessionMaxTokens").value;
  const tags = document.getElementById("sessionTagsFilter").value.trim();
  const sort = document.getElementById("sessionSortBy").value;
  const order = document.getElementById("sessionSortOrder").value;

  // If all empty, just reload normally
  if (!q && !agent && !status && !after && !before && !minTokens && !maxTokens && !tags) {
    clearSessionSearch();
    return;
  }

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (agent) params.set("agent", agent);
  if (status) params.set("status", status);
  if (after) params.set("after", after + "T00:00:00Z");
  if (before) params.set("before", before + "T23:59:59Z");
  if (minTokens) params.set("min_tokens", minTokens);
  if (maxTokens) params.set("max_tokens", maxTokens);
  if (tags) params.set("tags", tags);
  params.set("sort", sort);
  params.set("order", order);
  params.set("limit", "100");

  const listEl = document.getElementById("sessionList");
  listEl.innerHTML = '<div class="loading">Searching...</div>';

  try {
    const res = await fetch(`${API_BASE}/sessions/search?${params}`);
    const data = await res.json();

    sessionSearchActive = true;

    // Show results bar
    const bar = document.getElementById("sessionSearchResultsBar");
    bar.style.display = "flex";
    document.getElementById("sessionSearchResultsText").textContent =
      `Found ${data.total} session${data.total !== 1 ? 's' : ''} matching your filters`;

    if (!data.sessions || data.sessions.length === 0) {
      listEl.innerHTML = '<div class="loading">No sessions match your search.</div>';
      return;
    }

    listEl.innerHTML = data.sessions.map((s) => {
      const tagsHtml = s.tags && s.tags.length > 0
        ? `<span style="margin-left:8px">${s.tags.map(t => `<span class="status-badge" style="background:var(--accent);font-size:0.65rem;padding:1px 6px">${escHtml(t)}</span>`).join(' ')}</span>`
        : '';
      return `
        <div class="session-card ${compareSelection.some(c => c.id === s.session_id) ? 'selected' : ''}" data-session-id="${escHtml(s.session_id)}">
          <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
            <input type="checkbox" class="compare-checkbox"
              ${compareSelection.some(c => c.id === s.session_id) ? 'checked' : ''}
              onclick="event.stopPropagation(); toggleCompare('${escHtml(s.session_id)}', '${escHtml(s.agent_name)}')"
              title="Select for comparison">
            <div class="session-card-left" onclick="loadSessionDetail('${escHtml(s.session_id)}')">
              <div class="session-agent">${escHtml(s.agent_name)}${tagsHtml}</div>
              <div class="session-meta">
                <span>🆔 ${escHtml(s.session_id.slice(0, 8))}…</span>
                <span>🕐 ${formatTime(s.started_at)}</span>
                ${s.metadata?.version ? `<span>📦 v${escHtml(s.metadata.version)}</span>` : ""}
                ${s.metadata?.environment ? `<span>🌍 ${escHtml(s.metadata.environment)}</span>` : ""}
              </div>
            </div>
          </div>
          <div class="session-card-right" onclick="loadSessionDetail('${escHtml(s.session_id)}')">
            <div class="session-tokens">
              <div class="count">${(s.total_tokens_in + s.total_tokens_out).toLocaleString()}</div>
              <div>tokens</div>
            </div>
            <span class="status-badge ${escHtml(s.status)}">${escHtml(s.status)}</span>
          </div>
        </div>
      `;
    }).join("");
  } catch (err) {
    listEl.innerHTML = `<div class="loading">Search error: ${escHtml(err.message)}</div>`;
  }
}

function clearSessionSearch() {
  sessionSearchActive = false;
  document.getElementById("sessionSearchInput").value = "";
  document.getElementById("sessionAgentFilter").value = "";
  document.getElementById("sessionAfterDate").value = "";
  document.getElementById("sessionBeforeDate").value = "";
  document.getElementById("sessionMinTokens").value = "";
  document.getElementById("sessionMaxTokens").value = "";
  document.getElementById("sessionTagsFilter").value = "";
  document.getElementById("sessionSortBy").value = "started_at";
  document.getElementById("sessionSortOrder").value = "desc";
  document.getElementById("sessionSearchResultsBar").style.display = "none";
  loadSessions();
}

// ── Bookmarks ───────────────────────────────────────────────────────

let bookmarkedIds = new Set();
let showOnlyBookmarks = false;

async function loadBookmarks() {
  try {
    const res = await fetch(`${API_BASE}/bookmarks`);
    const data = await res.json();
    bookmarkedIds = new Set((data.bookmarks || []).map(b => b.session_id));
  } catch (e) {
    console.error("Failed to load bookmarks:", e);
  }
}

async function toggleBookmark(sessionId, event) {
  if (event) event.stopPropagation();
  const isBookmarked = bookmarkedIds.has(sessionId);
  try {
    if (isBookmarked) {
      await fetch(`${API_BASE}/bookmarks/${sessionId}`, { method: "DELETE" });
      bookmarkedIds.delete(sessionId);
    } else {
      await fetch(`${API_BASE}/bookmarks/${sessionId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: "" }),
      });
      bookmarkedIds.add(sessionId);
    }
    // Re-render the star icon
    const starEl = document.querySelector(`[data-bookmark-id="${CSS.escape(sessionId)}"]`);
    if (starEl) {
      starEl.textContent = bookmarkedIds.has(sessionId) ? "⭐" : "☆";
      starEl.title = bookmarkedIds.has(sessionId) ? "Remove bookmark" : "Bookmark session";
    }
    showToast(isBookmarked ? "Bookmark removed" : "⭐ Session bookmarked");
  } catch (e) {
    showToast("Failed to update bookmark");
  }
}

function toggleBookmarkFilter() {
  showOnlyBookmarks = !showOnlyBookmarks;
  const btn = document.getElementById("bookmarkFilterBtn");
  if (btn) {
    btn.classList.toggle("active", showOnlyBookmarks);
    btn.title = showOnlyBookmarks ? "Show all sessions" : "Show bookmarked only";
  }
  loadSessions();
}

// ── Annotations ─────────────────────────────────────────────────────

const ANNOTATION_ICONS = { note: '📝', bug: '🐛', insight: '💡', warning: '⚠️', milestone: '🏁' };

function showAnnotationForm() {
  document.getElementById("annotationForm").style.display = "block";
  document.getElementById("annotationText").focus();
}

function hideAnnotationForm() {
  document.getElementById("annotationForm").style.display = "none";
  document.getElementById("annotationText").value = "";
  document.getElementById("annotationAuthor").value = "";
  document.getElementById("annotationType").value = "note";
}

async function loadAnnotations() {
  if (!currentSession) return;
  const list = document.getElementById("annotationsList");
  list.innerHTML = '<div class="loading">Loading annotations...</div>';

  const typeFilter = document.getElementById("annotationTypeFilter").value;
  const params = new URLSearchParams();
  if (typeFilter) params.set("type", typeFilter);
  params.set("limit", "200");

  try {
    const res = await fetch(`${API_BASE}/sessions/${currentSession.session_id}/annotations?${params}`);
    const data = await res.json();

    if (!data.annotations || data.annotations.length === 0) {
      list.innerHTML = '<div class="loading">No annotations yet. Click "+ Add Note" to create one.</div>';
      return;
    }

    list.innerHTML = data.annotations.map(a => `
      <div class="annotation-card annotation-${escHtml(a.type)}">
        <div class="annotation-header">
          <span class="annotation-type-badge">${ANNOTATION_ICONS[a.type] || '📝'} ${escHtml(a.type)}</span>
          <span class="annotation-author">${escHtml(a.author)}</span>
          <span class="annotation-time">${formatTime(a.created_at)}</span>
          <button class="btn btn-ghost annotation-delete" onclick="deleteAnnotation('${escHtml(a.annotation_id)}')" title="Delete">🗑</button>
        </div>
        <div class="annotation-body">${escHtml(a.text)}</div>
      </div>
    `).join("");
  } catch (e) {
    list.innerHTML = `<div class="loading">Failed to load annotations: ${escHtml(e.message)}</div>`;
  }
}

async function saveAnnotation() {
  if (!currentSession) return;
  const text = document.getElementById("annotationText").value.trim();
  if (!text) { alert("Note text is required"); return; }

  const author = document.getElementById("annotationAuthor").value.trim() || "user";
  const type = document.getElementById("annotationType").value;

  try {
    const res = await fetch(`${API_BASE}/sessions/${currentSession.session_id}/annotations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, author, type }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert("Failed: " + (err.error || res.statusText));
      return;
    }
    hideAnnotationForm();
    showToast("📝 Annotation saved");
    loadAnnotations();
  } catch (e) {
    alert("Failed to save annotation: " + e.message);
  }
}

async function deleteAnnotation(annotationId) {
  if (!currentSession) return;
  if (!confirm("Delete this annotation?")) return;
  try {
    await fetch(`${API_BASE}/sessions/${currentSession.session_id}/annotations/${annotationId}`, { method: "DELETE" });
    showToast("Annotation deleted");
    loadAnnotations();
  } catch (e) {
    alert("Failed to delete: " + e.message);
  }
}

// ── Command Palette (Ctrl+K) ───────────────────────────────────────

let commandPaletteActive = false;
let commandActiveIndex = 0;
let filteredCommands = [];

function getCommandRegistry() {
  const cmds = [
    // Navigation
    { group: "Navigation", icon: "📋", name: "Go to Sessions", desc: "View all sessions", action: () => { showSessionList(); }, keywords: "sessions list home" },
    { group: "Navigation", icon: "📈", name: "Toggle Analytics", desc: "Show/hide analytics panel", action: () => { toggleAnalytics(); }, keywords: "analytics stats charts" },
    { group: "Navigation", icon: "🔔", name: "Open Alerts", desc: "View alerts and rules", action: () => { openAlertsModal(); }, keywords: "alerts notifications rules" },

    // Actions
    { group: "Actions", icon: "↻", name: "Refresh Sessions", desc: "Reload the session list", action: () => { loadSessions(); }, keywords: "refresh reload update" },
    { group: "Actions", icon: "☆", name: "Toggle Bookmark Filter", desc: "Show only bookmarked sessions", action: () => { toggleBookmarkFilter(); }, keywords: "bookmark star filter" },
    { group: "Actions", icon: "🔎", name: "Search Sessions", desc: "Search sessions by name or ID", action: () => { document.getElementById("sessionSearchInput")?.focus(); }, keywords: "search find query" },
    { group: "Actions", icon: "✕", name: "Clear Compare Selection", desc: "Reset comparison picks", action: () => { clearCompareSelection(); }, keywords: "compare clear reset" },

    // Filters
    { group: "Filters", icon: "🟢", name: "Filter: Active", desc: "Show only active sessions", action: () => { document.getElementById("statusFilter").value = "active"; loadSessions(); }, keywords: "filter active status" },
    { group: "Filters", icon: "✅", name: "Filter: Completed", desc: "Show only completed sessions", action: () => { document.getElementById("statusFilter").value = "completed"; loadSessions(); }, keywords: "filter completed done" },
    { group: "Filters", icon: "🔴", name: "Filter: Error", desc: "Show only errored sessions", action: () => { document.getElementById("statusFilter").value = "error"; loadSessions(); }, keywords: "filter error failed" },
    { group: "Filters", icon: "📊", name: "Filter: All Statuses", desc: "Remove status filter", action: () => { document.getElementById("statusFilter").value = ""; loadSessions(); }, keywords: "filter all clear" },
  ];

  // Session-specific commands when viewing a session
  if (currentSession) {
    cmds.push(
      { group: "Current Session", icon: "📄", name: "Export as JSON", desc: `Export ${currentSession.session_id.slice(0, 8)}… as JSON`, action: () => { exportSession("json"); }, keywords: "export json download" },
      { group: "Current Session", icon: "📄", name: "Export as CSV", desc: `Export ${currentSession.session_id.slice(0, 8)}… as CSV`, action: () => { exportSession("csv"); }, keywords: "export csv download" },
      { group: "Current Session", icon: "💡", name: "Load Explanation", desc: "Get AI explanation for this session", action: () => { loadExplanation(currentSession.session_id); }, keywords: "explain ai insight" },
      { group: "Current Session", icon: "💰", name: "View Costs", desc: "Show cost breakdown", action: () => { switchTab("costs"); }, keywords: "cost pricing tokens money" },
      { group: "Current Session", icon: "🚨", name: "View Errors", desc: "Error analytics dashboard", action: () => { switchTab("errors"); }, keywords: "error failure crash bug debug" },
      { group: "Current Session", icon: "🔥", name: "View Postmortem", desc: "Generate incident report", action: () => { switchTab("postmortem"); }, keywords: "postmortem incident report root cause severity" },
      { group: "Current Session", icon: "📝", name: "Add Annotation", desc: "Add a note to this session", action: () => { showAnnotationForm(); }, keywords: "annotate note comment" },
      { group: "Current Session", icon: "⭐", name: "Toggle Bookmark", desc: "Star/unstar this session", action: () => { toggleBookmark(currentSession.session_id); }, keywords: "bookmark star favorite" },
      { group: "Current Session", icon: "⬅️", name: "Back to Sessions", desc: "Return to session list", action: () => { showSessionList(); }, keywords: "back list return" }
    );
  }

  return cmds;
}

function openCommandPalette() {
  const overlay = document.getElementById("commandPalette");
  overlay.style.display = "flex";
  commandPaletteActive = true;
  commandActiveIndex = 0;
  const input = document.getElementById("commandPaletteInput");
  input.value = "";
  filterCommands();
  setTimeout(() => input.focus(), 50);
}

function closeCommandPalette(e) {
  if (e && e.target && e.target.id !== "commandPalette" && e.target.className !== "command-palette-overlay") return;
  const overlay = document.getElementById("commandPalette");
  overlay.style.display = "none";
  commandPaletteActive = false;
}

function closeCommandPaletteForce() {
  document.getElementById("commandPalette").style.display = "none";
  commandPaletteActive = false;
}

function filterCommands() {
  const query = document.getElementById("commandPaletteInput").value.toLowerCase().trim();
  const all = getCommandRegistry();

  if (!query) {
    filteredCommands = all;
  } else {
    filteredCommands = all.filter(cmd => {
      const text = `${cmd.name} ${cmd.desc} ${cmd.keywords}`.toLowerCase();
      return query.split(/\s+/).every(word => text.includes(word));
    });
  }

  commandActiveIndex = 0;
  renderCommandResults();
}

function renderCommandResults() {
  const container = document.getElementById("commandPaletteResults");

  if (filteredCommands.length === 0) {
    container.innerHTML = '<div class="command-palette-empty">No matching commands</div>';
    return;
  }

  let html = "";
  let lastGroup = "";
  filteredCommands.forEach((cmd, i) => {
    if (cmd.group !== lastGroup) {
      html += `<div class="command-palette-group">${escHtml(cmd.group)}</div>`;
      lastGroup = cmd.group;
    }
    html += `<div class="command-palette-item ${i === commandActiveIndex ? 'active' : ''}"
      onmouseenter="commandActiveIndex=${i};renderCommandResults()"
      onclick="executeCommand(${i})">
      <span class="command-palette-item-icon">${cmd.icon}</span>
      <div class="command-palette-item-label">
        <div class="command-palette-item-name">${escHtml(cmd.name)}</div>
        <div class="command-palette-item-desc">${escHtml(cmd.desc)}</div>
      </div>
    </div>`;
  });

  container.innerHTML = html;

  // Scroll active item into view
  const active = container.querySelector(".command-palette-item.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}

function handleCommandKey(e) {
  if (e.key === "Escape") {
    e.preventDefault();
    closeCommandPaletteForce();
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    commandActiveIndex = Math.min(commandActiveIndex + 1, filteredCommands.length - 1);
    renderCommandResults();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    commandActiveIndex = Math.max(commandActiveIndex - 1, 0);
    renderCommandResults();
  } else if (e.key === "Enter") {
    e.preventDefault();
    executeCommand(commandActiveIndex);
  }
}

function executeCommand(index) {
  const cmd = filteredCommands[index];
  if (!cmd) return;
  closeCommandPaletteForce();
  cmd.action();
}

// Global keyboard shortcut: Ctrl+K / Cmd+K
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    e.preventDefault();
    if (commandPaletteActive) {
      closeCommandPaletteForce();
    } else {
      openCommandPalette();
    }
  }
  if (e.key === "Escape" && commandPaletteActive) {
    closeCommandPaletteForce();
  }
});

// ── Error Analytics ─────────────────────────────────────────────────

async function loadErrors() {
  const loadingEl = document.getElementById("errorsLoading");
  const contentEl = document.getElementById("errorsContent");
  const emptyEl = document.getElementById("errorsEmpty");

  loadingEl.style.display = "block";
  contentEl.style.display = "none";
  emptyEl.style.display = "none";

  try {
    const res = await fetch(`${API_BASE}/errors?days=30&limit=20`);
    if (!res.ok) throw new Error("Failed to load error analytics");
    errorData = await res.json();

    loadingEl.style.display = "none";

    if (errorData.summary.total_errors === 0) {
      emptyEl.style.display = "block";
      return;
    }

    contentEl.style.display = "block";

    renderErrorSummaryCards(errorData);
    renderErrorRateChart(errorData);
    renderErrorHourlyChart(errorData);
    renderErrorTypeChart(errorData);
    renderErrorModelTable(errorData);
    renderErrorAgentTable(errorData);
    renderTopErrors(errorData);
    renderErrorSessions(errorData);
  } catch (err) {
    loadingEl.textContent = `Error: ${escHtml(err.message)}`;
  }
}

function renderErrorSummaryCards(data) {
  const s = data.summary;
  const mtbfText = s.mtbf
    ? (s.mtbf.mean_minutes >= 60
        ? `${(s.mtbf.mean_minutes / 60).toFixed(1)}h`
        : `${s.mtbf.mean_minutes}m`)
    : "N/A";

  const severityClass = s.error_rate_percent > 10 ? "error-high"
    : s.error_rate_percent > 3 ? "error-medium" : "error-low";

  document.getElementById("errorSummaryCards").innerHTML = `
    <div class="info-card">
      <div class="label">Total Errors</div>
      <div class="value" style="color:var(--red)">${s.total_errors.toLocaleString()}</div>
    </div>
    <div class="info-card">
      <div class="label">Error Rate</div>
      <div class="value ${severityClass}">${s.error_rate_percent.toFixed(2)}%</div>
    </div>
    <div class="info-card">
      <div class="label">Affected Sessions</div>
      <div class="value">${s.affected_sessions}</div>
    </div>
    <div class="info-card">
      <div class="label">Session Error Rate</div>
      <div class="value">${s.session_error_rate_percent.toFixed(1)}%</div>
    </div>
    <div class="info-card">
      <div class="label">MTBF</div>
      <div class="value" title="Mean Time Between Failures">${mtbfText}</div>
    </div>
  `;
}

function renderErrorRateChart(data) {
  const canvas = document.getElementById("errorRateChart");
  const ctx = canvas.getContext("2d");
  const rates = data.rate_over_time.slice().reverse(); // oldest first

  if (rates.length === 0) {
    ctx.fillStyle = "#888";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No error rate data", canvas.width / 2, canvas.height / 2);
    return;
  }

  const W = canvas.width;
  const H = canvas.height;
  const pad = { top: 30, right: 20, bottom: 60, left: 60 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  ctx.clearRect(0, 0, W, H);

  const maxRate = Math.max(...rates.map(r => r.error_rate), 1);
  const maxCount = Math.max(...rates.map(r => r.error_count), 1);

  // Draw gridlines
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (plotH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(W - pad.right, y);
    ctx.stroke();
  }

  // Bar chart for error count
  const barW = Math.max(2, (plotW / rates.length) * 0.6);
  ctx.fillStyle = "rgba(239,68,68,0.3)";
  rates.forEach((r, i) => {
    const x = pad.left + (i + 0.5) * (plotW / rates.length) - barW / 2;
    const barH = (r.error_count / maxCount) * plotH;
    ctx.fillRect(x, pad.top + plotH - barH, barW, barH);
  });

  // Line chart for error rate %
  ctx.strokeStyle = "#ef4444";
  ctx.lineWidth = 2;
  ctx.beginPath();
  rates.forEach((r, i) => {
    const x = pad.left + (i + 0.5) * (plotW / rates.length);
    const y = pad.top + plotH - (r.error_rate / maxRate) * plotH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Dots on line
  ctx.fillStyle = "#ef4444";
  rates.forEach((r, i) => {
    const x = pad.left + (i + 0.5) * (plotW / rates.length);
    const y = pad.top + plotH - (r.error_rate / maxRate) * plotH;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });

  // X-axis labels (show every Nth)
  ctx.fillStyle = "#888";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "center";
  const step = Math.max(1, Math.floor(rates.length / 8));
  rates.forEach((r, i) => {
    if (i % step === 0 || i === rates.length - 1) {
      const x = pad.left + (i + 0.5) * (plotW / rates.length);
      const label = r.day.slice(5); // MM-DD
      ctx.save();
      ctx.translate(x, pad.top + plotH + 12);
      ctx.rotate(-0.5);
      ctx.fillText(label, 0, 0);
      ctx.restore();
    }
  });

  // Y-axis labels (error rate %)
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (plotH / 4) * (4 - i);
    const val = ((maxRate / 4) * i).toFixed(1);
    ctx.fillText(`${val}%`, pad.left - 8, y + 4);
  }

  // Legend
  ctx.fillStyle = "rgba(239,68,68,0.3)";
  ctx.fillRect(pad.left + 10, 8, 14, 10);
  ctx.fillStyle = "#888";
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("Count", pad.left + 28, 17);

  ctx.strokeStyle = "#ef4444";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(pad.left + 90, 13);
  ctx.lineTo(pad.left + 104, 13);
  ctx.stroke();
  ctx.fillText("Rate %", pad.left + 108, 17);
}

function renderErrorHourlyChart(data) {
  const canvas = document.getElementById("errorHourlyChart");
  const ctx = canvas.getContext("2d");
  const hours = data.hourly_distribution;

  // Build full 24-hour array
  const counts = new Array(24).fill(0);
  hours.forEach(h => { counts[h.hour] = h.error_count; });

  const W = canvas.width;
  const H = canvas.height;
  const pad = { top: 20, right: 20, bottom: 40, left: 50 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  ctx.clearRect(0, 0, W, H);

  const maxCount = Math.max(...counts, 1);
  const barW = (plotW / 24) * 0.7;

  // Bars
  counts.forEach((count, hour) => {
    const x = pad.left + (hour + 0.5) * (plotW / 24) - barW / 2;
    const barH = (count / maxCount) * plotH;
    const intensity = count / maxCount;
    ctx.fillStyle = `rgba(239, 68, 68, ${0.2 + intensity * 0.6})`;
    ctx.fillRect(x, pad.top + plotH - barH, barW, barH);
  });

  // X-axis labels
  ctx.fillStyle = "#888";
  ctx.font = "10px sans-serif";
  ctx.textAlign = "center";
  for (let h = 0; h < 24; h += 3) {
    const x = pad.left + (h + 0.5) * (plotW / 24);
    ctx.fillText(`${h}:00`, x, pad.top + plotH + 18);
  }

  // Y-axis labels
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (plotH / 4) * (4 - i);
    const val = Math.round((maxCount / 4) * i);
    ctx.fillText(val, pad.left - 8, y + 4);
  }
}

function renderErrorTypeChart(data) {
  const canvas = document.getElementById("errorTypeChart");
  const ctx = canvas.getContext("2d");
  const types = data.by_type;

  if (types.length === 0) {
    ctx.fillStyle = "#888";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No data", canvas.width / 2, canvas.height / 2);
    return;
  }

  const W = canvas.width;
  const H = canvas.height;
  const centerX = W / 2;
  const centerY = H / 2;
  const radius = Math.min(W, H) * 0.35;

  const colors = ["#ef4444", "#f97316", "#eab308", "#3b82f6", "#8b5cf6", "#06b6d4"];
  const total = types.reduce((sum, t) => sum + t.count, 0);

  ctx.clearRect(0, 0, W, H);

  let startAngle = -Math.PI / 2;
  types.forEach((t, i) => {
    const sliceAngle = (t.count / total) * Math.PI * 2;
    ctx.fillStyle = colors[i % colors.length];
    ctx.beginPath();
    ctx.moveTo(centerX, centerY);
    ctx.arc(centerX, centerY, radius, startAngle, startAngle + sliceAngle);
    ctx.closePath();
    ctx.fill();

    // Label
    const midAngle = startAngle + sliceAngle / 2;
    const labelR = radius + 20;
    const lx = centerX + Math.cos(midAngle) * labelR;
    const ly = centerY + Math.sin(midAngle) * labelR;
    ctx.fillStyle = "#ccc";
    ctx.font = "11px sans-serif";
    ctx.textAlign = midAngle > Math.PI / 2 && midAngle < Math.PI * 1.5 ? "right" : "left";
    const pct = ((t.count / total) * 100).toFixed(0);
    ctx.fillText(`${t.event_type} (${pct}%)`, lx, ly);

    startAngle += sliceAngle;
  });

  // Center hole for donut effect
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--bg-primary").trim() || "#1a1a2e";
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius * 0.55, 0, Math.PI * 2);
  ctx.fill();

  // Center text
  ctx.fillStyle = "#fff";
  ctx.font = "bold 18px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(total.toLocaleString(), centerX, centerY - 8);
  ctx.font = "11px sans-serif";
  ctx.fillStyle = "#888";
  ctx.fillText("total errors", centerX, centerY + 10);
}

function renderErrorModelTable(data) {
  const models = data.by_model;
  if (!models || models.length === 0) {
    document.getElementById("errorModelTable").innerHTML =
      '<p style="color:var(--text-muted);padding:8px">No model-specific error data.</p>';
    return;
  }

  const rows = models.map(m => {
    const errRate = m.total_calls > 0
      ? ((m.error_count / m.total_calls) * 100).toFixed(2) + "%"
      : "—";
    return `<tr>
      <td>${escHtml(m.model)}</td>
      <td style="text-align:right">${m.error_count.toLocaleString()}</td>
      <td style="text-align:right">${m.total_calls.toLocaleString()}</td>
      <td style="text-align:right">${errRate}</td>
      <td style="text-align:right">${m.affected_sessions}</td>
    </tr>`;
  }).join("");

  document.getElementById("errorModelTable").innerHTML = `
    <table class="token-table">
      <thead>
        <tr>
          <th>Model</th>
          <th style="text-align:right">Errors</th>
          <th style="text-align:right">Total Calls</th>
          <th style="text-align:right">Error Rate</th>
          <th style="text-align:right">Sessions</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderErrorAgentTable(data) {
  const agents = data.by_agent;
  const section = document.getElementById("errorAgentSection");

  if (!agents || agents.length === 0) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";

  const rows = agents.map(a => {
    const sessionRate = a.total_sessions > 0
      ? ((a.error_sessions / a.total_sessions) * 100).toFixed(1) + "%"
      : "—";
    return `<tr>
      <td>${escHtml(a.agent_name || "unknown")}</td>
      <td style="text-align:right">${a.error_count.toLocaleString()}</td>
      <td style="text-align:right">${a.error_sessions}</td>
      <td style="text-align:right">${a.total_sessions}</td>
      <td style="text-align:right">${sessionRate}</td>
    </tr>`;
  }).join("");

  document.getElementById("errorAgentTable").innerHTML = `
    <table class="token-table">
      <thead>
        <tr>
          <th>Agent</th>
          <th style="text-align:right">Errors</th>
          <th style="text-align:right">Error Sessions</th>
          <th style="text-align:right">Total Sessions</th>
          <th style="text-align:right">Session Error Rate</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderTopErrors(data) {
  const errors = data.top_errors;
  if (!errors || errors.length === 0) {
    document.getElementById("topErrorsList").innerHTML =
      '<p style="color:var(--text-muted);padding:8px">No error details available.</p>';
    return;
  }

  const items = errors.map(err => {
    const msg = err.error_message || err.output_data || "Unknown error";
    const truncated = msg.length > 150 ? msg.slice(0, 150) + "…" : msg;
    const firstSeen = err.first_seen ? new Date(err.first_seen).toLocaleDateString() : "—";
    const lastSeen = err.last_seen ? new Date(err.last_seen).toLocaleDateString() : "—";

    return `
      <div class="error-item">
        <div class="error-item-header">
          <span class="error-type-badge error-badge-${err.event_type}">${escHtml(err.event_type)}</span>
          <span class="error-count">${err.occurrences}×</span>
          ${err.model ? `<span class="error-model">${escHtml(err.model)}</span>` : ""}
        </div>
        <div class="error-message">${escHtml(truncated)}</div>
        <div class="error-meta">
          ${err.affected_sessions} session${err.affected_sessions !== 1 ? "s" : ""} ·
          First: ${firstSeen} · Last: ${lastSeen}
        </div>
      </div>
    `;
  }).join("");

  document.getElementById("topErrorsList").innerHTML = items;
}

function renderErrorSessions(data) {
  const sessions = data.error_sessions;
  const section = document.getElementById("errorSessionsSection");

  if (!sessions || sessions.length === 0) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";

  const rows = sessions.map(s => {
    const started = new Date(s.started_at).toLocaleString();
    const errPct = s.total_events > 0
      ? ((s.error_count / s.total_events) * 100).toFixed(1) + "%"
      : "—";
    return `<tr class="clickable-row" onclick="loadSessionDetail('${escHtml(s.session_id)}')">
      <td title="${escHtml(s.session_id)}">${escHtml(s.session_id.slice(0, 12))}…</td>
      <td>${escHtml(s.agent_name || "—")}</td>
      <td>${started}</td>
      <td style="text-align:right;color:var(--red)">${s.error_count}</td>
      <td style="text-align:right">${s.total_events}</td>
      <td style="text-align:right">${errPct}</td>
    </tr>`;
  }).join("");

  document.getElementById("errorSessionsList").innerHTML = `
    <table class="token-table">
      <thead>
        <tr>
          <th>Session ID</th>
          <th>Agent</th>
          <th>Started</th>
          <th style="text-align:right">Errors</th>
          <th style="text-align:right">Events</th>
          <th style="text-align:right">Error Rate</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ── Postmortem Dashboard ────────────────────────────────────────────

async function loadPostmortem() {
  if (!currentSession) return;

  var loadingEl = document.getElementById("postmortemLoading");
  var contentEl = document.getElementById("postmortemContent");
  var emptyEl = document.getElementById("postmortemEmpty");

  loadingEl.style.display = "block";
  contentEl.style.display = "none";
  emptyEl.style.display = "none";

  try {
    var res = await fetch(API_BASE + "/postmortem/" + encodeURIComponent(currentSession.session_id), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) throw new Error("Failed to generate postmortem (status " + res.status + ")");
    postmortemData = await res.json();

    loadingEl.style.display = "none";

    if (postmortemData.incident_id === "INC-NONE") {
      emptyEl.style.display = "block";
      return;
    }

    contentEl.style.display = "block";

    renderPostmortemHeader(postmortemData);
    renderPostmortemImpact(postmortemData);
    renderPostmortemCauses(postmortemData);
    renderPostmortemTimeline(postmortemData);
  } catch (err) {
    loadingEl.textContent = "Error: " + escHtml(err.message);
  }
}

function severityColor(severity) {
  if (severity === "SEV-1") return "#ef4444";
  if (severity === "SEV-2") return "#f97316";
  if (severity === "SEV-3") return "#eab308";
  return "#6b7280";
}

function severityBg(severity) {
  if (severity === "SEV-1") return "rgba(239,68,68,0.15)";
  if (severity === "SEV-2") return "rgba(249,115,22,0.15)";
  if (severity === "SEV-3") return "rgba(234,179,8,0.15)";
  return "rgba(107,114,128,0.10)";
}

function renderPostmortemHeader(data) {
  var sevColor = severityColor(data.severity);
  var sevBgColor = severityBg(data.severity);
  var durationText = data.duration_ms ? formatDurationShort(data.duration_ms) : "\u2014";
  var generatedAt = data.generated_at ? formatTime(data.generated_at) : "\u2014";

  document.getElementById("postmortemHeader").innerHTML =
    '<div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center">' +
      '<div style="background:' + sevBgColor + ';color:' + sevColor + ';font-weight:700;font-size:1.5rem;padding:8px 18px;border-radius:8px;border:2px solid ' + sevColor + '">' +
        escHtml(data.severity) +
      '</div>' +
      '<div style="flex:1;min-width:200px">' +
        '<div style="font-size:0.85rem;color:var(--text-muted)">Incident ' + escHtml(data.incident_id) + '</div>' +
        '<div style="font-size:1.1rem;font-weight:600">' + escHtml(data.title) + '</div>' +
      '</div>' +
      '<div class="info-card"><div class="info-label">Duration</div><div class="info-value">' + durationText + '</div></div>' +
      '<div class="info-card"><div class="info-label">Events</div><div class="info-value">' + data.event_count + '</div></div>' +
      '<div class="info-card"><div class="info-label">Generated</div><div class="info-value" style="font-size:0.8rem">' + generatedAt + '</div></div>' +
    '</div>' +
    '<div style="margin-top:12px;padding:10px 14px;background:var(--bg-card);border-radius:6px;color:var(--text-muted);font-size:0.9rem">' +
      escHtml(data.summary) +
    '</div>';
}

function renderPostmortemImpact(data) {
  var impact = data.impact;
  if (!impact) {
    document.getElementById("postmortemImpact").innerHTML = "";
    return;
  }

  var costText = impact.estimated_cost_impact != null
    ? "$" + impact.estimated_cost_impact.toFixed(4)
    : "\u2014";
  var errorPct = impact.error_rate != null
    ? (impact.error_rate * 100).toFixed(1) + "%"
    : "\u2014";
  var downtimeText = impact.downtime_ms ? formatDurationShort(impact.downtime_ms) : "\u2014";

  var toolsHtml = "";
  if (impact.affected_tools && impact.affected_tools.length > 0) {
    toolsHtml = '<div style="margin-top:8px"><strong>Affected Tools:</strong> ' +
      impact.affected_tools.map(function(t) {
        return '<span style="background:rgba(59,130,246,0.15);color:#60a5fa;padding:2px 8px;border-radius:4px;font-size:0.8rem;margin-right:4px">' + escHtml(t) + '</span>';
      }).join("") +
    '</div>';
  }

  var modelsHtml = "";
  if (impact.affected_models && impact.affected_models.length > 0) {
    modelsHtml = '<div style="margin-top:6px"><strong>Affected Models:</strong> ' +
      impact.affected_models.map(function(m) {
        return '<span style="background:rgba(168,85,247,0.15);color:#c084fc;padding:2px 8px;border-radius:4px;font-size:0.8rem;margin-right:4px">' + escHtml(m) + '</span>';
      }).join("") +
    '</div>';
  }

  document.getElementById("postmortemImpact").innerHTML =
    '<h3>\uD83D\uDCA5 Impact Assessment</h3>' +
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-top:8px">' +
      '<div class="info-card"><div class="info-label">Errors</div><div class="info-value">' + impact.error_count + ' / ' + impact.total_events + '</div></div>' +
      '<div class="info-card"><div class="info-label">Error Rate</div><div class="info-value" style="color:' + (impact.error_rate > 0.25 ? '#ef4444' : impact.error_rate > 0.1 ? '#f97316' : '#eab308') + '">' + errorPct + '</div></div>' +
      '<div class="info-card"><div class="info-label">Downtime</div><div class="info-value">' + downtimeText + '</div></div>' +
      '<div class="info-card"><div class="info-label">Tokens Wasted</div><div class="info-value">' + (impact.tokens_wasted || 0).toLocaleString() + '</div></div>' +
      '<div class="info-card"><div class="info-label">Est. Cost</div><div class="info-value">' + costText + '</div></div>' +
      '<div class="info-card"><div class="info-label">User-Facing</div><div class="info-value">' + (impact.user_facing ? "\u26A0\uFE0F Yes" : "No") + '</div></div>' +
    '</div>' +
    toolsHtml +
    modelsHtml;
}

function renderPostmortemCauses(data) {
  var causes = data.root_causes;
  if (!causes || causes.length === 0) {
    document.getElementById("postmortemCauses").innerHTML =
      '<h3>\uD83D\uDD0D Root Cause Analysis</h3>' +
      '<div style="color:var(--text-muted);padding:12px">No root causes identified.</div>';
    return;
  }

  var html = '<h3>\uD83D\uDD0D Root Cause Analysis</h3>';
  html += '<div style="display:flex;flex-direction:column;gap:10px;margin-top:8px">';

  for (var i = 0; i < causes.length; i++) {
    var cause = causes[i];
    var confidence = Math.round(cause.confidence * 100);
    var barColor = confidence >= 70 ? "#ef4444" : confidence >= 40 ? "#f97316" : "#eab308";
    var categoryLabel = (cause.category || "unknown").replace(/_/g, " ");

    html += '<div style="background:var(--bg-card);border-radius:8px;padding:14px;border-left:4px solid ' + barColor + '">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center">';
    html += '<div style="font-weight:600">' + escHtml(cause.description) + '</div>';
    html += '<div style="display:flex;align-items:center;gap:8px">';
    html += '<span style="background:rgba(107,114,128,0.15);color:var(--text-muted);padding:2px 8px;border-radius:4px;font-size:0.75rem">' + escHtml(categoryLabel) + '</span>';
    html += '<span style="font-weight:600;color:' + barColor + '">' + confidence + '%</span>';
    html += '</div></div>';

    // Confidence bar
    html += '<div style="margin-top:8px;height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden">';
    html += '<div style="height:100%;width:' + confidence + '%;background:' + barColor + ';border-radius:3px"></div>';
    html += '</div>';

    // Evidence
    if (cause.evidence && cause.evidence.length > 0) {
      html += '<div style="margin-top:8px;font-size:0.85rem;color:var(--text-muted)">';
      for (var j = 0; j < cause.evidence.length; j++) {
        html += '<div style="margin-top:2px">\u2022 ' + escHtml(cause.evidence[j]) + '</div>';
      }
      html += '</div>';
    }

    if (cause.affected_events) {
      html += '<div style="margin-top:4px;font-size:0.8rem;color:var(--text-muted)">' + cause.affected_events + ' event(s) affected</div>';
    }

    html += '</div>';
  }

  html += '</div>';
  document.getElementById("postmortemCauses").innerHTML = html;
}

function renderPostmortemTimeline(data) {
  var timeline = data.timeline;
  if (!timeline || timeline.length === 0) {
    document.getElementById("postmortemTimeline").innerHTML =
      '<h3>\uD83D\uDCCB Incident Timeline</h3>' +
      '<div style="color:var(--text-muted);padding:12px">No timeline events.</div>';
    return;
  }

  var html = '<h3>\uD83D\uDCCB Incident Timeline</h3>';
  html += '<div style="position:relative;margin-top:12px;padding-left:24px">';

  // Vertical line
  html += '<div style="position:absolute;left:8px;top:0;bottom:0;width:2px;background:rgba(255,255,255,0.1)"></div>';

  for (var i = 0; i < timeline.length; i++) {
    var evt = timeline[i];
    var dotColor = evt.severity === "error" ? "#ef4444" : evt.severity === "warning" ? "#f97316" : "#22c55e";
    var elapsedText = formatDurationShort(evt.elapsed_ms);
    var timeText = formatTime(evt.timestamp);

    html += '<div style="position:relative;margin-bottom:16px;padding-left:16px">';

    // Dot
    html += '<div style="position:absolute;left:-20px;top:6px;width:12px;height:12px;border-radius:50%;background:' + dotColor + ';border:2px solid var(--bg-surface)"></div>';

    // Content
    html += '<div style="background:var(--bg-card);border-radius:6px;padding:10px 14px">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px">';
    html += '<span style="font-weight:600;color:' + dotColor + '">' + escHtml(evt.event_type) + '</span>';
    html += '<span style="font-size:0.8rem;color:var(--text-muted)">' + elapsedText + ' elapsed \u00B7 ' + timeText + '</span>';
    html += '</div>';
    html += '<div style="margin-top:4px;font-size:0.9rem;color:var(--text-secondary)">' + escHtml(evt.description) + '</div>';
    html += '</div></div>';
  }

  html += '</div>';
  document.getElementById("postmortemTimeline").innerHTML = html;
}

