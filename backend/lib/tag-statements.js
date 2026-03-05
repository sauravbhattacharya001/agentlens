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

module.exports = { getTagStatements };
