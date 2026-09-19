const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
const WS_BASE = BASE.replace(/^http/, 'ws');

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// A request that never answers must fail, not spin: the backend can be held
// open by a stalled upstream (a gateway that accepts and then goes silent), and
// without a deadline here the UI would show "Writing…" indefinitely. Callers
// that legitimately take long (scenario writing) pass a larger `timeoutMs`.
const DEFAULT_TIMEOUT_MS = 60000;

async function request(path, { method = 'GET', body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  // Honour the caller's own abort as well as the deadline.
  signal?.addEventListener('abort', () => controller.abort(), { once: true });

  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      if (!timedOut) throw err;
      throw new ApiError(
        `No answer from the backend within ${Math.round(timeoutMs / 1000)}s. `
          + 'The model gateway may be stalling — try again.',
        0,
      );
    }
    throw new ApiError('Backend is unreachable. Is it running on ' + BASE + '?', 0);
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') {
        detail = data.detail;
      } else if (Array.isArray(data.detail)) {
        // FastAPI validation errors (422) come as a list of {loc, msg, ...};
        // join the messages so the toast reads text, not "[object Object]".
        detail = data.detail.map((e) => e.msg || JSON.stringify(e)).join('; ');
      } else if (data.detail) {
        detail = JSON.stringify(data.detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return null;
  return response.json();
}

/** Consume an NDJSON stream, invoking `onEvent` for each complete line. */
async function streamNdjson(path, { body, signal, onEvent }) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
      signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError(`Could not reach the backend at ${BASE}. Is it still running?`, 0);
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      // The server died mid-run. Say so, rather than letting the caller
      // surface a bare "network error" that could mean anything.
      throw new ApiError(
        'The run stopped because the connection to the backend dropped. '
        + 'Check the backend console for the error that killed it.',
        0,
      );
    }
    const { value, done } = chunk;
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newline;
    // Only parse whole lines; a chunk can split a JSON object in half.
    while ((newline = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      try {
        onEvent(JSON.parse(line));
      } catch {
        console.warn('Skipping malformed stream line:', line);
      }
    }
  }

  const tail = buffer.trim();
  if (tail) {
    try {
      onEvent(JSON.parse(tail));
    } catch {
      /* ignore trailing partial */
    }
  }
}

export const api = {
  health: () => request('/api/health'),
  providers: () => request('/api/settings/providers'),
  providerModels: (provider) => request(`/api/settings/providers/${provider}/models`),
  saveProvider: (provider, model, api_key, extra = null) =>
    request('/api/settings/provider', { method: 'POST', body: { provider, model, api_key, extra } }),
  testProvider: (provider, model, api_key, extra = null) =>
    request('/api/settings/test', { method: 'POST', body: { provider, model, api_key, extra } }),

  appiumStatus: () => request('/api/appium/status'),
  startAppium: () => request('/api/appium/start', { method: 'POST' }),
  stopAppium: () => request('/api/appium/stop', { method: 'POST' }),
  appiumLogs: () => request('/api/appium/logs'),

  devices: () => request('/api/devices'),
  deviceApps: (udid, platform) =>
    request(`/api/devices/${encodeURIComponent(udid)}/apps?platform=${encodeURIComponent(platform)}`),

  // BrowserStack: the same sessions, on devices nobody has to keep on a desk.
  browserstackStatus: () => request('/api/browserstack/status'),
  browserstackDevices: () => request('/api/browserstack/devices'),
  saveBrowserstackCredentials: (username, accessKey) =>
    request('/api/browserstack/credentials', {
      method: 'POST',
      body: { username, accessKey },
    }),

  // A cloud device can sit in a queue before it is handed over, and the
  // backend then spends a few more seconds answering the app's first-launch
  // permission prompts before it answers. The 60s default cut that off while
  // the session was live on the other side, leaving a device booked and a
  // tester told it had failed.
  createSession: (device, appId) =>
    request('/api/appium/session', {
      method: 'POST',
      timeoutMs: 300000,
      body: { udid: device.udid, platform: device.platform, name: device.name, appId: appId || null },
    }),
  createWebSession: (url, viewport, browser, headless = true, size = null) =>
    request('/api/web/session', {
      method: 'POST',
      body: { url, viewport, browser, headless, width: size?.width, height: size?.height },
    }),
  navigate: (sessionId, url) =>
    request(`/api/web/session/${sessionId}/navigate`, { method: 'POST', body: { url } }),
  reopenWebSession: (sessionId) =>
    request(`/api/web/session/${sessionId}/reopen`, { method: 'POST' }),
  setViewport: (sessionId, width, height) =>
    request(`/api/web/session/${sessionId}/viewport`, { method: 'POST', body: { width, height } }),

  deleteSession: (sessionId) => request(`/api/session/${sessionId}`, { method: 'DELETE' }),
  sessions: () => request('/api/sessions'),

  screenshot: (sessionId) => request(`/api/session/${sessionId}/screenshot`),
  source: (sessionId) => request(`/api/session/${sessionId}/source`),
  elementAt: (sessionId, x, y) =>
    request(`/api/session/${sessionId}/element-at?x=${Math.round(x)}&y=${Math.round(y)}`),

  gesture: (sessionId, payload) =>
    request(`/api/session/${sessionId}/gesture`, { method: 'POST', body: payload }),
  action: (sessionId, payload) =>
    request(`/api/session/${sessionId}/action`, { method: 'POST', body: payload }),

  runAgent: (sessionId, body, onEvent, signal) =>
    streamNdjson(`/api/session/${sessionId}/agent/run`, { body, onEvent, signal }),

  // --- deterministic checks (no model, no tokens) -------------------------
  suggestions: (sessionId) => request(`/api/session/${sessionId}/suggestions`),
  explore: (sessionId, body, onEvent, signal) =>
    streamNdjson(`/api/session/${sessionId}/explore`, { body, onEvent, signal }),
  scanLinks: (sessionId) =>
    request(`/api/session/${sessionId}/scan/links`, { method: 'POST' }),
  scanAccessibility: (sessionId) =>
    request(`/api/session/${sessionId}/scan/accessibility`, { method: 'POST' }),

  stopAgent: (sessionId) => request(`/api/session/${sessionId}/agent/stop`, { method: 'POST' }),
  agentStatus: (sessionId) => request(`/api/session/${sessionId}/agent/status`),

  runs: (limit = 50, q = '', offset = 0, kind = null) =>
    request(`/api/runs?limit=${limit}&offset=${offset}`
      + (q ? `&q=${encodeURIComponent(q)}` : '')
      + (kind ? `&kind=${kind}` : '')),
  run: (runId) => request(`/api/runs/${runId}`),
  stepScreenshot: (runId, stepId) => request(`/api/runs/${runId}/steps/${stepId}/screenshot`),
  renameRun: (runId, title) => request(`/api/runs/${runId}`, { method: 'PATCH', body: { title } }),
  deleteRun: (runId) => request(`/api/runs/${runId}`, { method: 'DELETE' }),
  exportRun: (runId, format) => request(`/api/runs/${runId}/export?format=${format}`),
  replayRun: (runId, sessionId, onEvent, signal, heal = true) =>
    streamNdjson(`/api/runs/${runId}/replay?session_id=${sessionId}&heal=${heal}`, { onEvent, signal }),

  // --- suites -------------------------------------------------------------
  suites: () => request('/api/suites'),
  suite: (suiteId) => request(`/api/suites/${suiteId}`),
  createSuite: (body) => request('/api/suites', { method: 'POST', body }),
  updateSuite: (suiteId, body) => request(`/api/suites/${suiteId}`, { method: 'PATCH', body }),
  deleteSuite: (suiteId) => request(`/api/suites/${suiteId}`, { method: 'DELETE' }),

  addCase: (suiteId, body) => request(`/api/suites/${suiteId}/cases`, { method: 'POST', body }),
  addCases: (suiteId, cases) =>
    request(`/api/suites/${suiteId}/cases/bulk`, { method: 'POST', body: { cases } }),

  // --- scenario writing to the Digital Channels standard -------------------
  // From a written brief, or from whatever is on screen right now. The screen
  // version names real fields and buttons, so prefer it when a page is open.
  // Writing a full scenario set is the one long call: give it the same 300s
  // deadline the backend enforces on its side, so whichever side gives up
  // first, the tester sees an error and a live Generate button, not a spinner.
  generateScenarios: (body) =>
    request('/api/scenarios/generate', { method: 'POST', body, timeoutMs: 300000 }),
  generateScenariosFromScreen: (sessionId, body) =>
    request(`/api/session/${sessionId}/scenarios/generate`, { method: 'POST', body, timeoutMs: 300000 }),
  updateCase: (caseId, body) => request(`/api/cases/${caseId}`, { method: 'PATCH', body }),
  deleteCase: (caseId) => request(`/api/cases/${caseId}`, { method: 'DELETE' }),
  // Re-file picked scenarios into another Test Set (existing or just created).
  moveCases: (caseIds, suiteId) =>
    request('/api/cases/move', { method: 'POST', body: { caseIds, suiteId } }),
  saveRunAsCase: (runId, suiteId, name = null) =>
    request(
      `/api/runs/${runId}/save-as-case?suite_id=${suiteId}`
        + (name ? `&name=${encodeURIComponent(name)}` : ''),
      { method: 'POST' },
    ),

  // An execution from hand-picked scenarios, from one Test Set or several.
  createExecution: (body, onEvent, signal) =>
    streamNdjson('/api/executions', { body, onEvent, signal }),
  // Starts the execution on the server and returns its id. Use this when the
  // caller is about to close: a streamed run dies with the connection.
  startExecution: (body) =>
    request('/api/executions/start', { method: 'POST', body, timeoutMs: 90000 }),

  runSuite: (suiteId, body, onEvent, signal) =>
    streamNdjson(`/api/suites/${suiteId}/run`, { body, onEvent, signal }),
  // Runs the whole set on the server and returns the execution's id.
  startSuiteRun: (suiteId, body) =>
    request(`/api/suites/${suiteId}/run/start`, { method: 'POST', body, timeoutMs: 90000 }),
  suiteRuns: (suiteId = null, limit = 50) =>
    request(`/api/suite-runs?limit=${limit}${suiteId ? `&suite_id=${suiteId}` : ''}`),
  suiteRun: (suiteRunId) => request(`/api/suite-runs/${suiteRunId}`),
  deleteSuiteRun: (suiteRunId) =>
    request(`/api/suite-runs/${suiteRunId}`, { method: 'DELETE' }),
  cancelSuiteRun: (suiteRunId) =>
    request(`/api/suite-runs/${suiteRunId}/cancel`, { method: 'POST' }),

  // Bugs. The draft is composed from the run and handed back unsaved — a bug
  // that files itself is a bug nobody has checked.
  // Answering what a scenario asked for. Filling the last field turns the
  // scenario back on, which the backend does rather than the caller.
  setPreconditionData: (caseId, data) =>
    request(`/api/cases/${caseId}`, { method: 'PATCH', body: { preconditionData: data } }),

  bugDraft: (runId) => request(`/api/runs/${runId}/bug-draft`),
  bugs: ({ status = null, code = null, search = null, kind = null } = {}) => {
    const q = new URLSearchParams();
    if (status) q.set('status', status);
    if (code) q.set('code', code);
    if (search) q.set('search', search);
    if (kind) q.set('kind', kind);
    const query = q.toString();
    return request(`/api/bugs${query ? `?${query}` : ''}`);
  },
  createBug: (body) => request('/api/bugs', { method: 'POST', body }),
  updateBug: (bugId, body) => request(`/api/bugs/${bugId}`, { method: 'PATCH', body }),
  deleteBug: (bugId) => request(`/api/bugs/${bugId}`, { method: 'DELETE' }),
  bugScreenshot: (bugId) => request(`/api/bugs/${bugId}/screenshot`),

  // Reports and artifacts are files, so they are linked rather than fetched.
  suiteReportUrl: (suiteRunId, format) =>
    `${BASE}/api/suite-runs/${suiteRunId}/report?format=${format}`,
  runReportUrl: (runId, format) => `${BASE}/api/runs/${runId}/report?format=${format}`,
  artifactUrl: (artifactId) => `${BASE}/api/artifacts/${artifactId}/download`,

  // --- insights -----------------------------------------------------------
  // All four take a platform, because a pass rate that averages a mature web
  // suite with a handful of mobile runs describes neither of them.
  trend: (days = 14, kind = null) =>
    request(`/api/insights/trend?days=${days}${kind ? `&kind=${kind}` : ''}`),
  flaky: (limit = 20, kind = null) =>
    request(`/api/insights/flaky?limit=${limit}${kind ? `&kind=${kind}` : ''}`),
  priorityBreakdown: (days = 14, kind = null) =>
    request(`/api/insights/priority?days=${days}${kind ? `&kind=${kind}` : ''}`),
  // What the runs asked of the model. Not the quota left on the key — that
  // lives with whoever issues it — but what QAi itself spent.
  usage: (days = 14, kind = null) =>
    request(`/api/insights/usage?days=${days}${kind ? `&kind=${kind}` : ''}`),
  // Spend against the monthly limit, straight from the gateway.
  budget: () => request('/api/insights/budget'),

  // --- saved sign-ins, mocking, baselines ---------------------------------
  authProfiles: () => request('/api/auth-profiles'),
  saveAuth: (sessionId, name) =>
    request(`/api/session/${sessionId}/save-auth?name=${encodeURIComponent(name)}`, { method: 'POST' }),
  deleteAuthProfile: (name) =>
    request(`/api/auth-profiles/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  routes: (sessionId) => request(`/api/session/${sessionId}/routes`),
  setRoutes: (sessionId, rules) =>
    request(`/api/session/${sessionId}/routes`, { method: 'POST', body: { rules } }),
  pageEvents: (sessionId) => request(`/api/session/${sessionId}/page-events`),

  baselines: () => request('/api/baselines'),
  deleteBaseline: (name) =>
    request(`/api/baselines/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  baselineImageUrl: (name, diff = false) =>
    `${BASE}/api/baselines/${encodeURIComponent(name)}/image${diff ? '?diff=true' : ''}`,
  visualCheck: (sessionId, body) =>
    request(`/api/session/${sessionId}/visual-check`, { method: 'POST', body }),

  startTrace: (sessionId) => request(`/api/session/${sessionId}/trace/start`, { method: 'POST' }),
  stopTrace: (sessionId, runId = null) =>
    request(`/api/session/${sessionId}/trace/stop${runId ? `?run_id=${runId}` : ''}`, { method: 'POST' }),

  screenSocketUrl: (sessionId) => `${WS_BASE}/ws/session/${sessionId}/screen`,
};

export { BASE as API_BASE };
