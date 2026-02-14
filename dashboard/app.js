/* ── AgentLens Dashboard — App Logic ──────────────────────────────────── */

const API_BASE = window.location.origin;

let currentSession = null;

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
      <div class="session-card" onclick="loadSessionDetail('${s.session_id}')">
        <div class="session-card-left">
          <div class="session-agent">${escHtml(s.agent_name)}</div>
          <div class="session-meta">
            <span>🆔 ${s.session_id.slice(0, 8)}…</span>
            <span>🕐 ${formatTime(s.started_at)}</span>
            ${s.metadata?.version ? `<span>📦 v${escHtml(s.metadata.version)}</span>` : ""}
            ${s.metadata?.environment ? `<span>🌍 ${escHtml(s.metadata.environment)}</span>` : ""}
          </div>
        </div>
        <div class="session-card-right">
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

// ── Tab Switching ───────────────────────────────────────────────────

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));

  document.querySelector(`.tab[data-tab="${tabName}"]`).classList.add("active");
  document.getElementById(`${tabName}Tab`).classList.add("active");
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
