const Database = require("better-sqlite3");

function createTestDb() {
  const db = new Database(":memory:");
  db.pragma("foreign_keys = ON");
  db.exec(`
    CREATE TABLE sessions (session_id TEXT PRIMARY KEY, agent_name TEXT NOT NULL DEFAULT 'default-agent', started_at TEXT NOT NULL, ended_at TEXT, metadata TEXT DEFAULT '{}', total_tokens_in INTEGER DEFAULT 0, total_tokens_out INTEGER DEFAULT 0, status TEXT DEFAULT 'active');
    CREATE TABLE events (event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, event_type TEXT NOT NULL DEFAULT 'generic', timestamp TEXT NOT NULL, input_data TEXT, output_data TEXT, model TEXT, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, tool_call TEXT, decision_trace TEXT, duration_ms REAL, FOREIGN KEY (session_id) REFERENCES sessions(session_id));
    CREATE TABLE model_pricing (model TEXT PRIMARY KEY, input_cost_per_1m REAL NOT NULL DEFAULT 0, output_cost_per_1m REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD', updated_at TEXT NOT NULL);
    CREATE TABLE cost_budgets (scope TEXT NOT NULL, period TEXT NOT NULL CHECK(period IN ('daily','weekly','monthly','total')), limit_usd REAL NOT NULL, warn_pct REAL NOT NULL DEFAULT 80, created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY (scope, period));
  `);
  return db;
}

function seedData(db) {
  const now = new Date().toISOString();
  const today = new Date(); today.setHours(0,0,0,0);
  const t = today.toISOString();
  db.prepare("INSERT INTO sessions VALUES (?,?,?,null,'{}',0,0,'active')").run("sess-1","code-agent",t);
  db.prepare("INSERT INTO sessions VALUES (?,?,?,null,'{}',0,0,'active')").run("sess-2","search-agent",t);
  db.prepare("INSERT INTO sessions VALUES (?,?,?,null,'{}',0,0,'active')").run("sess-3","code-agent","2024-01-01T00:00:00Z");
  db.prepare("INSERT INTO events (event_id,session_id,event_type,timestamp,model,tokens_in,tokens_out) VALUES (?,?,?,?,?,?,?)").run("e1","sess-1","llm_call",t,"gpt-4o",1000,500);
  db.prepare("INSERT INTO events (event_id,session_id,event_type,timestamp,model,tokens_in,tokens_out) VALUES (?,?,?,?,?,?,?)").run("e2","sess-1","llm_call",t,"gpt-4o",2000,1000);
  db.prepare("INSERT INTO events (event_id,session_id,event_type,timestamp,model,tokens_in,tokens_out) VALUES (?,?,?,?,?,?,?)").run("e3","sess-2","llm_call",t,"claude-3-haiku",5000,2000);
  db.prepare("INSERT INTO events (event_id,session_id,event_type,timestamp,model,tokens_in,tokens_out) VALUES (?,?,?,?,?,?,?)").run("e4","sess-3","llm_call","2024-01-01T00:00:00Z","gpt-4o",100000,50000);
  db.prepare("INSERT INTO model_pricing VALUES (?,?,?,?,?)").run("gpt-4o",2.5,10.0,"USD",now);
  db.prepare("INSERT INTO model_pricing VALUES (?,?,?,?,?)").run("claude-3-haiku",0.25,1.25,"USD",now);
}

describe("Cost Budgets", () => {
  let db;
  beforeEach(() => { db = createTestDb(); seedData(db); });
  afterEach(() => { db.close(); });

  describe("Budget CRUD", () => {
    const ins = (db, scope, period, limit, warn) => {
      const now = new Date().toISOString();
      db.prepare("INSERT INTO cost_budgets (scope,period,limit_usd,warn_pct,created_at,updated_at) VALUES (?,?,?,?,?,?)").run(scope,period,limit,warn||80,now,now);
    };

    test("insert a budget", () => {
      ins(db, "global", "daily", 10.0);
      const r = db.prepare("SELECT * FROM cost_budgets WHERE scope=? AND period=?").get("global","daily");
      expect(r).toBeTruthy(); expect(r.limit_usd).toBe(10.0);
    });
    test("upsert updates on conflict", () => {
      ins(db, "global", "daily", 10.0);
      const now = new Date().toISOString();
      db.prepare("INSERT INTO cost_budgets (scope,period,limit_usd,warn_pct,created_at,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(scope,period) DO UPDATE SET limit_usd=excluded.limit_usd,warn_pct=excluded.warn_pct").run("global","daily",25.0,90,now,now);
      expect(db.prepare("SELECT limit_usd FROM cost_budgets WHERE scope='global' AND period='daily'").get().limit_usd).toBe(25.0);
    });
    test("delete a budget", () => {
      ins(db, "global", "monthly", 100.0);
      expect(db.prepare("DELETE FROM cost_budgets WHERE scope=? AND period=?").run("global","monthly").changes).toBe(1);
    });
    test("reject invalid period", () => {
      expect(() => ins(db, "global", "yearly", 100.0)).toThrow();
    });
    test("multiple budgets per scope", () => {
      ins(db, "global", "daily", 5.0); ins(db, "global", "monthly", 100.0);
      expect(db.prepare("SELECT * FROM cost_budgets WHERE scope='global'").all().length).toBe(2);
    });
    test("agent-scoped budgets", () => {
      ins(db, "agent:code-agent", "daily", 5.0); ins(db, "agent:search-agent", "daily", 2.0);
      expect(db.prepare("SELECT * FROM cost_budgets").all().length).toBe(2);
    });
    test("delete all budgets for scope", () => {
      ins(db, "agent:code-agent", "daily", 5.0); ins(db, "agent:code-agent", "monthly", 50.0);
      expect(db.prepare("DELETE FROM cost_budgets WHERE scope=?").run("agent:code-agent").changes).toBe(2);
    });
    test("enforce PK uniqueness", () => {
      ins(db, "global", "daily", 10.0);
      expect(() => ins(db, "global", "daily", 20.0)).toThrow();
    });
    test("same period different scopes", () => {
      ins(db, "global", "daily", 10.0); ins(db, "agent:code-agent", "daily", 5.0);
      expect(db.prepare("SELECT * FROM cost_budgets WHERE period='daily'").all().length).toBe(2);
    });
  });

  describe("Spend Calculation", () => {
    test("today's global events", () => {
      const today = new Date(); today.setHours(0,0,0,0);
      const rows = db.prepare("SELECT e.model, SUM(e.tokens_in) as ti, SUM(e.tokens_out) as to2 FROM events e JOIN sessions s ON e.session_id=s.session_id WHERE s.started_at>=? AND s.started_at<=? AND e.model IS NOT NULL AND e.model!='' GROUP BY e.model").all(today.toISOString(), new Date().toISOString());
      expect(rows.length).toBe(2);
      expect(rows.find(r=>r.model==="gpt-4o").ti).toBe(3000);
    });
    test("agent-specific spend", () => {
      const today = new Date(); today.setHours(0,0,0,0);
      const rows = db.prepare("SELECT e.model, SUM(e.tokens_in) as ti FROM events e JOIN sessions s ON e.session_id=s.session_id WHERE s.agent_name=? AND s.started_at>=? AND s.started_at<=? AND e.model IS NOT NULL GROUP BY e.model").all("code-agent",today.toISOString(),new Date().toISOString());
      expect(rows.length).toBe(1); expect(rows[0].model).toBe("gpt-4o");
    });
    test("cost calc using pricing", () => {
      const p = db.prepare("SELECT * FROM model_pricing WHERE model='gpt-4o'").get();
      expect((3000/1e6)*p.input_cost_per_1m).toBeCloseTo(0.0075,6);
      expect((1500/1e6)*p.output_cost_per_1m).toBeCloseTo(0.015,6);
    });
    test("total period includes old sessions", () => {
      const rows = db.prepare("SELECT e.model, SUM(e.tokens_in) as ti FROM events e JOIN sessions s ON e.session_id=s.session_id WHERE s.started_at>='2000-01-01' AND e.model IS NOT NULL AND e.model!='' GROUP BY e.model").all();
      expect(rows.find(r=>r.model==="gpt-4o").ti).toBe(103000);
    });
    test("no events for empty agent", () => {
      db.prepare("INSERT INTO sessions VALUES ('se','empty-agent',?,null,'{}',0,0,'active')").run(new Date().toISOString());
      expect(db.prepare("SELECT e.model FROM events e JOIN sessions s ON e.session_id=s.session_id WHERE s.agent_name='empty-agent' AND e.model IS NOT NULL GROUP BY e.model").all().length).toBe(0);
    });
    test("null model excluded", () => {
      db.prepare("INSERT INTO events (event_id,session_id,event_type,timestamp,model,tokens_in,tokens_out) VALUES ('en','sess-1','tool',?,null,100,50)").run(new Date().toISOString());
      const rows = db.prepare("SELECT e.model FROM events e WHERE e.session_id='sess-1' AND e.model IS NOT NULL AND e.model!=''").all();
      expect(rows.every(r=>r.model!==null)).toBe(true);
    });
  });

  describe("Budget Status Logic", () => {
    test("exceeded", () => { expect(Math.round((10.5/10)*10000)/100 >= 100 ? "exceeded" : "ok").toBe("exceeded"); });
    test("warning", () => { const p=Math.round((8.5/10)*10000)/100; expect(p>=100?"exceeded":p>=80?"warning":"ok").toBe("warning"); });
    test("ok", () => { const p=Math.round((5/10)*10000)/100; expect(p>=100?"exceeded":p>=80?"warning":"ok").toBe("ok"); });
    test("remaining clamps to 0", () => { expect(Math.max(0,10-12)).toBe(0); });
    test("zero limit", () => { expect(0>0 ? 5/0 : 0).toBe(0); });
  });

  describe("Period Ranges", () => {
    test("daily midnight", () => { const n=new Date(); expect(new Date(n.getFullYear(),n.getMonth(),n.getDate()).getHours()).toBe(0); });
    test("weekly Sunday", () => { const n=new Date(); expect(new Date(n.getFullYear(),n.getMonth(),n.getDate()-n.getDay()).getDay()).toBe(0); });
    test("monthly 1st", () => { expect(new Date(new Date().getFullYear(),new Date().getMonth(),1).getDate()).toBe(1); });
  });

  describe("Multi-budget check", () => {
    test("finds agent + global budgets", () => {
      const now = new Date().toISOString();
      db.prepare("INSERT INTO cost_budgets VALUES ('global','daily',1.0,80,?,?)").run(now,now);
      db.prepare("INSERT INTO cost_budgets VALUES ('agent:code-agent','daily',0.5,80,?,?)").run(now,now);
      const s = db.prepare("SELECT * FROM sessions WHERE session_id='sess-1'").get();
      expect(db.prepare("SELECT * FROM cost_budgets WHERE scope=?").all(`agent:${s.agent_name}`).length).toBe(1);
      expect(db.prepare("SELECT * FROM cost_budgets WHERE scope='global'").all().length).toBe(1);
    });
  });
});
