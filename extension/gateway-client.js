// Pure helpers for talking to the vendor gateway. No chrome.* and no network
// access in here, so the same file runs in the service worker (importScripts),
// in the popup (<script>) and in the Node unit tests (require).
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WAGatewayClient = api;
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  const CONTRACT_VERSION = '1';
  // The backend waits up to 30s for the vendor bot (bot_timeout_seconds). The
  // plugin must wait longer than that or it gives up before the gateway does.
  const AI_REPLY_TIMEOUT_MS = 35000;
  const HEALTH_TIMEOUT_MS = 3000;
  const DEFAULT_RATE_LIMIT_PAUSE_S = 60;

  function digitsOnly(value) {
    return String(value == null ? '' : value).replace(/\D/g, '');
  }

  function joinUrl(base, path) {
    return String(base).replace(/\/+$/, '') + path;
  }

  // WhatsApp numbers are compared as digits only (no +, spaces or dashes).
  function numbersMatch(a, b) {
    const x = digitsOnly(a);
    const y = digitsOnly(b);
    return Boolean(x) && x === y;
  }

  function formatPhone(value) {
    const digits = digitsOnly(value);
    return digits ? '+' + digits : '';
  }

  function maskKey(apiKey) {
    // Shows only the public part (wab_live_<prefix>), never the secret part.
    const parts = String(apiKey || '').split('_');
    if (parts.length >= 4 && parts[0] === 'wab') return `${parts[0]}_${parts[1]}_${parts[2]}_\u2022\u2022\u2022\u2022`;
    return apiKey ? '\u2022\u2022\u2022\u2022' : '';
  }

  function buildAiReplyHeaders({ apiKey, deviceId, waNumber, requestId }) {
    const headers = {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey,
      'X-Request-Id': requestId,
      'X-Contract-Version': CONTRACT_VERSION,
    };
    if (deviceId) headers['X-Device-Id'] = deviceId;
    const wa = digitsOnly(waNumber);
    if (wa) headers['X-WA-Number'] = wa;
    return headers;
  }

  function authHeaders(apiKey) {
    return { 'X-API-Key': apiKey, 'X-Contract-Version': CONTRACT_VERSION };
  }

  // Turns a non-2xx gateway answer into what the plugin should do:
  //   lastError: text for the popup, severity: badge colour (red|yellow),
  //   pause: null, or {code, message, until?} (no more requests until fixed).
  // The plugin never retries a request and never sends an error to the customer.
  function classifyHttpFailure({ status, errorCode, retryAfterSeconds, now }) {
    const code = errorCode || `HTTP_${status}`;
    if (status === 401) {
      return {
        severity: 'red',
        lastError: 'API key rejected (invalid or revoked)',
        pause: { code: 'INVALID_KEY', message: 'API key rejected. Enter a valid key and save.' },
      };
    }
    if (status === 403) {
      const messages = {
        VENDOR_SUSPENDED: 'This vendor account is suspended. Contact support.',
        NUMBER_MISMATCH: 'This WhatsApp number does not belong to this API key.',
      };
      const message = messages[code] || 'Access denied by the gateway.';
      return { severity: 'red', lastError: message, pause: { code, message } };
    }
    if (status === 429) {
      const seconds = retryAfterSeconds > 0 ? retryAfterSeconds : DEFAULT_RATE_LIMIT_PAUSE_S;
      const message = `Rate limited by the gateway. Waiting ${seconds}s.`;
      return {
        severity: 'yellow',
        lastError: message,
        pause: { code: 'RATE_LIMITED', message, until: (now ?? Date.now()) + seconds * 1000 },
      };
    }
    if (status === 413) {
      return { severity: 'yellow', lastError: 'Message was too large for the gateway (413).', pause: null };
    }
    if (status >= 500) {
      return { severity: 'yellow', lastError: `Gateway or bot error ${status} (${code}). No reply sent.`, pause: null };
    }
    return { severity: 'yellow', lastError: `Gateway rejected the request (${status} ${code}).`, pause: null };
  }

  // The live line (Socket.IO) refused the plugin with a code. Same shape as classifyHttpFailure.
  function classifyRefusal(code) {
    const messages = {
      INVALID_KEY: 'API key rejected (invalid or revoked)',
      VENDOR_SUSPENDED: 'This vendor account is suspended. Contact support.',
      NUMBER_MISMATCH: 'This WhatsApp number does not belong to this API key.',
      TEST_KEY_NOT_ALLOWED: 'A test key cannot receive messages. Use the live key.',
      INVALID_DEVICE: 'The gateway did not accept this installation. Reinstall the extension.',
    };
    const message = messages[code] || 'The gateway refused the live connection.';
    return { severity: 'red', lastError: message, pause: { code, message } };
  }

  function classifyNetworkFailure(err) {
    if (err && err.name === 'AbortError') {
      return { severity: 'yellow', lastError: `No answer from the gateway within ${AI_REPLY_TIMEOUT_MS / 1000}s. No reply sent.`, pause: null };
    }
    return { severity: 'yellow', lastError: 'Gateway unreachable. No reply sent.', pause: null };
  }

  // Badge: green = working, yellow = temporary problem, red = needs the user,
  // grey = switched off by the user.
  function badgeFor({ hasKey, paused, autoReplyEnabled, lastErrorSeverity }) {
    if (!hasKey) return { text: '!', color: '#d93025' };
    if (paused) return { text: '!', color: '#d93025' };
    if (!autoReplyEnabled) return { text: 'OFF', color: '#6b7280' };
    if (lastErrorSeverity === 'yellow') return { text: '...', color: '#f9ab00' };
    if (lastErrorSeverity === 'red') return { text: '!', color: '#d93025' };
    return { text: 'ON', color: '#188038' };
  }

  function parseRetryAfter(value) {
    const n = parseInt(value, 10);
    return Number.isFinite(n) && n > 0 ? Math.min(n, 3600) : 0;
  }

  // Extracts the gateway error code from {"error":{"code","message"}}; never throws.
  function errorCodeFromBody(text) {
    try {
      const code = JSON.parse(text)?.error?.code;
      return typeof code === 'string' && /^[A-Z_]{3,40}$/.test(code) ? code : '';
    } catch (_) {
      return '';
    }
  }

  return {
    CONTRACT_VERSION,
    AI_REPLY_TIMEOUT_MS,
    HEALTH_TIMEOUT_MS,
    digitsOnly,
    joinUrl,
    numbersMatch,
    formatPhone,
    maskKey,
    buildAiReplyHeaders,
    authHeaders,
    classifyHttpFailure,
    classifyNetworkFailure,
    classifyRefusal,
    badgeFor,
    parseRetryAfter,
    errorCodeFromBody,
  };
});
