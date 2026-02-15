const express = require("express");
const { getDb } = require("../db");
const { safeJsonParse } = require("../lib/validation");

const router = express.Router();

// GET /analytics — Aggregate statistics across all sessions
router.get("/", (req, res) => {
  const db = getDb();

  try {
    // ── Overall session stats ──────────────────────────────────────
    const sessionStats = db
      .prepare(
        `SELECT
          COUNT(*) as total_sessions,
          SUM(total_tokens_in) as total_tokens_in,
          SUM(total_tokens_out) as total_tokens_out,
          SUM(total_tokens_in + total_tokens_out) as total_tokens,
          AVG(total_tokens_in + total_tokens_out) as avg_tokens_per_session,
          SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_sessions,
          SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_sessions,
          MIN(started_at) as earliest_session,
          MAX(started_at) as latest_session
        FROM sessions`
      )
      .get();

    // ── Top agents by token usage ──────────────────────────────────
    const topAgents = db
      .prepare(
        `SELECT
          agent_name,
          COUNT(*) as session_count,
          SUM(total_tokens_in + total_tokens_out) as total_tokens,
          AVG(total_tokens_in + total_tokens_out) as avg_tokens
        FROM sessions
        GROUP BY agent_name
        ORDER BY total_tokens DESC
        LIMIT 10`
      )
      .all();

    // ── Model usage distribution ───────────────────────────────────
    const modelUsage = db
      .prepare(
        `SELECT
          model,
          COUNT(*) as call_count,
          SUM(tokens_in) as total_tokens_in,
          SUM(tokens_out) as total_tokens_out,
          SUM(tokens_in + tokens_out) as total_tokens,
          AVG(duration_ms) as avg_duration_ms
        FROM events
        WHERE model IS NOT NULL AND model != ''
        GROUP BY model
        ORDER BY total_tokens DESC`
      )
      .all();

    // ── Event type distribution ────────────────────────────────────
    const eventTypes = db
      .prepare(
        `SELECT
          event_type,
          COUNT(*) as count
        FROM events
        WHERE event_type NOT IN ('session_start', 'session_end')
        GROUP BY event_type
        ORDER BY count DESC`
      )
      .all();

    // ── Sessions over time (daily buckets) ─────────────────────────
    const sessionsOverTime = db
      .prepare(
        `SELECT
          DATE(started_at) as day,
          COUNT(*) as session_count,
          SUM(total_tokens_in + total_tokens_out) as total_tokens
        FROM sessions
        GROUP BY DATE(started_at)
        ORDER BY day ASC
        LIMIT 90`
      )
      .all();

    // ── Hourly activity heatmap data ───────────────────────────────
    const hourlyActivity = db
      .prepare(
        `SELECT
          CAST(strftime('%H', timestamp) AS INTEGER) as hour,
          COUNT(*) as event_count
        FROM events
        GROUP BY hour
        ORDER BY hour ASC`
      )
      .all();

    // ── Average session duration ───────────────────────────────────
    const durationStats = db
      .prepare(
        `SELECT
          AVG(
            CASE WHEN ended_at IS NOT NULL
              THEN (julianday(ended_at) - julianday(started_at)) * 86400000
              ELSE NULL
            END
          ) as avg_duration_ms,
          MIN(
            CASE WHEN ended_at IS NOT NULL
              THEN (julianday(ended_at) - julianday(started_at)) * 86400000
              ELSE NULL
            END
          ) as min_duration_ms,
          MAX(
            CASE WHEN ended_at IS NOT NULL
              THEN (julianday(ended_at) - julianday(started_at)) * 86400000
              ELSE NULL
            END
          ) as max_duration_ms
        FROM sessions
        WHERE ended_at IS NOT NULL`
      )
      .get();

    // ── Total events ───────────────────────────────────────────────
    const eventCount = db
      .prepare(`SELECT COUNT(*) as total FROM events`)
      .get();

    // ── Error rate ─────────────────────────────────────────────────
    const errorRate =
      sessionStats.total_sessions > 0
        ? Math.round(
            (sessionStats.error_sessions / sessionStats.total_sessions) * 10000
          ) / 100
        : 0;

    res.json({
      overview: {
        total_sessions: sessionStats.total_sessions || 0,
        active_sessions: sessionStats.active_sessions || 0,
        completed_sessions: sessionStats.completed_sessions || 0,
        error_sessions: sessionStats.error_sessions || 0,
        error_rate: errorRate,
        total_events: eventCount.total || 0,
        total_tokens: sessionStats.total_tokens || 0,
        total_tokens_in: sessionStats.total_tokens_in || 0,
        total_tokens_out: sessionStats.total_tokens_out || 0,
        avg_tokens_per_session: Math.round(
          sessionStats.avg_tokens_per_session || 0
        ),
        earliest_session: sessionStats.earliest_session,
        latest_session: sessionStats.latest_session,
      },
      duration: {
        avg_ms: Math.round(durationStats.avg_duration_ms || 0),
        min_ms: Math.round(durationStats.min_duration_ms || 0),
        max_ms: Math.round(durationStats.max_duration_ms || 0),
      },
      top_agents: topAgents.map((a) => ({
        agent_name: a.agent_name,
        session_count: a.session_count,
        total_tokens: a.total_tokens || 0,
        avg_tokens: Math.round(a.avg_tokens || 0),
      })),
      model_usage: modelUsage.map((m) => ({
        model: m.model,
        call_count: m.call_count,
        total_tokens: m.total_tokens || 0,
        total_tokens_in: m.total_tokens_in || 0,
        total_tokens_out: m.total_tokens_out || 0,
        avg_duration_ms: Math.round((m.avg_duration_ms || 0) * 100) / 100,
      })),
      event_types: eventTypes,
      sessions_over_time: sessionsOverTime,
      hourly_activity: hourlyActivity,
    });
  } catch (err) {
    console.error("Error fetching analytics:", err);
    res.status(500).json({ error: "Failed to fetch analytics" });
  }
});

module.exports = router;
