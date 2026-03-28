const express = require("express");
const { getDb } = require("../db");
const {
  safeJsonParse,
  validateTag,
  validateTags,
  MAX_TAGS_PER_SESSION,
} = require("../lib/validation");
const { getTagStatements, batchGetTags } = require("../lib/tag-statements");
const { parsePagination, requireSessionId, wrapRoute } = require("../lib/request-helpers");

const router = express.Router();

// ── Collection-level routes ─────────────────────────────────────────

// GET /sessions/tags — List all unique tags across all sessions.
/**
 * GET /sessions/tags — List all tags with session counts.
 *
 * @returns {{ tags: { tag: string, session_count: number }[] }}
 */
router.get("/tags", wrapRoute("list tags", (req, res) => {
  const stmts = getTagStatements();
  const tags = stmts.allTags.all();
  res.json({ tags });
}));

// GET /sessions/by-tag/:tag — List sessions with a specific tag.
// (Must be before /:id routes to avoid matching "by-tag" as a session ID)
/**
 * GET /sessions/by-tag/:tag — List sessions that have a specific tag.
 *
 * @param {string} tag - The tag to filter by (URL-encoded path parameter).
 * @query {number} [limit=50] - Results per page (1-200).
 * @query {number} [offset=0] - Pagination offset.
 * @returns {{ sessions: Object[], total: number, tag: string }} Sessions matching the tag.
 */
router.get("/by-tag/:tag", wrapRoute("list sessions by tag", (req, res) => {
  const tag = validateTag(req.params.tag);
  if (!tag) {
    return res.status(400).json({ error: "Invalid tag" });
  }

  const { limit, offset } = parsePagination(req.query);

  const stmts = getTagStatements();
  const sessions = stmts.sessionsByTag.all(tag, limit, offset);
  const { count: total } = stmts.sessionsByTagCount.get(tag);

  // Batch-fetch all tags for the returned sessions using cached statements
  // instead of building a dynamic IN (...) prepared statement per request
  const sessionIds = sessions.map((s) => s.session_id);
  const tagMap = batchGetTags(sessionIds);

  const enriched = sessions.map((s) => ({
    ...s,
    metadata: safeJsonParse(s.metadata),
    tags: tagMap[s.session_id] || [],
  }));

  res.json({ sessions: enriched, total, limit, offset, tag });
}));

// ── Per-session routes ──────────────────────────────────────────────

// GET /sessions/:id/tags — Get tags for a session.
/**
 * GET /sessions/:id/tags — List all tags for a session.
 *
 * @param {string} id - Session ID (path parameter).
 * @returns {{ tags: string[], session_id: string }}
 * @returns {404} If session not found.
 */
router.get("/:id/tags", requireSessionId, wrapRoute("get tags", (req, res) => {
  const stmts = getTagStatements();
  const tags = stmts.getTagsForSession.all(req.params.id);
  res.json({ session_id: req.params.id, tags: tags.map((t) => t.tag) });
}));

// POST /sessions/:id/tags — Add tags to a session.
/**
 * POST /sessions/:id/tags — Add tags to a session.
 * Validates tag format and enforces per-session tag limit.
 *
 * @param {string} id - Session ID (path parameter).
 * @body {string[]} tags - Array of tag strings to add (max MAX_TAGS_PER_SESSION total).
 * @returns {{ tags: string[], added: number, session_id: string }}
 * @returns {400} If tags are invalid or limit exceeded.
 * @returns {404} If session not found.
 */
router.post("/:id/tags", requireSessionId, wrapRoute("add tags", (req, res) => {
  const { tags } = req.body || {};
  const validTags = validateTags(tags);
  if (!validTags) {
    return res.status(400).json({
      error: "Invalid tags. Provide an array of strings (alphanumeric, _-.:/ , max 64 chars each).",
    });
  }

  // Check session exists
  const db = getDb();
  const session = db.prepare("SELECT session_id FROM sessions WHERE session_id = ?").get(req.params.id);
  if (!session) {
    return res.status(404).json({ error: "Session not found" });
  }

  // Check tag limit
  const stmts = getTagStatements();
  const { count: existing } = stmts.countTags.get(req.params.id);
  if (existing + validTags.length > MAX_TAGS_PER_SESSION) {
    return res.status(400).json({
      error: `Tag limit exceeded. Session has ${existing} tags, adding ${validTags.length} would exceed max of ${MAX_TAGS_PER_SESSION}.`,
    });
  }

  const now = new Date().toISOString();
  const addMany = db.transaction(() => {
    let added = 0;
    for (const tag of validTags) {
      const result = stmts.addTag.run(req.params.id, tag, now);
      if (result.changes > 0) added++;
    }
    return added;
  });

  const added = addMany();
  const allTags = stmts.getTagsForSession.all(req.params.id).map((t) => t.tag);

  res.json({
    session_id: req.params.id,
    added,
    tags: allTags,
  });
}));

// DELETE /sessions/:id/tags — Remove tags from a session.
/**
 * DELETE /sessions/:id/tags — Remove tags from a session.
 * If no tags specified in body, removes all tags.
 *
 * @param {string} id - Session ID (path parameter).
 * @body {string[]} [tags] - Specific tags to remove. If omitted, removes all tags.
 * @returns {{ tags: string[], removed: number, session_id: string }}
 * @returns {404} If session not found.
 */
router.delete("/:id/tags", requireSessionId, wrapRoute("remove tags", (req, res) => {
  const { tags } = req.body || {};

  const stmts = getTagStatements();

  // If no tags specified, remove all
  if (!tags || (Array.isArray(tags) && tags.length === 0)) {
    const result = stmts.removeAllTags.run(req.params.id);
    return res.json({
      session_id: req.params.id,
      removed: result.changes,
      tags: [],
    });
  }

  const validTags = validateTags(tags);
  if (!validTags) {
    return res.status(400).json({ error: "Invalid tags array" });
  }

  const db = getDb();
  const removeMany = db.transaction(() => {
    let removed = 0;
    for (const tag of validTags) {
      const result = stmts.removeTag.run(req.params.id, tag);
      removed += result.changes;
    }
    return removed;
  });

  const removed = removeMany();
  const remaining = stmts.getTagsForSession.all(req.params.id).map((t) => t.tag);

  res.json({
    session_id: req.params.id,
    removed,
    tags: remaining,
  });
}));

module.exports = router;
