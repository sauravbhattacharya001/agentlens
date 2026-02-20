/**
 * Tests for backend/middleware.js — security and auth middleware.
 *
 * Tests createApiKeyAuth, createHelmetMiddleware, createCorsMiddleware,
 * createApiLimiter, and createIngestLimiter factory functions.
 */

const {
  createHelmetMiddleware,
  createCorsMiddleware,
  createApiLimiter,
  createIngestLimiter,
  createApiKeyAuth,
} = require("../middleware");

/* ================================================================
 * createApiKeyAuth
 * ================================================================ */
describe("createApiKeyAuth", () => {
  const originalEnv = process.env.AGENTLENS_API_KEY;

  afterEach(() => {
    if (originalEnv !== undefined) {
      process.env.AGENTLENS_API_KEY = originalEnv;
    } else {
      delete process.env.AGENTLENS_API_KEY;
    }
  });

  test("dev mode (no key set) — passes all requests through", () => {
    delete process.env.AGENTLENS_API_KEY;
    const { authenticateApiKey, hasApiKey } = createApiKeyAuth();

    expect(hasApiKey).toBe(false);

    const req = { headers: {} };
    const res = {
      status: jest.fn().mockReturnThis(),
      json: jest.fn(),
    };
    const next = jest.fn();

    authenticateApiKey(req, res, next);

    expect(next).toHaveBeenCalled();
    expect(res.status).not.toHaveBeenCalled();
  });

  test("auth mode — rejects requests without API key", () => {
    process.env.AGENTLENS_API_KEY = "test-secret-key";
    const { authenticateApiKey, hasApiKey } = createApiKeyAuth();

    expect(hasApiKey).toBe(true);

    const req = { headers: {} };
    const res = {
      status: jest.fn().mockReturnThis(),
      json: jest.fn(),
    };
    const next = jest.fn();

    authenticateApiKey(req, res, next);

    expect(next).not.toHaveBeenCalled();
    expect(res.status).toHaveBeenCalledWith(401);
    expect(res.json).toHaveBeenCalledWith(
      expect.objectContaining({ error: expect.stringContaining("Unauthorized") })
    );
  });

  test("auth mode — rejects requests with wrong API key", () => {
    process.env.AGENTLENS_API_KEY = "correct-key";
    const { authenticateApiKey } = createApiKeyAuth();

    const req = { headers: { "x-api-key": "wrong-key" } };
    const res = {
      status: jest.fn().mockReturnThis(),
      json: jest.fn(),
    };
    const next = jest.fn();

    authenticateApiKey(req, res, next);

    expect(next).not.toHaveBeenCalled();
    expect(res.status).toHaveBeenCalledWith(401);
  });

  test("auth mode — accepts requests with correct API key", () => {
    process.env.AGENTLENS_API_KEY = "correct-key";
    const { authenticateApiKey } = createApiKeyAuth();

    const req = { headers: { "x-api-key": "correct-key" } };
    const res = {
      status: jest.fn().mockReturnThis(),
      json: jest.fn(),
    };
    const next = jest.fn();

    authenticateApiKey(req, res, next);

    expect(next).toHaveBeenCalled();
    expect(res.status).not.toHaveBeenCalled();
  });
});

/* ================================================================
 * Middleware factory functions return middleware
 * ================================================================ */
describe("middleware factories", () => {
  test("createHelmetMiddleware returns a function", () => {
    const middleware = createHelmetMiddleware();
    expect(typeof middleware).toBe("function");
  });

  test("createCorsMiddleware returns a function", () => {
    const middleware = createCorsMiddleware();
    expect(typeof middleware).toBe("function");
  });

  test("createApiLimiter returns a function", () => {
    const middleware = createApiLimiter();
    expect(typeof middleware).toBe("function");
  });

  test("createIngestLimiter returns a function", () => {
    const middleware = createIngestLimiter();
    expect(typeof middleware).toBe("function");
  });
});

/* ================================================================
 * CORS origins from environment
 * ================================================================ */
describe("createCorsMiddleware with CORS_ORIGINS", () => {
  const originalEnv = process.env.CORS_ORIGINS;

  afterEach(() => {
    if (originalEnv !== undefined) {
      process.env.CORS_ORIGINS = originalEnv;
    } else {
      delete process.env.CORS_ORIGINS;
    }
  });

  test("without CORS_ORIGINS env — uses permissive mode", () => {
    delete process.env.CORS_ORIGINS;
    const middleware = createCorsMiddleware();
    // Should return without error
    expect(typeof middleware).toBe("function");
  });

  test("with CORS_ORIGINS env — creates middleware", () => {
    process.env.CORS_ORIGINS = "http://localhost:3000, https://example.com";
    const middleware = createCorsMiddleware();
    expect(typeof middleware).toBe("function");
  });
});
