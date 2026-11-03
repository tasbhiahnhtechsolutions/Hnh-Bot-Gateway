// The live line to the gateway (Socket.IO over a WebSocket), plan sections 8.4 and 10.
//
// No chrome.* in here: `io` (the bundled socket.io client), the timers and the auth lookup are
// passed in, so the same file runs in the service worker and in the Node unit tests.
//
// MV3 service workers sleep. What keeps the line alive: traffic on the socket at least every 30 s
// (Socket.IO ping 20 s + our heartbeat 20 s) and a chrome.alarms tick that calls ensureConnected().
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WARealtime = api;
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  const HEARTBEAT_MS = 20000;
  const REPLY_TIMEOUT_MS = 10000;

  // The gateway said no and trying again will not help until the user does something.
  const HARD_REFUSALS = new Set(['INVALID_KEY', 'INVALID_DEVICE', 'TEST_KEY_NOT_ALLOWED', 'NUMBER_MISMATCH', 'VENDOR_SUSPENDED']);
  // Somebody else has the line, or we are being throttled: wait and try again.
  const SOFT_REFUSALS = { SESSION_ALREADY_ACTIVE: 30000, RATE_LIMITED: 60000 };

  function refusalCode(err) {
    const raw = (err && (err.data && err.data.message ? err.data.message : err.message)) || '';
    const match = String(raw).match(/[A-Z][A-Z_]{3,40}/);
    return match ? match[0] : '';
  }

  function createRealtime(deps) {
    const {
      io,
      url,
      getAuth, // () => Promise<{apiKey, deviceId, waNumber, version}>
      onJob, // (job) => void
      onConnected = () => {},
      onRefused = () => {}, // (code) => void: a hard refusal
      getHeartbeat = () => ({}),
      setTimer = setTimeout,
      clearTimer = clearTimeout,
      setRepeat = setInterval,
      clearRepeat = clearInterval,
      log = () => {},
    } = deps;

    let socket = null;
    let wanted = false;
    let heartbeat = null;
    let retry = null;
    const state = { status: 'off', refusal: '' };

    function stopHeartbeat() {
      if (heartbeat) clearRepeat(heartbeat);
      heartbeat = null;
    }

    function scheduleRetry(ms) {
      clearTimer(retry);
      retry = setTimer(() => {
        retry = null;
        ensureConnected();
      }, ms);
    }

    function sendHeartbeat() {
      if (!socket || !socket.connected) return;
      socket.timeout(REPLY_TIMEOUT_MS).emit('heartbeat', getHeartbeat(), (err, reply) => {
        if (!err && reply && reply.ok === false && reply.error === 'SESSION_LOST') {
          log('realtime_session_lost', {});
          socket.disconnect(); // the gateway dropped us: reconnect from scratch
          state.status = 'offline';
          scheduleRetry(3000);
        }
      });
    }

    function build() {
      socket = io(url, {
        transports: ['websocket'], // long-polling does not work inside a service worker
        autoConnect: false,
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 30000,
        randomizationFactor: 0.5, // not every plugin comes back in the same second
        auth: (cb) => {
          getAuth().then(cb, () => cb({}));
        },
      });
      // Registered once, at the top level of the worker, so a restarted worker hears jobs too.
      socket.on('job', (job) => onJob(job));
      socket.on('connect', () => {
        state.status = 'connected';
        state.refusal = '';
        stopHeartbeat();
        heartbeat = setRepeat(sendHeartbeat, HEARTBEAT_MS);
        sendHeartbeat(); // tell the gateway the WhatsApp state right away: it may be holding jobs
        onConnected();
      });
      socket.on('disconnect', (reason) => {
        stopHeartbeat();
        if (state.status === 'connected' || state.status === 'connecting') state.status = wanted ? 'offline' : 'off';
        // "io server disconnect": the client library will not retry by itself.
        if (wanted && reason === 'io server disconnect') scheduleRetry(5000);
      });
      socket.on('connect_error', (err) => {
        stopHeartbeat();
        const code = refusalCode(err);
        if (HARD_REFUSALS.has(code)) {
          state.status = 'refused';
          state.refusal = code;
          socket.disconnect(); // stops the library's reconnect loop
          onRefused(code);
        } else if (code in SOFT_REFUSALS) {
          state.status = 'busy';
          state.refusal = code;
          socket.disconnect();
          scheduleRetry(SOFT_REFUSALS[code]);
        } else {
          state.status = 'offline'; // network trouble: the library reconnects with back-off
        }
      });
    }

    function ensureConnected() {
      if (!wanted) return;
      if (!socket) build();
      if (!socket.connected && !socket.active) {
        state.status = 'connecting';
        socket.connect();
      }
    }

    // `value` is "should the line be open": key saved, a live key, not paused.
    function setWanted(value) {
      const next = Boolean(value);
      if (next === wanted) {
        if (next) ensureConnected();
        return;
      }
      wanted = next;
      if (wanted) {
        state.refusal = '';
        ensureConnected();
      } else {
        clearTimer(retry);
        stopHeartbeat();
        if (socket) socket.disconnect();
        state.status = 'off';
      }
    }

    // A new key or a user "resume": forget the old refusal and connect again.
    function reconnectNow() {
      if (!wanted) return;
      state.refusal = '';
      if (socket && (socket.connected || socket.active)) socket.disconnect();
      ensureConnected();
    }

    function emit(event, payload) {
      return new Promise((resolve, reject) => {
        if (!socket || !socket.connected) {
          reject(new Error('not connected'));
          return;
        }
        socket.timeout(REPLY_TIMEOUT_MS).emit(event, payload, (err, reply) => (err ? reject(err) : resolve(reply)));
      });
    }

    return {
      setWanted,
      ensureConnected,
      reconnectNow,
      emit,
      sendHeartbeat,
      isConnected: () => Boolean(socket && socket.connected),
      state: () => ({ ...state }),
    };
  }

  return { createRealtime, refusalCode, HARD_REFUSALS, SOFT_REFUSALS, HEARTBEAT_MS };
});
