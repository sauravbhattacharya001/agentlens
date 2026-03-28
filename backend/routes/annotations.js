/* ── Session Annotations — notes and comments on sessions ──────────── */

const express = require("express");
const crypto = require("crypto");
const router = express.Router();
const { getDb } = require("../db");
const { parsePagination, wrapRoute } = require("../lib/request-helpers");

// ── Schema initialisation ───────────────────────────────────────────

let _annotationsReady = false;
function ensureAnnotationsTable() {
  if (_annotationsReady) return;
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS annotations (
      annotation_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      text TEXT NOT NULL,
      author TEXT NOT NULL DEFAULT 'system',
      event_id TEXT DEFAULT NULL,
      annotation_type TEXT NOT NULL DEFAULT 'note'
        CHECK(annotation_type IN ('note','bug','insight','warning','milestone')),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_annotations_session
      ON annotations(session_id);
    CREATE INDEX IF NOT EXISTS idx_annotations_type
      ON annotations(annotation_type);
    CREATE INDEX IF NOT EXISTS idx_annotations_created
      ON annotations(created_at);
  `);
  _annotationsReady = true;
}

const VALID_TYPES = ["note", "bug", "insight", "warning", "milestone"];
const MAX_TEXT_LENGTH = 4000;
const MAX_AUTHOR_LENGTH = 100;

// ── Helper: generate unique ID ──────────────────────────────────────

function generateId() {
  return `ann-${Date.now().toString(36)}-${crypto.randomBytes(6).toString('hex')}`;
}

// ── Helper: validate annotation input ───────────────────────────────

function validateAnnotation(body) {
  const errors = [];

  if (!body.text || typeof body.text !== "string" || !body.text.trim()) {
    errors.push("text is required and must be a non-empty string");
  } else if (body.text.length > MAX_TEXT_LENGTH) {
    errors.push(`text must be at most ${MAX_TEXT_LENGTH} characters`);
  }

  if (body.author !== undefined) {
    if (typeof body.author !== "string" || !body.author.trim()) {
      errors.push("author must be a non-empty string");
    } else if (body.author.length > MAX_AUTHOR_LENGTH) {
      errors.push(`author must be at most ${MAX_AUTHOR_LENGTH} characters`);
    }
  }

  if (body.type !== undefined && !VALID_TYPES.includes(body.type)) {
    errors.push(`type must be one of: ${VALID_TYPES.join(", ")}`);
  }

  return errors;
}

// ── POST /sessions/:id/annotations — add annotation ─────────────────

router.post("/:id/annotations", wrapRoute("create annotation", (req, res) => {
  ensureAnnotationsTable();
  const db = getDb();
  const sessionId = req.params.id;

  // Verify session exists
  const session = db
    .prepare("SELECT session_id FROM sessions WHERE session_id = ?")
    .get(sessionId);
  if (!session) {
    return res.status(404).json({ error: "Session not found" });
  }

  const errors = validateAnnotation(req.body);
  if (errors.length > 0) {
    return res.status(400).json({ error: "Validation failed", details: errors });
  }

  const {
    text,
    author = "system",
    type: annotationType = "note",
    event_id: eventId = null,
  } = req.body;

  // Verify event_id belongs to this session if provided
  if (eventId) {
    const event = db
      .prepare(
        "SELECT event_id FROM events WHERE event_id = ? AND session_id = ?"
      )
      .get(eventId, sessionId);
    if (!event) {
      return res.status(400).json({
        error: "event_id not found in this session",
      });
    }
  }

  const now = new Date().toISOString();
  const annotationId = generateId();

  db.prepare(`
    INSERT INTO annotations
      (annotation_id, session_id, text, author, event_id, annotation_type, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(annotationId, sessionId, text.trim(), author.trim(), eventId, annotationType, now, now);

  const annotation = db
    .prepare("SELECT * FROM annotations WHERE annotation_id = ?")
    .get(annotationId);

  res.status(201).json({
    annotation_id: annotation.annotation_id,
    session_id: annotation.session_id,
    text: annotation.text,
    author: annotation.author,
    event_id: annotation.event_id,
    type: annotation.annotation_type,
    created_at: annotation.created_at,
    updated_at: annotation.updated_at,
  });
}));

// ── GET /sessions/:id/annotations — list annotations ────────────────

router.get("/:id/annotations", wrapRoute("fetch annotations", (req, res) => {
  ensureAnnotationsTable();
  const db = getDb();
  const sessionId = req.params.id;

  // Verify session exists
  const session = db
    .prepare("SELECT session_id FROM sessions WHERE session_id = ?")
    .get(sessionId);
  if (!session) {
    return res.status(404).json({ error: "Session not found" });
  }

  let query = `
    SELECT * FROM annotations
    WHERE session_id = ?
  `;
  const params = [sessionId];

  // Optional type filter
  if (req.query.type) {
    const types = req.query.type.split(",").filter((t) => VALID_TYPES.includes(t));
    if (types.length > 0) {
      query += ` AND annotation_type IN (${types.map(() => "?").join(",")})`;
      params.push(...types);
    }
  }

  // Optional author filter
  if (req.query.author) {
    query += " AND author = ?";
    params.push(req.query.author);
  }

  query += " ORDER BY created_at ASC";

  // Pagination
  const { limit, offset } = parsePagination(req.query, { defaultLimit: 100, maxLimit: 500 });
  query += " LIMIT ? OFFSET ?";
  params.push(limit, offset);

  const annotations = db.prepare(query).all(...params);

  // Get total count
  let countQuery = `
    SELECT COUNT(*) as total FROM annotations WHERE session_id = ?
  `;
  const countParams = [sessionId];
  if (req.query.type) {
    const types = req.query.type.split(",").filter((t) => VALID_TYPES.includes(t));
    if (types.length > 0) {
      countQuery += ` AND annotation_type IN (${types.map(() => "?").join(",")})`;
      countParams.push(...types);
    }
  }
  if (req.query.author) {
    countQuery += " AND author = ?";
    countParams.push(req.query.author);
  }
  const { total } = db.prepare(countQuery).get(...countParams);

  // Type breakdown
  const breakdown = db
    .prepare(`
      SELECT annotation_type, COUNT(*) as count
      FROM annotations WHERE session_id = ?
      GROUP BY annotation_type ORDER BY count DESC
    `)
    .all(sessionId);

  res.json({
    session_id: sessionId,
    total,
    returned: annotations.length,
    limit,
    offset,
    type_breakdown: Object.fromEntries(breakdown.map((r) => [r.annotation_type, r.count])),
    annotations: annotations.map((a) => ({
      annotation_id: a.annotation_id,
      session_id: a.session_id,
      text: a.text,
      author: a.author,
      event_id: a.event_id,
      type: a.annotation_type,
      created_at: a.created_at,
      updated_at: a.updated_at,
    })),
  });
}));

// ── PUT /sessions/:id/annotations/:annId — update annotation ────────

router.put("/:id/annotations/:annId", wrapRoute("update annotation", (req, res) => {
  ensureAnnotationsTable();
  const db = getDb();
  const { id: sessionId, annId } = req.params;

  const existing = db
    .prepare(
      "SELECT * FROM annotations WHERE annotation_id = ? AND session_id = ?"
    )
    .get(annId, sessionId);
  if (!existing) {
    return res.status(404).json({ error: "Annotation not found" });
  }

  // Only allow updating text, type, and author
  const updates = {};
  if (req.body.text !== undefined) {
    if (typeof req.body.text !== "string" || !req.body.text.trim()) {
      return res.status(400).json({ error: "text must be a non-empty string" });
    }
    if (req.body.text.length > MAX_TEXT_LENGTH) {
      return res.status(400).json({
        error: `text must be at most ${MAX_TEXT_LENGTH} characters`,
      });
    }
    updates.text = req.body.text.trim();
  }
  if (req.body.type !== undefined) {
    if (!VALID_TYPES.includes(req.body.type)) {
      return res.status(400).json({
        error: `type must be one of: ${VALID_TYPES.join(", ")}`,
      });
    }
    updates.annotation_type = req.body.type;
  }
  if (req.body.author !== undefined) {
    if (typeof req.body.author !== "string" || !req.body.author.trim()) {
      return res.status(400).json({ error: "author must be a non-empty string" });
    }
    updates.author = req.body.author.trim();
  }

  if (Object.keys(updates).length === 0) {
    return res.status(400).json({ error: "No valid fields to update" });
  }

  updates.updated_at = new Date().toISOString();

  const setClauses = Object.keys(updates)
    .map((k) => `${k} = ?`)
    .join(", ");
  const values = Object.values(updates);
  values.push(annId, sessionId);

  db.prepare(`
    UPDATE annotations SET ${setClauses}
    WHERE annotation_id = ? AND session_id = ?
  `).run(...values);

  const updated = db
    .prepare("SELECT * FROM annotations WHERE annotation_id = ?")
    .get(annId);

  res.json({
    annotation_id: updated.annotation_id,
    session_id: updated.session_id,
    text: updated.text,
    author: updated.author,
    event_id: updated.event_id,
    type: updated.annotation_type,
    created_at: updated.created_at,
    updated_at: updated.updated_at,
  });
}));

// ── DELETE /sessions/:id/annotations/:annId — delete annotation ─────

router.delete("/:id/annotations/:annId", wrapRoute("delete annotation", (req, res) => {
  ensureAnnotationsTable();
  const db = getDb();
  const { id: sessionId, annId } = req.params;

  const existing = db
    .prepare(
      "SELECT * FROM annotations WHERE annotation_id = ? AND session_id = ?"
    )
    .get(annId, sessionId);
  if (!existing) {
    return res.status(404).json({ error: "Annotation not found" });
  }

  db.prepare(
    "DELETE FROM annotations WHERE annotation_id = ? AND session_id = ?"
  ).run(annId, sessionId);

  res.json({ deleted: true, annotation_id: annId });
}));

// ── GET /annotations/recent — recent annotations across all sessions ──

router.get("/", wrapRoute("fetch recent annotations", (req, res) => {
  ensureAnnotationsTable();
  const db = getDb();

  const { limit } = parsePagination(req.query);

  let query = `
    SELECT a.*, s.agent_name
    FROM annotations a
    JOIN sessions s ON a.session_id = s.session_id
  `;
  const params = [];

  // Optional type filter
  if (req.query.type) {
    const types = req.query.type.split(",").filter((t) => VALID_TYPES.includes(t));
    if (types.length > 0) {
      query += ` WHERE a.annotation_type IN (${types.map(() => "?").join(",")})`;
      params.push(...types);
    }
  }

  query += " ORDER BY a.created_at DESC LIMIT ?";
  params.push(limit);

  const annotations = db.prepare(query).all(...params);

  res.json({
    total: annotations.length,
    annotations: annotations.map((a) => ({
      annotation_id: a.annotation_id,
      session_id: a.session_id,
      agent_name: a.agent_name,
      text: a.text,
      author: a.author,
      event_id: a.event_id,
      type: a.annotation_type,
      created_at: a.created_at,
      updated_at: a.updated_at,
    })),
  });
}));

module.exports = router;
module.exports.ensureAnnotationsTable = ensureAnnotationsTable;
module.exports.VALID_TYPES = VALID_TYPES;
