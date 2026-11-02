/**
 * WhatsApp Auto-Reply Gateway - Background Service Worker.
 *
 * Only talks to the vendor gateway (GATEWAY_BASE_URL from config.js):
 *   POST /v1/ai-reply      customer burst in, reply bubbles out
 *   GET  /v1/plugin/whoami check an API key when the user saves it
 *   GET  /health           "Backend: Online" in the popup
 * Rules (VENDOR_GATEWAY_PLAN.md section 10): no retries, nothing is ever sent
 * to the customer on an error, 401/403 pause the plugin, no key = no request.
 * All state lives in chrome.storage.local because the service worker can be
 * stopped at any time. The API key is never logged.
 */

importScripts('config.js', 'gateway-client.js');

const GW = self.WAGatewayClient;
const GATEWAY_BASE_URL = self.WA_GATEWAY_CONFIG.GATEWAY_BASE_URL;
const AI_REPLY_URL = GW.joinUrl(GATEWAY_BASE_URL, '/v1/ai-reply');
const WHOAMI_URL = GW.joinUrl(GATEWAY_BASE_URL, '/v1/plugin/whoami');
const HEALTH_URL = GW.joinUrl(GATEWAY_BASE_URL, '/health');
const WHOAMI_TIMEOUT_MS = 10000;
const WA_TAB_QUERY = { url: '*://web.whatsapp.com/*' };
const REQUEST_ID_RE = /^[A-Za-z0-9._-]{1,64}$/;
const STATE_KEYS = ['apiKey', 'deviceId', 'autoReplyEnabled', 'vendor', 'paused', 'lastError', 'waNumber'];

// ---------------------------------------------------------------------------
// State (chrome.storage.local)
// ---------------------------------------------------------------------------

async function getState() {
  const raw = await chrome.storage.local.get(STATE_KEYS);
  return {
    apiKey: typeof raw.apiKey === 'string' ? raw.apiKey : '',
    deviceId: raw.deviceId || '',
    autoReplyEnabled: raw.autoReplyEnabled !== false,
    vendor: raw.vendor || null,
    paused: raw.paused || null,
    lastError: raw.lastError || null,
    waNumber: raw.waNumber || '',
  };
}

async function ensureDeviceId() {
  const { deviceId } = await chrome.storage.local.get(['deviceId']);
  if (deviceId) return deviceId;
  const created = crypto.randomUUID();
  await chrome.storage.local.set({ deviceId: created });
  return created;
}

// A rate-limit pause ("until") ends by itself; other pauses need the user.
function currentPause(state, now = Date.now()) {
  const paused = state.paused;
  if (!paused) return null;
  if (paused.until && now >= paused.until) return null;
  return paused;
}

function hardPaused(state) {
  const paused = currentPause(state);
  return paused && !paused.until ? paused : null;
}

// What the page script (injected.js) is told: should it react to messages?
function isActive(state) {
  return Boolean(state.apiKey) && state.autoReplyEnabled && !hardPaused(state);
}

async function recordError(failure) {
  const updates = { lastError: { text: failure.lastError, severity: failure.severity, at: Date.now() } };
  if (failure.pause) updates.paused = failure.pause;
  await chrome.storage.local.set(updates);
}

async function clearErrors() {
  await chrome.storage.local.remove(['lastError', 'paused']);
}

// ---------------------------------------------------------------------------
// Badge + page control
// ---------------------------------------------------------------------------

async function refreshBadge() {
  const state = await getState();
  const pause = currentPause(state);
  const badge = GW.badgeFor({
    hasKey: Boolean(state.apiKey),
    paused: Boolean(hardPaused(state)),
    autoReplyEnabled: state.autoReplyEnabled,
    lastErrorSeverity: pause && pause.until ? 'yellow' : state.lastError?.severity,
  });
  await chrome.action.setBadgeText({ text: badge.text });
  await chrome.action.setBadgeBackgroundColor({ color: badge.color });
}

async function broadcastControl() {
  const state = await getState();
  const message = { type: 'CONTROL', active: isActive(state) };
  const tabs = await chrome.tabs.query(WA_TAB_QUERY);
  await Promise.all(tabs.map((tab) => chrome.tabs.sendMessage(tab.id, message).catch(() => undefined)));
}

async function refreshUi() {
  try {
    await refreshBadge();
    await broadcastControl();
  } catch (err) {
    console.warn('[WA Gateway SW] UI refresh failed:', err.message);
  }
}

// ---------------------------------------------------------------------------
// Network helpers
// ---------------------------------------------------------------------------

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal, redirect: 'error' });
  } finally {
    clearTimeout(timer);
  }
}

async function failureFromResponse(response) {
  const body = await response.text().catch(() => '');
  return GW.classifyHttpFailure({
    status: response.status,
    errorCode: GW.errorCodeFromBody(body),
    retryAfterSeconds: GW.parseRetryAfter(response.headers.get('Retry-After')),
    now: Date.now(),
  });
}

async function sendRepliesToTab(senderTabId, data) {
  const message = { type: 'WA_LOGGER_AI_RESPONSE', data };
  if (senderTabId) {
    try {
      await chrome.tabs.sendMessage(senderTabId, message);
      return;
    } catch (_) {
      // The tab was closed or reloaded: fall through to the other WhatsApp tabs.
      // A tab without a matching in-flight request ignores the reply.
    }
  }
  const tabs = await chrome.tabs.query(WA_TAB_QUERY);
  await Promise.all(tabs.map((tab) => chrome.tabs.sendMessage(tab.id, message).catch(() => undefined)));
}

// ---------------------------------------------------------------------------
// AI reply request (content.js -> here -> gateway)
// ---------------------------------------------------------------------------

async function handleAiRequest(message, sender) {
  const payload = message.payload;
  if (!payload || typeof payload.chatId !== 'string') return { success: false, reason: 'bad_request' };

  const state = await getState();
  if (!state.apiKey) return { success: false, reason: 'no_api_key' };
  if (!state.autoReplyEnabled) return { success: false, reason: 'auto_reply_disabled' };
  const pause = currentPause(state);
  if (pause) return { success: false, reason: 'paused', code: pause.code };

  const requestId = REQUEST_ID_RE.test(message.requestId || '') ? message.requestId : crypto.randomUUID();
  const waNumber = GW.digitsOnly(message.waNumber) || state.waNumber;
  if (waNumber && waNumber !== state.waNumber) await chrome.storage.local.set({ waNumber });
  const deviceId = state.deviceId || (await ensureDeviceId());

  let response;
  try {
    response = await fetchWithTimeout(
      AI_REPLY_URL,
      {
        method: 'POST',
        headers: GW.buildAiReplyHeaders({ apiKey: state.apiKey, deviceId, waNumber, requestId }),
        body: JSON.stringify(payload),
      },
      GW.AI_REPLY_TIMEOUT_MS,
    );
  } catch (err) {
    console.warn(`[WA Gateway SW] ai-reply failed (${err.name}) request=${requestId}`);
    await recordError(GW.classifyNetworkFailure(err));
    return { success: false, reason: err.name === 'AbortError' ? 'timeout' : 'network' };
  }

  if (!response.ok) {
    console.warn(`[WA Gateway SW] ai-reply HTTP ${response.status} request=${requestId}`);
    await recordError(await failureFromResponse(response));
    return { success: false, reason: 'http', status: response.status };
  }

  let replies;
  try {
    const data = await response.json();
    replies = Array.isArray(data.replies) ? data.replies.filter((r) => typeof r === 'string' && r.trim()) : null;
  } catch (_) {
    replies = null;
  }
  if (replies === null) {
    await recordError({ severity: 'yellow', lastError: 'Gateway sent an unreadable answer. No reply sent.', pause: null });
    return { success: false, reason: 'invalid_response' };
  }

  if (state.lastError || state.paused) await clearErrors();
  if (replies.length > 0) {
    await sendRepliesToTab(sender.tab?.id, { chatId: payload.chatId, replies, requestId });
  }
  return { success: true, repliesCount: replies.length };
}

// ---------------------------------------------------------------------------
// Popup requests
// ---------------------------------------------------------------------------

async function handleGetStatus() {
  const state = await getState();
  let backend = 'offline';
  try {
    const response = await fetchWithTimeout(HEALTH_URL, { method: 'GET' }, GW.HEALTH_TIMEOUT_MS);
    backend = response.ok ? 'online' : 'offline';
  } catch (_) {
    backend = 'offline';
  }
  const pause = currentPause(state);
  return {
    backend,
    hasKey: Boolean(state.apiKey),
    keyHint: GW.maskKey(state.apiKey),
    vendor: state.vendor,
    autoReplyEnabled: state.autoReplyEnabled,
    paused: pause ? { code: pause.code, message: pause.message, until: pause.until || null } : null,
    lastError: state.lastError,
    waNumber: state.waNumber,
    waNumberMismatch: Boolean(state.vendor && state.waNumber && !GW.numbersMatch(state.waNumber, state.vendor.whatsappNumber)),
    gatewayEnv: self.WA_GATEWAY_CONFIG.env,
  };
}

async function handleSaveKey(rawKey) {
  const apiKey = typeof rawKey === 'string' ? rawKey.trim() : '';
  if (!apiKey) return { ok: false, error: 'Enter an API key.' };

  let response;
  try {
    response = await fetchWithTimeout(WHOAMI_URL, { method: 'GET', headers: GW.authHeaders(apiKey) }, WHOAMI_TIMEOUT_MS);
  } catch (_) {
    return { ok: false, error: 'Backend unreachable. Try again.' };
  }

  if (response.status === 401) return { ok: false, error: 'Invalid key.' };
  if (response.status === 403) {
    const code = GW.errorCodeFromBody(await response.text().catch(() => ''));
    return { ok: false, error: code === 'VENDOR_SUSPENDED' ? 'This vendor account is suspended.' : 'Key not allowed.' };
  }
  if (!response.ok) return { ok: false, error: `Backend error (${response.status}). Try again.` };

  let who;
  try {
    who = await response.json();
  } catch (_) {
    return { ok: false, error: 'Backend sent an unreadable answer.' };
  }
  const vendor = {
    businessName: String(who.businessName || ''),
    whatsappNumber: GW.digitsOnly(who.whatsappNumber),
    keyKind: String(who.keyKind || ''),
  };
  await ensureDeviceId();
  await chrome.storage.local.set({ apiKey, vendor });
  await clearErrors();
  return { ok: true, vendor };
}

async function handleClearKey() {
  await chrome.storage.local.remove(['apiKey', 'vendor', 'paused', 'lastError']);
  return { ok: true };
}

async function handleSetAutoReply(enabled) {
  await chrome.storage.local.set({ autoReplyEnabled: Boolean(enabled) });
  return { ok: true };
}

// The user fixed something (or just wants to try again): lift the pause.
async function handleResume() {
  await clearErrors();
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

const POPUP_HANDLERS = {
  GET_STATUS: () => handleGetStatus(),
  SAVE_KEY: (message) => handleSaveKey(message.apiKey),
  CLEAR_KEY: () => handleClearKey(),
  SET_AUTO_REPLY: (message) => handleSetAutoReply(message.enabled),
  RESUME: () => handleResume(),
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== 'string') return false;

  let task;
  if (sender.tab) {
    // From content.js (a WhatsApp Web tab).
    if (message.type === 'WA_LOGGER_AI_REQUEST') task = handleAiRequest(message, sender);
    else if (message.type === 'GET_CONTROL') task = getState().then((state) => ({ active: isActive(state) }));
    else if (message.type === 'WA_LOGGER_WA_INFO') {
      const waNumber = GW.digitsOnly(message.waNumber);
      task = waNumber ? chrome.storage.local.set({ waNumber }).then(() => ({ ok: true })) : Promise.resolve({ ok: false });
    }
  } else if (POPUP_HANDLERS[message.type]) {
    // From the extension's own popup (no tab).
    task = POPUP_HANDLERS[message.type](message);
  }
  if (!task) return false;

  task.then(sendResponse).catch((err) => {
    console.warn(`[WA Gateway SW] ${message.type} failed:`, err.message);
    sendResponse({ success: false, ok: false, error: 'internal_error' });
  });
  return true;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (['apiKey', 'autoReplyEnabled', 'paused', 'lastError'].some((key) => key in changes)) refreshUi();
});

chrome.runtime.onInstalled.addListener(async () => {
  await ensureDeviceId();
  const { autoReplyEnabled } = await chrome.storage.local.get(['autoReplyEnabled']);
  if (autoReplyEnabled === undefined) await chrome.storage.local.set({ autoReplyEnabled: true });
  refreshUi();
});

chrome.runtime.onStartup.addListener(() => refreshUi());
refreshUi();
