/**
 * Service Dependency Map — analyzes tool_call events to build a dependency
 * graph of external services/tools that agents use, with per-service
 * reliability, latency, and critical-path analysis.
 *
 * Exposed via routes/dependencies.js
 */

"use strict";

const { latencyStats, round2 } = require("./stats");

/**
 * Extract the service name from a tool_call value.
 * tool_call can be a JSON string like {"name":"web_search","args":{...}}
 * or just a plain string like "web_search".
 *
 * @param {string|null} toolCall
 * @returns {string|null}
 */
function extractServiceName(toolCall) {
  if (!toolCall || typeof toolCall !== "string") return null;
  const trimmed = toolCall.trim();
  if (!trimmed) return null;

  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed);
      return parsed.name || parsed.tool || parsed.function || parsed.service || null;
    } catch {
      return null;
    }
  }

  // Plain string — treat it as the service name directly
  return trimmed || null;
}

/**
 * Determine if an event represents a failure.
 * Checks event_type, output_data, and duration for error signals.
 *
 * @param {object} event
 * @returns {boolean}
 */
function isFailure(event) {
  if (event.event_type === "error" || event.event_type === "tool_error") {
    return true;
  }
  if (event.output_data) {
    const out =
      typeof event.output_data === "string"
        ? event.output_data.toLowerCase()
        : "";
    if (
      out.includes('"error"') ||
      out.includes('"status":"fail') ||
      out.includes('"success":false')
    ) {
      return true;
    }
  }
  return false;
}

/**
 * Build a service dependency map from a list of events.
 *
 * @param {Array<object>} events — Event rows with tool_call, event_type,
 *   duration_ms, tokens_in, tokens_out, session_id, agent_name, timestamp.
 * @returns {object} — Dependency map keyed by service name.
 */
function buildDependencyMap(events) {
  const services = Object.create(null);

  for (const event of events) {
    const name = extractServiceName(event.tool_call);
    if (!name) continue;

    if (!services[name]) {
      services[name] = {
        service: name,
        callCount: 0,
        errorCount: 0,
        totalDurationMs: 0,
        durations: [],
        totalTokensIn: 0,
        totalTokensOut: 0,
        agents: new Set(),
        sessions: new Set(),
        firstSeen: event.timestamp,
        lastSeen: event.timestamp,
      };
    }

    const svc = services[name];
    svc.callCount++;
    if (isFailure(event)) svc.errorCount++;

    const dur = parseFloat(event.duration_ms) || 0;
    svc.totalDurationMs += dur;
    if (dur > 0) svc.durations.push(dur);

    svc.totalTokensIn += parseInt(event.tokens_in) || 0;
    svc.totalTokensOut += parseInt(event.tokens_out) || 0;

    if (event.agent_name) svc.agents.add(event.agent_name);
    if (event.session_id) svc.sessions.add(event.session_id);

    if (event.timestamp < svc.firstSeen) svc.firstSeen = event.timestamp;
    if (event.timestamp > svc.lastSeen) svc.lastSeen = event.timestamp;
  }

  return services;
}

/**
 * Compute final statistics for each service in the dependency map.
 *
 * @param {object} rawMap — Output of buildDependencyMap()
 * @returns {Array<object>} — Array of service stats, sorted by call count desc.
 */
function computeServiceStats(rawMap) {
  const results = [];

  for (const name of Object.keys(rawMap)) {
    const svc = rawMap[name];
    // Sort in-place — the raw array is not needed in original order
    // after this point, saving an O(n) allocation from .slice().
    svc.durations.sort((a, b) => a - b);
    // Pass precomputed totalDurationMs to avoid redundant O(n) reduce
    // inside latencyStats.
    const latency = latencyStats(svc.durations, svc.totalDurationMs);
    const errorRate =
      svc.callCount > 0 ? round2((svc.errorCount / svc.callCount) * 100) : 0;

    results.push({
      service: svc.service,
      callCount: svc.callCount,
      errorCount: svc.errorCount,
      errorRate,
      reliability: round2(100 - errorRate),
      latency,
      totalTokensIn: svc.totalTokensIn,
      totalTokensOut: svc.totalTokensOut,
      totalTokens: svc.totalTokensIn + svc.totalTokensOut,
      uniqueAgents: svc.agents.size,
      uniqueSessions: svc.sessions.size,
      firstSeen: svc.firstSeen,
      lastSeen: svc.lastSeen,
    });
  }

  results.sort((a, b) => b.callCount - a.callCount);
  return results;
}

/**
 * Identify critical dependencies — services with high call volume
 * or high error rates that could impact overall system reliability.
 *
 * A service is critical if:
 *  - It accounts for >= criticalSharePct% of total calls, OR
 *  - Its error rate >= errorThresholdPct%, OR
 *  - Its p95 latency >= latencyThresholdMs
 *
 * @param {Array<object>} serviceStats — Output of computeServiceStats()
 * @param {object} [opts]
 * @param {number} [opts.criticalSharePct=20]
 * @param {number} [opts.errorThresholdPct=10]
 * @param {number} [opts.latencyThresholdMs=5000]
 * @returns {Array<object>}
 */
function identifyCriticalDependencies(serviceStats, opts = {}) {
  const criticalSharePct = opts.criticalSharePct ?? 20;
  const errorThresholdPct = opts.errorThresholdPct ?? 10;
  const latencyThresholdMs = opts.latencyThresholdMs ?? 5000;

  const totalCalls = serviceStats.reduce((s, d) => s + d.callCount, 0);
  if (totalCalls === 0) return [];

  const critical = [];

  for (const svc of serviceStats) {
    const reasons = [];
    const sharePct = round2((svc.callCount / totalCalls) * 100);

    if (sharePct >= criticalSharePct) {
      reasons.push(`high_volume (${sharePct}% of calls)`);
    }
    if (svc.errorRate >= errorThresholdPct) {
      reasons.push(`high_error_rate (${svc.errorRate}%)`);
    }
    if (svc.latency && svc.latency.p95 >= latencyThresholdMs) {
      reasons.push(`high_latency (p95=${svc.latency.p95}ms)`);
    }

    if (reasons.length > 0) {
      critical.push({
        ...svc,
        callSharePct: sharePct,
        criticalReasons: reasons,
      });
    }
  }

  critical.sort((a, b) => b.callCount - a.callCount);
  return critical;
}

/**
 * Build per-agent dependency profiles — which services each agent uses.
 *
 * @param {Array<object>} events
 * @returns {Object<string, Array<{service: string, callCount: number, errorCount: number}>>}
 */
function agentDependencyProfiles(events) {
  const profiles = Object.create(null);

  for (const event of events) {
    const name = extractServiceName(event.tool_call);
    if (!name || !event.agent_name) continue;

    if (!profiles[event.agent_name]) {
      profiles[event.agent_name] = Object.create(null);
    }
    const agentMap = profiles[event.agent_name];
    if (!agentMap[name]) {
      agentMap[name] = { service: name, callCount: 0, errorCount: 0 };
    }
    agentMap[name].callCount++;
    if (isFailure(event)) agentMap[name].errorCount++;
  }

  // Convert to sorted arrays
  const result = Object.create(null);
  for (const agent of Object.keys(profiles)) {
    result[agent] = Object.values(profiles[agent]).sort(
      (a, b) => b.callCount - a.callCount
    );
  }
  return result;
}

/**
 * Detect co-occurring service calls within the same session to identify
 * common service call chains/patterns.
 *
 * @param {Array<object>} events
 * @param {number} [minCoOccurrence=2]
 * @returns {Array<{services: [string, string], coOccurrenceCount: number, sessionCount: number}>}
 */
function detectServiceCoOccurrence(events, minCoOccurrence = 2) {
  // Group services by session
  const sessionServices = Object.create(null);
  for (const event of events) {
    const name = extractServiceName(event.tool_call);
    if (!name || !event.session_id) continue;
    if (!sessionServices[event.session_id]) {
      sessionServices[event.session_id] = new Set();
    }
    sessionServices[event.session_id].add(name);
  }

  // Count pairwise co-occurrences
  const pairs = Object.create(null);
  for (const sid of Object.keys(sessionServices)) {
    const svcs = Array.from(sessionServices[sid]).sort();
    for (let i = 0; i < svcs.length; i++) {
      for (let j = i + 1; j < svcs.length; j++) {
        const key = svcs[i] + "||" + svcs[j];
        if (!pairs[key]) {
          pairs[key] = { services: [svcs[i], svcs[j]], count: 0 };
        }
        pairs[key].count++;
      }
    }
  }

  return Object.values(pairs)
    .filter((p) => p.count >= minCoOccurrence)
    .map((p) => ({
      services: p.services,
      coOccurrenceCount: p.count,
    }))
    .sort((a, b) => b.coOccurrenceCount - a.coOccurrenceCount);
}

/**
 * Compute a per-service trend over time (calls and error rate per period).
 *
 * @param {Array<object>} events
 * @param {string} service — Service name to trend.
 * @param {string} [granularity="day"] — "hour" | "day" | "week"
 * @returns {Array<{period: string, calls: number, errors: number, errorRate: number}>}
 */
function serviceTrend(events, service, granularity = "day") {
  const buckets = Object.create(null);

  for (const event of events) {
    const name = extractServiceName(event.tool_call);
    if (name !== service) continue;

    let period;
    const ts = event.timestamp || "";
    if (granularity === "hour") {
      period = ts.slice(0, 13); // "2026-03-04T14"
    } else if (granularity === "week") {
      const d = new Date(ts);
      const dayOfWeek = d.getUTCDay();
      const weekStart = new Date(d);
      weekStart.setUTCDate(d.getUTCDate() - dayOfWeek);
      period = weekStart.toISOString().slice(0, 10);
    } else {
      period = ts.slice(0, 10); // "2026-03-04"
    }

    if (!period) continue;
    if (!buckets[period]) {
      buckets[period] = { period, calls: 0, errors: 0 };
    }
    buckets[period].calls++;
    if (isFailure(event)) buckets[period].errors++;
  }

  return Object.values(buckets)
    .sort((a, b) => (a.period < b.period ? -1 : 1))
    .map((b) => ({
      ...b,
      errorRate: b.calls > 0 ? round2((b.errors / b.calls) * 100) : 0,
    }));
}

module.exports = {
  extractServiceName,
  isFailure,
  buildDependencyMap,
  computeServiceStats,
  identifyCriticalDependencies,
  agentDependencyProfiles,
  detectServiceCoOccurrence,
  serviceTrend,
};
