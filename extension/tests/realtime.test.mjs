// The live line: connect rules, refusals, heartbeat, reconnect (plan 8.4, 10).
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const { createRealtime, refusalCode, HEARTBEAT_MS } = require('../realtime.js');

function fakeIo() {
  const sockets = [];
  const io = (url, options) => {
    const handlers = {};
    const socket = {
      url,
      options,
      connected: false,
      active: false,
      emitted: [],
      connectCalls: 0,
      disconnectCalls: 0,
      on: (event, fn) => {
        handlers[event] = fn;
      },
      connect() {
        socket.connectCalls += 1;
        socket.active = true;
      },
      disconnect() {
        socket.disconnectCalls += 1;
        socket.connected = false;
        socket.active = false;
      },
      timeout: () => ({
        emit: (event, payload, callback) => {
          socket.emitted.push({ event, payload, callback });
        },
      }),
      // test controls
      fire: (event, ...args) => handlers[event]?.(...args),
      open() {
        socket.connected = true;
        socket.active = true;
        handlers.connect?.();
      },
    };
    sockets.push(socket);
    return socket;
  };
  return { io, sockets };
}

function setup(overrides = {}) {
  const { io, sockets } = fakeIo();
  const timers = [];
  const repeats = [];
  const events = { jobs: [], connected: 0, refused: [] };
  const rt = createRealtime({
    io,
    url: 'http://gateway.test',
    getAuth: async () => ({ apiKey: 'wab_live_x_y', deviceId: 'dev-1', waNumber: '923001234567', version: '2.0.0' }),
    onJob: (job) => events.jobs.push(job),
    onConnected: () => {
      events.connected += 1;
    },
    onRefused: (code) => events.refused.push(code),
    getHeartbeat: () => ({ waState: 'connected', queueLength: 0, sendingJobId: null }),
    setTimer: (fn, ms) => {
      const timer = { fn, ms, cleared: false };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => {
      if (timer) timer.cleared = true;
    },
    setRepeat: (fn, ms) => {
      const repeat = { fn, ms, cleared: false };
      repeats.push(repeat);
      return repeat;
    },
    clearRepeat: (repeat) => {
      if (repeat) repeat.cleared = true;
    },
    ...overrides,
  });
  return { rt, sockets, timers, repeats, events };
}

test('nothing connects until the line is wanted', () => {
  const { rt, sockets } = setup();
  assert.equal(sockets.length, 0);
  rt.setWanted(true);
  assert.equal(sockets.length, 1);
  assert.equal(sockets[0].connectCalls, 1);
});

test('only the websocket transport is used (a service worker cannot long-poll)', () => {
  const { rt, sockets } = setup();
  rt.setWanted(true);
  assert.deepEqual(sockets[0].options.transports, ['websocket']);
  assert.equal(sockets[0].options.reconnection, true);
  assert.equal(sockets[0].options.randomizationFactor, 0.5);
  assert.equal(sockets[0].url, 'http://gateway.test');
});

test('the auth is read fresh at every connect', async () => {
  const { rt, sockets } = setup();
  rt.setWanted(true);
  const auth = await new Promise((resolve) => sockets[0].options.auth(resolve));
  assert.deepEqual(auth, { apiKey: 'wab_live_x_y', deviceId: 'dev-1', waNumber: '923001234567', version: '2.0.0' });
});

test('jobs are handed to the engine from the first moment', () => {
  const { rt, sockets, events } = setup();
  rt.setWanted(true);
  sockets[0].fire('job', { jobId: 'job_0123456789abcdef' });
  assert.deepEqual(events.jobs, [{ jobId: 'job_0123456789abcdef' }]);
});

test('connecting sends a heartbeat at once and then every 20 seconds', () => {
  const { rt, sockets, repeats, events } = setup();
  rt.setWanted(true);
  sockets[0].open();
  assert.equal(rt.state().status, 'connected');
  assert.equal(events.connected, 1);
  assert.equal(sockets[0].emitted.length, 1);
  assert.deepEqual(sockets[0].emitted[0], {
    event: 'heartbeat',
    payload: { waState: 'connected', queueLength: 0, sendingJobId: null },
    callback: sockets[0].emitted[0].callback,
  });
  assert.equal(repeats[0].ms, HEARTBEAT_MS);
  repeats[0].fn();
  assert.equal(sockets[0].emitted.length, 2);
});

test('the heartbeat stops when the line drops', () => {
  const { rt, sockets, repeats } = setup();
  rt.setWanted(true);
  sockets[0].open();
  sockets[0].connected = false;
  sockets[0].fire('disconnect', 'transport close');
  assert.equal(repeats[0].cleared, true);
  assert.equal(rt.state().status, 'offline');
});

test('a hard refusal stops everything and tells the extension', () => {
  for (const code of ['INVALID_KEY', 'NUMBER_MISMATCH', 'VENDOR_SUSPENDED', 'TEST_KEY_NOT_ALLOWED', 'INVALID_DEVICE']) {
    const { rt, sockets, events, timers } = setup();
    rt.setWanted(true);
    sockets[0].fire('connect_error', Object.assign(new Error(code), { data: { message: code } }));
    assert.deepEqual(events.refused, [code]);
    assert.equal(rt.state().status, 'refused');
    assert.equal(rt.state().refusal, code);
    assert.equal(sockets[0].disconnectCalls, 1, 'the library must not keep retrying');
    assert.equal(timers.length, 0, 'and we do not retry either');
  }
});

test('another device holds the line: wait 30 seconds and try again, no pause', () => {
  const { rt, sockets, timers, events } = setup();
  rt.setWanted(true);
  sockets[0].fire('connect_error', new Error('SESSION_ALREADY_ACTIVE'));
  assert.equal(rt.state().status, 'busy');
  assert.deepEqual(events.refused, []);
  const retry = timers.find((t) => !t.cleared);
  assert.equal(retry.ms, 30000);
  const before = sockets[0].connectCalls;
  retry.fn();
  assert.equal(sockets[0].connectCalls, before + 1);
});

test('rate limited: wait a minute', () => {
  const { rt, sockets, timers } = setup();
  rt.setWanted(true);
  sockets[0].fire('connect_error', new Error('RATE_LIMITED'));
  assert.equal(timers.find((t) => !t.cleared).ms, 60000);
  assert.equal(rt.state().refusal, 'RATE_LIMITED');
});

test('plain network trouble is left to the library (back-off reconnect)', () => {
  const { rt, sockets, events, timers } = setup();
  rt.setWanted(true);
  sockets[0].fire('connect_error', new Error('websocket error'));
  assert.equal(rt.state().status, 'offline');
  assert.deepEqual(events.refused, []);
  assert.equal(sockets[0].disconnectCalls, 0);
  assert.equal(timers.length, 0);
});

test('"io server disconnect" does not reconnect by itself, so we do', () => {
  const { rt, sockets, timers } = setup();
  rt.setWanted(true);
  sockets[0].open();
  sockets[0].connected = false;
  sockets[0].active = false;
  sockets[0].fire('disconnect', 'io server disconnect');
  const retry = timers.find((t) => !t.cleared);
  assert.equal(retry.ms, 5000);
  retry.fn();
  assert.equal(sockets[0].connectCalls, 2);
});

test('SESSION_LOST in a heartbeat answer: drop the line and reconnect soon', () => {
  const { rt, sockets, timers } = setup();
  rt.setWanted(true);
  sockets[0].open();
  sockets[0].emitted[0].callback(null, { ok: false, error: 'SESSION_LOST' });
  assert.equal(sockets[0].disconnectCalls, 1);
  assert.equal(timers.find((t) => !t.cleared).ms, 3000);
});

test('a heartbeat that times out does not disturb the line (the library decides)', () => {
  const { rt, sockets } = setup();
  rt.setWanted(true);
  sockets[0].open();
  sockets[0].emitted[0].callback(new Error('timeout'));
  assert.equal(sockets[0].disconnectCalls, 0);
});

test('turning the line off disconnects and stops the heartbeat; turning on again reconnects', () => {
  const { rt, sockets, repeats } = setup();
  rt.setWanted(true);
  sockets[0].open();
  rt.setWanted(false);
  assert.equal(sockets[0].disconnectCalls, 1);
  assert.equal(repeats[0].cleared, true);
  assert.equal(rt.state().status, 'off');
  rt.setWanted(true);
  assert.equal(sockets.length, 1, 'the same socket object is reused');
  assert.equal(sockets[0].connectCalls, 2);
});

test('ensureConnected (the alarm tick) reconnects a dead line but leaves a working one alone', () => {
  const { rt, sockets } = setup();
  rt.setWanted(true);
  sockets[0].open();
  rt.ensureConnected();
  assert.equal(sockets[0].connectCalls, 1);
  sockets[0].connected = false;
  sockets[0].active = false;
  rt.ensureConnected();
  assert.equal(sockets[0].connectCalls, 2);
  sockets[0].active = true; // the library is already trying
  rt.ensureConnected();
  assert.equal(sockets[0].connectCalls, 2);
});

test('ensureConnected does nothing while the line is not wanted', () => {
  const { rt, sockets } = setup();
  rt.ensureConnected();
  assert.equal(sockets.length, 0);
});

test('emit goes through the socket with a timeout and resolves with the server reply', async () => {
  const { rt, sockets } = setup();
  rt.setWanted(true);
  sockets[0].open();
  const pending = rt.emit('job_ack', { jobId: 'job_x' });
  const call = sockets[0].emitted.at(-1);
  assert.equal(call.event, 'job_ack');
  call.callback(null, { ok: true });
  assert.deepEqual(await pending, { ok: true });
});

test('emit rejects when there is no line or no answer', async () => {
  const { rt, sockets } = setup();
  await assert.rejects(rt.emit('job_ack', {}), /not connected/);
  rt.setWanted(true);
  sockets[0].open();
  const pending = rt.emit('job_result', {});
  sockets[0].emitted.at(-1).callback(new Error('operation has timed out'));
  await assert.rejects(pending, /timed out/);
});

test('the refusal code is read from the error however the library shapes it', () => {
  assert.equal(refusalCode(new Error('INVALID_KEY')), 'INVALID_KEY');
  assert.equal(refusalCode(Object.assign(new Error('x'), { data: { message: 'NUMBER_MISMATCH' } })), 'NUMBER_MISMATCH');
  assert.equal(refusalCode(new Error('xhr poll error')), '');
  assert.equal(refusalCode(undefined), '');
});

test('reconnectNow forgets an old refusal and connects again', () => {
  const { rt, sockets } = setup();
  rt.setWanted(true);
  sockets[0].fire('connect_error', new Error('INVALID_KEY'));
  rt.reconnectNow();
  assert.equal(rt.state().refusal, '');
  assert.equal(sockets[0].connectCalls, 2);
});
