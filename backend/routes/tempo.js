/**
 * Agent Operational Tempo Analyzer – autonomous pace/rhythm analysis engine.
 *
 * Measures the cadence of agent operations, detects when agents operate
 * too fast (rushing, cutting corners) or too slow (stuck, looping),
 * identifies optimal tempos per task type, and recommends pace adjustments.
 *
 * 7 analysis engines:
 *   1. Cadence Profiler – inter-event timing distribution per agent
 *   2. Rush Detector – identifies suspiciously fast bursts
 *   3. Stall Detector – identifies stuck/looping patterns
 *   4. Task Tempo Optimizer – optimal pace per task type
 *   5. Rhythm Regularity Scorer – consistency of operational rhythm
 *   6. Tempo Drift Tracker – pace changes over time windows
 *   7. Pace Recommendation Engine – autonomous suggestions
 *
 * Routes:
 *   GET  /tempo                   – fleet-wide tempo overview
 *   GET  /tempo/:agent            – detailed tempo profile for one agent
 *   GET  /tempo/:agent/rhythm     – rhythm regularity timeline
 *   GET  /tempo/:agent/anomalies  – rushing/stalling episodes
 *   POST /tempo/analyze           – on-demand tempo analysis for given sessions
 */

const express = require("express");
const { getDb } = require("../db");
const { parseDays, daysAgoCutoff, wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// ── Statistical Helpers ────────────────────────────────────────────

function median(arr) {
  if (!arr.length) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function mean(arr) {
  if (!arr.length) return 0;
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}

function stddev(arr) {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  return Math.sqrt(arr.reduce((s, v) => s + (v - m) ** 2, 0) / (arr.length - 1));
}

function percentile(arr, p) {
  if (!arr.length) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function coefficientOfVariation(arr) {
  const m = mean(arr);
  if (m === 0) return 0;
  return stddev(arr) / m;
}

// ── Engine 1: Cadence Profiler ─────────────────────────────────────

function buildCadenceProfile(events) {
  if (events.length < 2) return null;

  // Sort events by timestamp
  const sorted = [...events].sort((a, b) =>
    new Date(a.timestamp || a.created_at).getTime() - new Date(b.timestamp || b.created_at).getTime()
  );

  // Compute inter-event intervals (ms)
  const intervals = [];
  for (let i = 1; i < sorted.length; i++) {
    const dt = new Date(sorted[i].timestamp || sorted[i].created_at).getTime() -
               new Date(sorted[i - 1].timestamp || sorted[i - 1].created_at).getTime();
    if (dt >= 0) intervals.push(dt);
  }

  if (!intervals.length) return null;

  return {
    eventCount: events.length,
    intervalCount: intervals.length,
    medianIntervalMs: Math.round(median(intervals)),
    meanIntervalMs: Math.round(mean(intervals)),
    stddevMs: Math.round(stddev(intervals)),
    p10Ms: Math.round(percentile(intervals, 10)),
    p90Ms: Math.round(percentile(intervals, 90)),
    minMs: Math.min(...intervals),
    maxMs: Math.max(...intervals),
    coeffOfVariation: Math.round(coefficientOfVariation(intervals) * 1000) / 1000,
    tempoCategory: categorizeTempoFromIntervals(intervals),
  };
}

function categorizeTempoFromIntervals(intervals) {
  const med = median(intervals);
  if (med < 500) return "hyper-fast";
  if (med < 2000) return "fast";
  if (med < 10000) return "moderate";
  if (med < 30000) return "deliberate";
  if (med < 120000) return "slow";
  return "stalled";
}

// ── Engine 2: Rush Detector ────────────────────────────────────────

function detectRushEpisodes(events, options = {}) {
  const windowSize = options.windowSize || 10;
  const rushThresholdMultiplier = options.rushThreshold || 0.3; // 30% of median

  const sorted = [...events].sort((a, b) =>
    new Date(a.timestamp || a.created_at).getTime() - new Date(b.timestamp || b.created_at).getTime()
  );

  const intervals = [];
  for (let i = 1; i < sorted.length; i++) {
    const dt = new Date(sorted[i].timestamp || sorted[i].created_at).getTime() -
               new Date(sorted[i - 1].timestamp || sorted[i - 1].created_at).getTime();
    intervals.push({ dt, index: i, event: sorted[i] });
  }

  if (intervals.length < windowSize) return [];

  const globalMedian = median(intervals.map(i => i.dt));
  const rushThreshold = globalMedian * rushThresholdMultiplier;

  const episodes = [];
  let currentEpisode = null;

  for (let i = 0; i < intervals.length; i++) {
    if (intervals[i].dt < rushThreshold) {
      if (!currentEpisode) {
        currentEpisode = {
          startIndex: i,
          startTime: sorted[i].timestamp || sorted[i].created_at,
          intervals: [],
          severity: "low",
        };
      }
      currentEpisode.intervals.push(intervals[i].dt);
    } else if (currentEpisode) {
      if (currentEpisode.intervals.length >= 3) {
        currentEpisode.endIndex = i - 1;
        currentEpisode.endTime = sorted[i - 1].timestamp || sorted[i - 1].created_at;
        currentEpisode.durationMs = currentEpisode.intervals.reduce((s, v) => s + v, 0);
        currentEpisode.meanIntervalMs = Math.round(mean(currentEpisode.intervals));
        currentEpisode.severity = classifyRushSeverity(currentEpisode, globalMedian);
        episodes.push(currentEpisode);
      }
      currentEpisode = null;
    }
  }

  // Close trailing episode
  if (currentEpisode && currentEpisode.intervals.length >= 3) {
    currentEpisode.endIndex = intervals.length - 1;
    currentEpisode.endTime = sorted[sorted.length - 1].timestamp || sorted[sorted.length - 1].created_at;
    currentEpisode.durationMs = currentEpisode.intervals.reduce((s, v) => s + v, 0);
    currentEpisode.meanIntervalMs = Math.round(mean(currentEpisode.intervals));
    currentEpisode.severity = classifyRushSeverity(currentEpisode, globalMedian);
    episodes.push(currentEpisode);
  }

  return episodes;
}

function classifyRushSeverity(episode, globalMedian) {
  const ratio = episode.meanIntervalMs / globalMedian;
  if (ratio < 0.05) return "critical";
  if (ratio < 0.1) return "high";
  if (ratio < 0.2) return "medium";
  return "low";
}

// ── Engine 3: Stall Detector ───────────────────────────────────────

function detectStallEpisodes(events, options = {}) {
  const stallThresholdMultiplier = options.stallThreshold || 5; // 5x median

  const sorted = [...events].sort((a, b) =>
    new Date(a.timestamp || a.created_at).getTime() - new Date(b.timestamp || b.created_at).getTime()
  );

  const intervals = [];
  for (let i = 1; i < sorted.length; i++) {
    const dt = new Date(sorted[i].timestamp || sorted[i].created_at).getTime() -
               new Date(sorted[i - 1].timestamp || sorted[i - 1].created_at).getTime();
    intervals.push({ dt, index: i, eventBefore: sorted[i - 1], eventAfter: sorted[i] });
  }

  if (intervals.length < 3) return [];

  const globalMedian = median(intervals.map(i => i.dt));
  const stallThreshold = globalMedian * stallThresholdMultiplier;

  const episodes = [];
  for (const interval of intervals) {
    if (interval.dt >= stallThreshold) {
      episodes.push({
        gapMs: interval.dt,
        gapFormatted: formatDuration(interval.dt),
        beforeEvent: interval.eventBefore.event_type || "unknown",
        afterEvent: interval.eventAfter.event_type || "unknown",
        timestamp: interval.eventBefore.timestamp || interval.eventBefore.created_at,
        severity: classifyStallSeverity(interval.dt, globalMedian),
        possibleCause: inferStallCause(interval),
      });
    }
  }

  return episodes.sort((a, b) => b.gapMs - a.gapMs);
}

function classifyStallSeverity(gapMs, globalMedian) {
  const ratio = gapMs / globalMedian;
  if (ratio > 50) return "critical";
  if (ratio > 20) return "high";
  if (ratio > 10) return "medium";
  return "low";
}

function inferStallCause(interval) {
  const afterType = (interval.eventAfter.event_type || "").toLowerCase();
  const beforeType = (interval.eventBefore.event_type || "").toLowerCase();

  if (afterType.includes("error") || afterType.includes("retry")) return "error-recovery";
  if (beforeType === afterType) return "possible-loop";
  if (interval.dt > 300000) return "session-timeout";
  if (interval.dt > 60000) return "external-dependency";
  return "processing-delay";
}

function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}

// ── Engine 4: Task Tempo Optimizer ─────────────────────────────────

function computeOptimalTempos(sessions, events) {
  // Group events by session, then by task type (inferred from event patterns)
  const taskGroups = {};

  for (const session of sessions) {
    const sessionEvents = events.filter(e => e.session_id === session.id);
    if (sessionEvents.length < 3) continue;

    const taskType = inferTaskType(sessionEvents);
    if (!taskGroups[taskType]) taskGroups[taskType] = [];

    const intervals = computeIntervals(sessionEvents);
    if (intervals.length > 0) {
      taskGroups[taskType].push({
        sessionId: session.id,
        medianInterval: median(intervals),
        success: !session.error_count || session.error_count === 0,
        tokenEfficiency: session.total_tokens > 0 && session.duration_ms > 0
          ? session.total_tokens / (session.duration_ms / 1000)
          : 0,
      });
    }
  }

  const optimalTempos = {};
  for (const [taskType, entries] of Object.entries(taskGroups)) {
    const successful = entries.filter(e => e.success);
    const failed = entries.filter(e => !e.success);

    const successMedians = successful.map(e => e.medianInterval);
    const failMedians = failed.map(e => e.medianInterval);

    optimalTempos[taskType] = {
      sampleSize: entries.length,
      successRate: entries.length > 0 ? Math.round((successful.length / entries.length) * 100) : 0,
      optimalPaceMs: successMedians.length > 0 ? Math.round(median(successMedians)) : null,
      riskyFastMs: successMedians.length > 3 ? Math.round(percentile(successMedians, 10)) : null,
      riskySlowMs: successMedians.length > 3 ? Math.round(percentile(successMedians, 90)) : null,
      failedMeanPaceMs: failMedians.length > 0 ? Math.round(mean(failMedians)) : null,
      recommendation: generateTempoRecommendation(successMedians, failMedians),
    };
  }

  return optimalTempos;
}

function inferTaskType(events) {
  const types = events.map(e => (e.event_type || "").toLowerCase());
  if (types.some(t => t.includes("tool") || t.includes("function"))) return "tool-use";
  if (types.some(t => t.includes("search") || t.includes("retrieval"))) return "research";
  if (types.some(t => t.includes("code") || t.includes("edit"))) return "coding";
  if (types.some(t => t.includes("plan") || t.includes("reason"))) return "planning";
  if (types.some(t => t.includes("chat") || t.includes("response"))) return "conversation";
  return "general";
}

function computeIntervals(events) {
  const sorted = [...events].sort((a, b) =>
    new Date(a.timestamp || a.created_at).getTime() - new Date(b.timestamp || b.created_at).getTime()
  );
  const intervals = [];
  for (let i = 1; i < sorted.length; i++) {
    const dt = new Date(sorted[i].timestamp || sorted[i].created_at).getTime() -
               new Date(sorted[i - 1].timestamp || sorted[i - 1].created_at).getTime();
    if (dt >= 0) intervals.push(dt);
  }
  return intervals;
}

function generateTempoRecommendation(successMedians, failMedians) {
  if (successMedians.length < 3) return "insufficient-data";
  const optPace = median(successMedians);
  if (failMedians.length > 0) {
    const failPace = mean(failMedians);
    if (failPace < optPace * 0.5) return "slow-down";
    if (failPace > optPace * 2) return "speed-up";
  }
  return "maintain-pace";
}

// ── Engine 5: Rhythm Regularity Scorer ─────────────────────────────

function scoreRhythm(events) {
  const intervals = computeIntervals(events);
  if (intervals.length < 5) return null;

  const cv = coefficientOfVariation(intervals);
  const burstRatio = computeBurstRatio(intervals);
  const autocorrelation = computeAutocorrelation(intervals, 1);

  // Composite rhythm score: low CV + high autocorrelation + low burst = regular
  const cvScore = Math.max(0, 100 - cv * 100);
  const autoScore = (autocorrelation + 1) * 50; // normalize -1..1 to 0..100
  const burstScore = Math.max(0, 100 - burstRatio * 200);

  const compositeScore = Math.round(cvScore * 0.4 + autoScore * 0.35 + burstScore * 0.25);

  return {
    score: Math.min(100, Math.max(0, compositeScore)),
    classification: classifyRhythm(compositeScore),
    coeffOfVariation: Math.round(cv * 1000) / 1000,
    autocorrelation: Math.round(autocorrelation * 1000) / 1000,
    burstRatio: Math.round(burstRatio * 1000) / 1000,
    interpretation: interpretRhythm(compositeScore, cv, autocorrelation),
  };
}

function computeBurstRatio(intervals) {
  if (intervals.length < 3) return 0;
  const med = median(intervals);
  const bursts = intervals.filter(i => i < med * 0.2).length;
  return bursts / intervals.length;
}

function computeAutocorrelation(arr, lag) {
  if (arr.length <= lag) return 0;
  const m = mean(arr);
  let num = 0, den = 0;
  for (let i = 0; i < arr.length - lag; i++) {
    num += (arr[i] - m) * (arr[i + lag] - m);
  }
  for (let i = 0; i < arr.length; i++) {
    den += (arr[i] - m) ** 2;
  }
  return den === 0 ? 0 : num / den;
}

function classifyRhythm(score) {
  if (score >= 80) return "metronome";
  if (score >= 60) return "steady";
  if (score >= 40) return "variable";
  if (score >= 20) return "erratic";
  return "chaotic";
}

function interpretRhythm(score, cv, autocorrelation) {
  if (score >= 80) return "Agent operates with very consistent timing — machine-like regularity";
  if (score >= 60) return "Agent maintains a reasonably steady operational rhythm";
  if (score >= 40) return "Agent shows variable pacing — may adapt tempo to task difficulty";
  if (score >= 20) return "Agent rhythm is erratic — possible context-switching or unclear strategy";
  return "Agent pacing is chaotic — may indicate confusion, thrashing, or external disruptions";
}

// ── Engine 6: Tempo Drift Tracker ──────────────────────────────────

function trackTempoDrift(events, windowDays = 1) {
  const sorted = [...events].sort((a, b) =>
    new Date(a.timestamp || a.created_at).getTime() - new Date(b.timestamp || b.created_at).getTime()
  );

  if (sorted.length < 10) return [];

  const windowMs = windowDays * 24 * 3600 * 1000;
  const startTime = new Date(sorted[0].timestamp || sorted[0].created_at).getTime();
  const endTime = new Date(sorted[sorted.length - 1].timestamp || sorted[sorted.length - 1].created_at).getTime();

  const windows = [];
  let windowStart = startTime;

  while (windowStart < endTime) {
    const windowEnd = windowStart + windowMs;
    const windowEvents = sorted.filter(e => {
      const t = new Date(e.timestamp || e.created_at).getTime();
      return t >= windowStart && t < windowEnd;
    });

    if (windowEvents.length >= 3) {
      const intervals = computeIntervals(windowEvents);
      windows.push({
        windowStart: new Date(windowStart).toISOString(),
        windowEnd: new Date(windowEnd).toISOString(),
        eventCount: windowEvents.length,
        medianPaceMs: Math.round(median(intervals)),
        meanPaceMs: Math.round(mean(intervals)),
        rhythm: scoreRhythm(windowEvents),
      });
    }

    windowStart = windowEnd;
  }

  // Compute drift between consecutive windows
  for (let i = 1; i < windows.length; i++) {
    const prev = windows[i - 1].medianPaceMs;
    const curr = windows[i].medianPaceMs;
    if (prev > 0) {
      windows[i].driftPct = Math.round(((curr - prev) / prev) * 100);
      windows[i].driftDirection = curr > prev ? "slowing" : curr < prev ? "accelerating" : "stable";
    }
  }

  return windows;
}

// ── Engine 7: Pace Recommendation Engine ───────────────────────────

function generateRecommendations(cadence, rushEpisodes, stallEpisodes, rhythm, optimalTempos) {
  const recommendations = [];

  if (rushEpisodes.length > 2) {
    const criticalRushes = rushEpisodes.filter(e => e.severity === "critical" || e.severity === "high");
    if (criticalRushes.length > 0) {
      recommendations.push({
        type: "pace-warning",
        priority: "high",
        message: `Agent has ${criticalRushes.length} severe rushing episodes — may be skipping verification steps`,
        action: "Introduce mandatory pause/check between rapid-fire operations",
        confidence: 0.85,
      });
    }
  }

  if (stallEpisodes.length > 3) {
    const loopStalls = stallEpisodes.filter(e => e.possibleCause === "possible-loop");
    if (loopStalls.length > 0) {
      recommendations.push({
        type: "loop-detection",
        priority: "high",
        message: `Detected ${loopStalls.length} possible loop-induced stalls`,
        action: "Add loop breakers or escalation after N retries",
        confidence: 0.8,
      });
    }
  }

  if (rhythm && rhythm.score < 30) {
    recommendations.push({
      type: "rhythm-coaching",
      priority: "medium",
      message: "Operational rhythm is chaotic — high variance in inter-step timing",
      action: "Consider structured step sequencing or task batching",
      confidence: 0.7,
    });
  }

  if (cadence && cadence.coeffOfVariation > 2) {
    recommendations.push({
      type: "consistency",
      priority: "medium",
      message: "Extremely high tempo variability (CV > 2.0) — agent may be context-switching excessively",
      action: "Reduce concurrent task load or improve focus mechanisms",
      confidence: 0.75,
    });
  }

  // Optimal tempo recommendations
  for (const [taskType, tempo] of Object.entries(optimalTempos)) {
    if (tempo.recommendation === "slow-down") {
      recommendations.push({
        type: "tempo-optimization",
        priority: "medium",
        taskType,
        message: `Tasks of type "${taskType}" fail more when rushed — optimal pace is ${formatDuration(tempo.optimalPaceMs)} between steps`,
        action: `Enforce minimum ${formatDuration(tempo.riskyFastMs)} inter-step delay for ${taskType} tasks`,
        confidence: 0.7,
      });
    }
  }

  if (recommendations.length === 0) {
    recommendations.push({
      type: "all-clear",
      priority: "low",
      message: "Operational tempo appears healthy — no pace issues detected",
      action: "Continue current operational patterns",
      confidence: 0.9,
    });
  }

  return recommendations.sort((a, b) => {
    const pOrder = { high: 0, medium: 1, low: 2 };
    return (pOrder[a.priority] || 2) - (pOrder[b.priority] || 2);
  });
}

// ── Composite Tempo Score ──────────────────────────────────────────

function computeTempoScore(cadence, rushEpisodes, stallEpisodes, rhythm) {
  let score = 100;

  // Penalize rushing
  for (const ep of rushEpisodes) {
    const penalties = { critical: 15, high: 10, medium: 5, low: 2 };
    score -= penalties[ep.severity] || 2;
  }

  // Penalize stalling
  for (const ep of stallEpisodes) {
    const penalties = { critical: 12, high: 8, medium: 4, low: 2 };
    score -= penalties[ep.severity] || 2;
  }

  // Bonus/penalty for rhythm
  if (rhythm) {
    if (rhythm.score >= 70) score += 5;
    else if (rhythm.score < 30) score -= 10;
  }

  // Penalize extreme variability
  if (cadence && cadence.coeffOfVariation > 2) score -= 10;

  return Math.min(100, Math.max(0, Math.round(score)));
}

function classifyTempoHealth(score) {
  if (score >= 85) return "excellent";
  if (score >= 70) return "good";
  if (score >= 50) return "fair";
  if (score >= 30) return "poor";
  return "critical";
}

// ── Route: Fleet-wide tempo overview ───────────────────────────────

router.get("/", wrapRoute(async (req, res) => {
  const db = getDb();
  const days = parseDays(req.query.days, 7);
  const cutoff = daysAgoCutoff(days);

  const sessions = db.prepare(`
    SELECT id, agent, duration_ms, total_tokens, error_count, created_at
    FROM sessions WHERE created_at >= ?
  `).all(cutoff);

  const events = db.prepare(`
    SELECT id, session_id, event_type, timestamp, created_at
    FROM events WHERE created_at >= ?
    ORDER BY created_at ASC
  `).all(cutoff);

  // Group by agent
  const agentMap = {};
  for (const s of sessions) {
    const agent = s.agent || "unknown";
    if (!agentMap[agent]) agentMap[agent] = { sessions: [], events: [] };
    agentMap[agent].sessions.push(s);
  }
  for (const e of events) {
    const session = sessions.find(s => s.id === e.session_id);
    if (session) {
      const agent = session.agent || "unknown";
      if (agentMap[agent]) agentMap[agent].events.push(e);
    }
  }

  const fleet = [];
  for (const [agent, data] of Object.entries(agentMap)) {
    const cadence = buildCadenceProfile(data.events);
    const rushEpisodes = detectRushEpisodes(data.events);
    const stallEpisodes = detectStallEpisodes(data.events);
    const rhythm = scoreRhythm(data.events);

    const tempoScore = computeTempoScore(cadence, rushEpisodes, stallEpisodes, rhythm);

    fleet.push({
      agent,
      sessionCount: data.sessions.length,
      eventCount: data.events.length,
      tempoScore,
      tempoHealth: classifyTempoHealth(tempoScore),
      cadenceCategory: cadence ? cadence.tempoCategory : "unknown",
      rhythmScore: rhythm ? rhythm.score : null,
      rhythmClass: rhythm ? rhythm.classification : "unknown",
      rushEpisodeCount: rushEpisodes.length,
      stallEpisodeCount: stallEpisodes.length,
    });
  }

  fleet.sort((a, b) => a.tempoScore - b.tempoScore);

  const avgScore = fleet.length > 0 ? Math.round(mean(fleet.map(f => f.tempoScore))) : 0;

  res.json({
    period: { days, cutoff },
    fleetTempoScore: avgScore,
    fleetTempoHealth: classifyTempoHealth(avgScore),
    agentCount: fleet.length,
    agents: fleet,
    summary: {
      rushing: fleet.filter(f => f.rushEpisodeCount > 0).length,
      stalling: fleet.filter(f => f.stallEpisodeCount > 0).length,
      chaotic: fleet.filter(f => f.rhythmClass === "chaotic" || f.rhythmClass === "erratic").length,
      healthy: fleet.filter(f => f.tempoHealth === "excellent" || f.tempoHealth === "good").length,
    },
  });
}));

// ── Route: Detailed tempo profile for one agent ────────────────────

router.get("/:agent", wrapRoute(async (req, res) => {
  const db = getDb();
  const { agent } = req.params;
  const days = parseDays(req.query.days, 14);
  const cutoff = daysAgoCutoff(days);

  const sessions = db.prepare(`
    SELECT id, agent, duration_ms, total_tokens, error_count, created_at
    FROM sessions WHERE agent = ? AND created_at >= ?
  `).all(agent, cutoff);

  if (!sessions.length) {
    return res.status(404).json({ error: "Agent not found or no sessions in period" });
  }

  const sessionIds = sessions.map(s => s.id);
  const placeholders = sessionIds.map(() => "?").join(",");
  const events = db.prepare(`
    SELECT id, session_id, event_type, timestamp, created_at
    FROM events WHERE session_id IN (${placeholders})
    ORDER BY created_at ASC
  `).all(...sessionIds);

  const cadence = buildCadenceProfile(events);
  const rushEpisodes = detectRushEpisodes(events);
  const stallEpisodes = detectStallEpisodes(events);
  const rhythm = scoreRhythm(events);
  const optimalTempos = computeOptimalTempos(sessions, events);
  const tempoScore = computeTempoScore(cadence, rushEpisodes, stallEpisodes, rhythm);
  const recommendations = generateRecommendations(cadence, rushEpisodes, stallEpisodes, rhythm, optimalTempos);

  res.json({
    agent,
    period: { days, cutoff },
    tempoScore,
    tempoHealth: classifyTempoHealth(tempoScore),
    cadence,
    rhythm,
    optimalTempos,
    rushEpisodes: rushEpisodes.slice(0, 20),
    stallEpisodes: stallEpisodes.slice(0, 20),
    recommendations,
    summary: {
      totalEvents: events.length,
      totalSessions: sessions.length,
      rushCount: rushEpisodes.length,
      stallCount: stallEpisodes.length,
    },
  });
}));

// ── Route: Rhythm regularity timeline ──────────────────────────────

router.get("/:agent/rhythm", wrapRoute(async (req, res) => {
  const db = getDb();
  const { agent } = req.params;
  const days = parseDays(req.query.days, 14);
  const windowDays = parseFloat(req.query.window) || 1;
  const cutoff = daysAgoCutoff(days);

  const sessions = db.prepare(`
    SELECT id FROM sessions WHERE agent = ? AND created_at >= ?
  `).all(agent, cutoff);

  if (!sessions.length) {
    return res.status(404).json({ error: "Agent not found or no sessions in period" });
  }

  const sessionIds = sessions.map(s => s.id);
  const placeholders = sessionIds.map(() => "?").join(",");
  const events = db.prepare(`
    SELECT id, session_id, event_type, timestamp, created_at
    FROM events WHERE session_id IN (${placeholders})
    ORDER BY created_at ASC
  `).all(...sessionIds);

  const driftTimeline = trackTempoDrift(events, windowDays);

  res.json({
    agent,
    period: { days, cutoff, windowDays },
    timeline: driftTimeline,
    overallTrend: computeOverallTrend(driftTimeline),
  });
}));

function computeOverallTrend(timeline) {
  if (timeline.length < 2) return "insufficient-data";
  const drifts = timeline.filter(w => w.driftPct != null).map(w => w.driftPct);
  if (!drifts.length) return "stable";
  const avgDrift = mean(drifts);
  if (avgDrift > 20) return "decelerating";
  if (avgDrift < -20) return "accelerating";
  return "stable";
}

// ── Route: Rushing/stalling anomalies ──────────────────────────────

router.get("/:agent/anomalies", wrapRoute(async (req, res) => {
  const db = getDb();
  const { agent } = req.params;
  const days = parseDays(req.query.days, 14);
  const cutoff = daysAgoCutoff(days);

  const sessions = db.prepare(`
    SELECT id FROM sessions WHERE agent = ? AND created_at >= ?
  `).all(agent, cutoff);

  if (!sessions.length) {
    return res.status(404).json({ error: "Agent not found or no sessions in period" });
  }

  const sessionIds = sessions.map(s => s.id);
  const placeholders = sessionIds.map(() => "?").join(",");
  const events = db.prepare(`
    SELECT id, session_id, event_type, timestamp, created_at
    FROM events WHERE session_id IN (${placeholders})
    ORDER BY created_at ASC
  `).all(...sessionIds);

  const rushEpisodes = detectRushEpisodes(events);
  const stallEpisodes = detectStallEpisodes(events);

  res.json({
    agent,
    period: { days, cutoff },
    anomalies: {
      rushing: {
        count: rushEpisodes.length,
        episodes: rushEpisodes,
        bySeverity: {
          critical: rushEpisodes.filter(e => e.severity === "critical").length,
          high: rushEpisodes.filter(e => e.severity === "high").length,
          medium: rushEpisodes.filter(e => e.severity === "medium").length,
          low: rushEpisodes.filter(e => e.severity === "low").length,
        },
      },
      stalling: {
        count: stallEpisodes.length,
        episodes: stallEpisodes,
        bySeverity: {
          critical: stallEpisodes.filter(e => e.severity === "critical").length,
          high: stallEpisodes.filter(e => e.severity === "high").length,
          medium: stallEpisodes.filter(e => e.severity === "medium").length,
          low: stallEpisodes.filter(e => e.severity === "low").length,
        },
        byCause: stallEpisodes.reduce((acc, e) => {
          acc[e.possibleCause] = (acc[e.possibleCause] || 0) + 1;
          return acc;
        }, {}),
      },
    },
  });
}));

// ── Route: On-demand analysis ──────────────────────────────────────

router.post("/analyze", wrapRoute(async (req, res) => {
  const { events: inputEvents } = req.body;

  if (!inputEvents || !Array.isArray(inputEvents) || inputEvents.length < 3) {
    return res.status(400).json({ error: "Provide at least 3 events with timestamp fields" });
  }

  const cadence = buildCadenceProfile(inputEvents);
  const rushEpisodes = detectRushEpisodes(inputEvents);
  const stallEpisodes = detectStallEpisodes(inputEvents);
  const rhythm = scoreRhythm(inputEvents);
  const tempoScore = computeTempoScore(cadence, rushEpisodes, stallEpisodes, rhythm);
  const recommendations = generateRecommendations(cadence, rushEpisodes, stallEpisodes, rhythm, {});

  res.json({
    tempoScore,
    tempoHealth: classifyTempoHealth(tempoScore),
    cadence,
    rhythm,
    rushEpisodes,
    stallEpisodes,
    recommendations,
  });
}));

module.exports = router;
