const express = require("express");
const { requireSessionId, wrapRoute } = require("../lib/request-helpers");
const { createLazyStatements } = require("../lib/lazy-statements");

const router = express.Router();

// ── Cached prepared statements ──────────────────────────────────────
const getBookmarkStatements = createLazyStatements((db) => ({
  listAll: db.prepare(
    `SELECT b.session_id, b.note, b.created_at,
            s.agent_name, s.started_at, s.status,
            s.total_tokens_in, s.total_tokens_out
     FROM session_bookmarks b
     JOIN sessions s ON s.session_id = b.session_id
     ORDER BY b.created_at DESC`
  ),
  getOne: db.prepare(
    "SELECT session_id, note, created_at FROM session_bookmarks WHERE session_id = ?"
  ),
  checkSession: db.prepare(
    "SELECT session_id FROM sessions WHERE session_id = ?"
  ),
  upsert: db.prepare(
    `INSERT INTO session_bookmarks (session_id, note, created_at)
     VALUES (?, ?, datetime('now'))
     ON CONFLICT(session_id) DO UPDATE SET note = excluded.note`
  ),
  remove: db.prepare(
    "DELETE FROM session_bookmarks WHERE session_id = ?"
  ),
}));

// ── GET /bookmarks — list all bookmarked sessions ───────────────────
router.get(
  "/",
  wrapRoute("list bookmarks", (req, res) => {
    const stmts = getBookmarkStatements();
    const rows = stmts.listAll.all();
    res.json({ bookmarks: rows });
  })
);

// ── GET /bookmarks/:sessionId — check if a session is bookmarked ────
router.get(
  "/:sessionId",
  requireSessionId,
  wrapRoute("check bookmark", (req, res) => {
    const stmts = getBookmarkStatements();
    const row = stmts.getOne.get(req.params.sessionId);
    res.json({ bookmarked: !!row, bookmark: row || null });
  })
);

// ── PUT /bookmarks/:sessionId — add or update a bookmark ────────────
router.put(
  "/:sessionId",
  requireSessionId,
  wrapRoute("upsert bookmark", (req, res) => {
    const stmts = getBookmarkStatements();
    const { sessionId } = req.params;
    const note = typeof req.body.note === "string" ? req.body.note.slice(0, 500) : "";

    // Verify session exists
    const session = stmts.checkSession.get(sessionId);
    if (!session) {
      return res.status(404).json({ error: "Session not found" });
    }

    stmts.upsert.run(sessionId, note);
    res.json({ bookmarked: true, session_id: sessionId, note });
  })
);

// ── DELETE /bookmarks/:sessionId — remove a bookmark ────────────────
router.delete(
  "/:sessionId",
  requireSessionId,
  wrapRoute("delete bookmark", (req, res) => {
    const stmts = getBookmarkStatements();
    const result = stmts.remove.run(req.params.sessionId);
    res.json({ bookmarked: false, deleted: result.changes > 0 });
  })
);

module.exports = router;
