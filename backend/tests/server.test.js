/* ── server.js (app factory) Tests ─────────────────────────────────
 *
 * server.js was previously untestable: importing it bound a TCP port,
 * opened the SQLite database on load, and installed SIGTERM/SIGINT
 * handlers — all process-level side effects. It now exports a pure
 * `createApp()` factory (wiring only) with those side effects fenced
 * behind the `require.main === module` entry point, so the full
 * middleware + route wiring can be exercised in-process here.
 *
 * The real route modules are replaced with trivial stub routers so this
 * suite tests server.js's own wiring (mount order, dedup'd auth/limiter
 * registration, static/no-cache/health/catch-all/error middleware),
 * not each route's internals — those have their own suites.
 */

// db is required at module load (closeDb) and getDb() runs in start(),
// which this suite never calls; stub both so no real DB is opened.
jest.mock("../db", () => ({ getDb: jest.fn(), closeDb: jest.fn() }));

// Replace every mounted route module with a tiny router that echoes its
// mount so tests can assert the request reached the route layer (i.e.
// passed through limiter + auth + body-parser + no-cache middleware).
// NB: jest hoists jest.mock() above imports and forbids referencing
// out-of-scope helpers inside a factory, so each router is built inline
// (lazily requiring express) within its own factory.
function mockStub(label) {
  const express = require("express");
  const r = express.Router();
  r.get("/ping", (req, res) => res.json({ route: label }));
  r.post("/echo", (req, res) => res.json({ route: label, body: req.body }));
  return r;
}
jest.mock("../routes/events", () => mockStub("events"));
jest.mock("../routes/tags", () => mockStub("tags"));
jest.mock("../routes/sessions", () => mockStub("sessions"));
jest.mock("../routes/analytics", () => mockStub("analytics"));
jest.mock("../routes/pricing", () => mockStub("pricing"));
jest.mock("../routes/alerts", () => mockStub("alerts"));
jest.mock("../routes/annotations", () => mockStub("annotations"));
jest.mock("../routes/retention", () => mockStub("retention"));
jest.mock("../routes/leaderboard", () => mockStub("leaderboard"));
jest.mock("../routes/errors", () => mockStub("errors"));
jest.mock("../routes/webhooks", () => mockStub("webhooks"));
jest.mock("../routes/bookmarks", () => mockStub("bookmarks"));
jest.mock("../routes/replay", () => mockStub("replay"));
jest.mock("../routes/diff", () => mockStub("diff"));

const request = require("supertest");

// Build a fresh app under a given env. server.js reads AGENTLENS_API_KEY
// (auth) and AGENTLENS_TRUST_PROXY inside createApp() at call time, so we
// must keep the env active THROUGH the createApp() call, then restore.
function buildApp(env = {}) {
  const saved = {};
  for (const [k, v] of Object.entries(env)) {
    saved[k] = process.env[k];
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  let built;
  try {
    jest.isolateModules(() => {
      const { createApp } = require("../server");
      built = createApp();
    });
  } finally {
    for (const [k] of Object.entries(env)) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  }
  return built; // { app, hasApiKey }
}

describe("server.js createApp() wiring", () => {
  test("exports createApp and start functions without side effects", () => {
    const mod = require("../server");
    expect(typeof mod.createApp).toBe("function");
    expect(typeof mod.start).toBe("function");
    const { getDb } = require("../db");
    // Merely requiring the module must not open the DB (start() does that).
    expect(getDb).not.toHaveBeenCalled();
  });

  test("createApp returns an app and hasApiKey=false in dev mode (no API key)", () => {
    const { app, hasApiKey } = buildApp({ AGENTLENS_API_KEY: undefined });
    expect(typeof app).toBe("function"); // express app is callable
    expect(hasApiKey).toBe(false);
  });

  test("/health returns ok with a timestamp and no auth required", async () => {
    const { app } = buildApp({ AGENTLENS_API_KEY: "secret" });
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("ok");
    expect(typeof res.body.timestamp).toBe("string");
  });

  test("mounted routes are reachable and pass through the middleware stack (dev mode)", async () => {
    const { app } = buildApp({ AGENTLENS_API_KEY: undefined });
    const res = await request(app).get("/events/ping");
    expect(res.status).toBe(200);
    expect(res.body.route).toBe("events");
  });

  test("body parser is wired: POST body reaches the route handler", async () => {
    const { app } = buildApp({ AGENTLENS_API_KEY: undefined });
    const res = await request(app).post("/sessions/echo").send({ hi: "there" });
    expect(res.status).toBe(200);
    expect(res.body.body).toEqual({ hi: "there" });
  });

  test("API responses carry no-store cache-control headers", async () => {
    const { app } = buildApp({ AGENTLENS_API_KEY: undefined });
    const res = await request(app).get("/analytics/ping");
    expect(res.headers["cache-control"]).toContain("no-store");
    expect(res.headers["pragma"]).toBe("no-cache");
    expect(res.headers["expires"]).toBe("0");
  });

  test("auth gate: protected route rejects missing key when AGENTLENS_API_KEY is set", async () => {
    const { app, hasApiKey } = buildApp({ AGENTLENS_API_KEY: "topsecret" });
    expect(hasApiKey).toBe(true);
    const res = await request(app).get("/pricing/ping");
    expect(res.status).toBe(401);
  });

  test("auth gate: protected route accepts the correct key", async () => {
    const { app } = buildApp({ AGENTLENS_API_KEY: "topsecret" });
    const res = await request(app)
      .get("/pricing/ping")
      .set("x-api-key", "topsecret");
    expect(res.status).toBe(200);
    expect(res.body.route).toBe("pricing");
  });

  test("unknown API path under an auth'd mount still gates before 404 (dev mode passes through to 404)", async () => {
    const { app } = buildApp({ AGENTLENS_API_KEY: undefined });
    const res = await request(app).get("/pricing/does-not-exist");
    expect(res.status).toBe(404);
  });

  test("global error handler returns a generic 500 without leaking internals", async () => {
    // A throwing route is registered inside createApp (via the mocked
    // ./routes/diff module) BEFORE the factory's 4-arg error handler, so
    // its synchronous throw funnels through that error middleware — the
    // exact path server.js's global handler exists to cover.
    let created;
    jest.isolateModules(() => {
      jest.doMock("../db", () => ({ getDb: jest.fn(), closeDb: jest.fn() }));
      jest.doMock("../routes/diff", () => {
        const express = require("express");
        const r = express.Router();
        r.get("/boom", () => {
          throw new Error("kaboom internal detail");
        });
        return r;
      });
      created = require("../server").createApp();
    });
    const { app } = created;
    const spy = jest.spyOn(console, "error").mockImplementation(() => {});
    const res = await request(app).get("/diff/boom");
    expect(res.status).toBe(500);
    expect(res.body).toEqual({ error: "Internal server error" });
    expect(res.text).not.toContain("kaboom internal detail");
    spy.mockRestore();
  });

  describe("start() process entry point", () => {
    // start() binds a port, opens the DB, and installs signal handlers.
    // We drive it with an ephemeral port (PORT=0) and the top-of-file db
    // mock so no real database is opened, then assert the side effects and
    // exercise the graceful-shutdown path without killing the test process.
    let signalListeners;
    let openServers;
    let logSpy;
    let errSpy;

    beforeEach(() => {
      // Silence banner/shutdown logging for the whole block so a listen
      // callback that fires after a test settles can't "log after tests".
      logSpy = jest.spyOn(console, "log").mockImplementation(() => {});
      errSpy = jest.spyOn(console, "error").mockImplementation(() => {});
      const db = require("../db");
      db.getDb.mockClear();
      db.closeDb.mockClear();
      openServers = [];
      // Snapshot signal listeners so start()'s additions can be removed.
      signalListeners = {
        SIGTERM: process.listeners("SIGTERM").slice(),
        SIGINT: process.listeners("SIGINT").slice(),
      };
    });

    afterEach(async () => {
      // Force every server this test opened fully closed BEFORE restoring
      // the console spies, so no late listen/close callback logs escape.
      for (const s of openServers) {
        if (s && typeof s.close === "function" && s.listening) {
          await new Promise((r) => s.close(r));
        }
      }
      await new Promise((r) => setImmediate(r));
      for (const sig of ["SIGTERM", "SIGINT"]) {
        for (const l of process.listeners(sig)) {
          if (!signalListeners[sig].includes(l)) process.removeListener(sig, l);
        }
      }
      logSpy.mockRestore();
      errSpy.mockRestore();
    });

    function startServer(env = {}) {
      const saved = { PORT: process.env.PORT };
      process.env.PORT = "0"; // ephemeral port — never collides
      for (const [k, v] of Object.entries(env)) {
        saved[k] = process.env[k];
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
      }
      let server;
      jest.isolateModules(() => {
        server = require("../server").start();
      });
      for (const [k, v] of Object.entries(saved)) {
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
      }
      openServers.push(server);
      // The db module is mocked at the top of this file; return the SAME
      // mock instance server.js bound its getDb/closeDb references to.
      return { server, dbMock: require("../db") };
    }

    function waitListening(server) {
      return new Promise((resolve) => {
        if (server.listening) resolve();
        else server.once("listening", resolve);
      });
    }

    test("binds a listening server and opens the DB", async () => {
      const { server, dbMock } = startServer({ AGENTLENS_API_KEY: undefined });
      await waitListening(server);
      expect(server.listening).toBe(true);
      expect(dbMock.getDb).toHaveBeenCalledTimes(1);
    });

    test("logs the auth-enabled banner when AGENTLENS_API_KEY is set", async () => {
      const { server } = startServer({ AGENTLENS_API_KEY: "k" });
      await waitListening(server);
      await new Promise((r) => setImmediate(r));
      const logged = logSpy.mock.calls.map((c) => String(c[0])).join("\n");
      expect(logged).toMatch(/API key authentication enabled/);
    });

    test("SIGTERM triggers graceful shutdown: closes server + DB, exits 0", async () => {
      const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});
      try {
        const { server, dbMock } = startServer({ AGENTLENS_API_KEY: undefined });
        await waitListening(server);
        // start() registered a handler that calls server.close(cb) then
        // closeDb() + process.exit(0) in the callback.
        const closed = new Promise((resolve) => server.on("close", resolve));
        process.emit("SIGTERM");
        await closed;
        await new Promise((r) => setImmediate(r));
        expect(dbMock.closeDb).toHaveBeenCalled();
        expect(exitSpy).toHaveBeenCalledWith(0);
      } finally {
        exitSpy.mockRestore();
      }
    });

    test("shutdown force-exits with code 1 if the server never drains", async () => {
      const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});
      const { server, dbMock } = startServer({ AGENTLENS_API_KEY: undefined });
      await waitListening(server);
      jest.useFakeTimers();
      try {
        // Stub server.close so its callback NEVER fires (connections stuck),
        // forcing the 10s watchdog timer to be the thing that exits.
        server.close = jest.fn();
        process.emit("SIGTERM");
        jest.advanceTimersByTime(10000);
        expect(dbMock.closeDb).toHaveBeenCalled();
        expect(exitSpy).toHaveBeenCalledWith(1);
      } finally {
        jest.useRealTimers();
        exitSpy.mockRestore();
        // Real close so afterEach can drain it (we stubbed .close above).
        delete server.close;
      }
    });
  });

  describe("trust proxy configuration", () => {
    test("unset → trust proxy stays disabled (default false)", () => {
      const { app } = buildApp({ AGENTLENS_TRUST_PROXY: undefined });
      expect(app.get("trust proxy")).toBe(false);
    });

    test("numeric string → parsed as a hop count (number)", () => {
      const { app } = buildApp({ AGENTLENS_TRUST_PROXY: "2" });
      expect(app.get("trust proxy")).toBe(2);
    });

    test("named/CIDR string → passed through verbatim", () => {
      const { app } = buildApp({ AGENTLENS_TRUST_PROXY: "loopback" });
      expect(app.get("trust proxy")).toBe("loopback");
    });

    test("whitespace-only → treated as unset", () => {
      const { app } = buildApp({ AGENTLENS_TRUST_PROXY: "   " });
      expect(app.get("trust proxy")).toBe(false);
    });
  });
});
