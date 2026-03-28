/**
 * Lazy-initialised prepared statements for session tag operations.
 *
 * Shared between routes/sessions.js (tag-filtered session listing)
 * and routes/tags.js (tag CRUD and collection endpoints).
 */

const { getDb } = require("../db");

let _tagStmts = null;

function getTagStatements() {
  if (_tagStmts) return _tagStmts;
  const db = getDb();

  _tagStmts = {
    getTagsForSession: db.prepare(
      "SELECT tag, created_at FROM session_tags WHERE session_id = ? ORDER BY created_at ASC"
    ),
    addTag: db.prepare(
      "INSERT OR IGNORE INTO session_tags (session_id, tag, created_at) VALUES (?, ?, ?)"
    ),
    removeTag: db.prepare(
      "DELETE FROM session_tags WHERE session_id = ? AND tag = ?"
    ),
    removeAllTags: db.prepare(
      "DELETE FROM session_tags WHERE session_id = ?"
    ),
    countTags: db.prepare(
      "SELECT COUNT(*) as count FROM session_tags WHERE session_id = ?"
    ),
    sessionsByTag: db.prepare(
      `SELECT DISTINCT s.* FROM sessions s
       INNER JOIN session_tags st ON s.session_id = st.session_id
       WHERE st.tag = ?
       ORDER BY s.started_at DESC
       LIMIT ? OFFSET ?`
    ),
    sessionsByTagCount: db.prepare(
      `SELECT COUNT(DISTINCT s.session_id) as count FROM sessions s
       INNER JOIN session_tags st ON s.session_id = st.session_id
       WHERE st.tag = ?`
    ),
    allTags: db.prepare(
      `SELECT tag, COUNT(*) as session_count FROM session_tags
       GROUP BY tag ORDER BY session_count DESC, tag ASC`
    ),
  };

  return _tagStmts;
}

/**
 * Fetch all tags for a batch of session IDs in minimal SQL round-trips.
 * Uses chunked queries with cached prepared statements for common batch
 * sizes, falling back to per-session lookups for the remainder.
 * This replaces the previous approach of building a dynamic
 * `IN (?,?,?...)` prepared statement on every request.
 *
 * @param {string[]} sessionIds - Array of session IDs to look up tags for.
 * @returns {Object.<string, string[]>} Map of session_id → tag array.
 */
function batchGetTags(sessionIds) {
  if (!sessionIds || sessionIds.length === 0) return {};

  const stmts = getTagStatements();
  const tagMap = {};

  // For small batches, just do per-session lookups (cached statement, no dynamic SQL)
  if (sessionIds.length <= 5) {
    for (const sid of sessionIds) {
      const rows = stmts.getTagsForSession.all(sid);
      if (rows.length > 0) {
        tagMap[sid] = rows.map(r => r.tag);
      }
    }
    return tagMap;
  }

  // For larger batches, use chunked queries with cached prepared statements
  // for common chunk sizes to avoid re-preparing SQL each request.
  const db = getDb();
  const CHUNK_SIZES = [50, 10, 1];
  let remaining = [...sessionIds];

  for (const chunkSize of CHUNK_SIZES) {
    while (remaining.length >= chunkSize) {
      const chunk = remaining.splice(0, chunkSize);
      const key = `_batchTags_${chunkSize}`;
      if (!_tagStmts[key]) {
        const placeholders = new Array(chunkSize).fill("?").join(",");
        _tagStmts[key] = db.prepare(
          `SELECT session_id, tag FROM session_tags
           WHERE session_id IN (${placeholders})
           ORDER BY created_at ASC`
        );
      }
      const rows = _tagStmts[key].all(...chunk);
      for (const row of rows) {
        if (!tagMap[row.session_id]) tagMap[row.session_id] = [];
        tagMap[row.session_id].push(row.tag);
      }
    }
  }

  return tagMap;
}

module.exports = { getTagStatements, batchGetTags };
