// How background.js wires the live line and the send engine together (against fake sockets).
import assert from 'node:assert/strict';
import test from 'node:test';

import { flush, jsonResponse, makeBackground } from './helpers.mjs';

const KEY = 'wab_live_k3f9a1xz_SUPERSECRETVALUE';
const VENDOR = { vendorId: 'v1', businessName: 'Ali Boutique', whatsappNumber: '923001112222', keyKind: 'live', inboundMode: 'forward', contractVersion: 1 };
const JOB = { jobId: 'job_0123456789abcdef', attempt: 1, to: '923001234567', message: 'Aap ka OTP 482913 hai.', type: 'otp' };
const plain = (value) => JSON.parse(JSON.stringify(value)); // objects made inside the vm have another prototype
const WA_TAB = { sender: { tab: { id: 7 } } };

function fakeIo() {
  const sockets = [];
  const io = (url, options) => {
    const handlers = {};
    const socket = {
      url,
      options,
      connected: false,
      active: false,
      connectCalls: 0,
      disconnectCalls: 0,
      emitted: [],
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
          socket.emitted.push({ event, payload });
          // The gateway answers "ok, applied" to everything.
          callback(null, { ok: true, outcome: 'applied' });
        },
      }),
      fire: (event, ...args) => handlers[event]?.(...args),
      open: () => {
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

const liveStorage = (extra = {}) => ({
  apiKey: KEY,
  deviceId: 'device-123',
  vendor: VENDOR,
  waNumber: '923001112222',
  autoReplyEnabled: true,
  ...extra,
});

async function boot(storage, extra = {}) {
  const { io, sockets } = fakeIo();
  const bg = makeBackground({ storage, io, fetchImpl: async () => jsonResponse(200, VENDOR), ...extra });
  await flush();
  await flush();
  return { bg, sockets };
}

test('a live key on the vendor\'s own WhatsApp number opens the line, websocket only, auth read fresh', async () => {
  const { sockets } = await boot(liveStorage());
  assert.equal(sockets.length, 1);
  assert.equal(sockets[0].connectCalls, 1);
  assert.deepEqual(plain(sockets[0].options.transports), ['websocket']);
  assert.equal(sockets[0].url, 'http://localhost:8000');
  const auth = await new Promise((resolve) => sockets[0].options.auth(resolve));
  assert.deepEqual(plain(auth), { apiKey: KEY, deviceId: 'device-123', waNumber: '923001112222', version: '2.0.0' });
});

test('no connection without a key, for a test key, on someone else\'s number, or while paused', async () => {
  const cases = {
    'no key': liveStorage({ apiKey: '' }),
    'test key': liveStorage({ vendor: { ...VENDOR, keyKind: 'test' } }),
    'other number': liveStorage({ waNumber: '923009990000' }),
    'number unknown yet': liveStorage({ waNumber: undefined }),
    'paused': liveStorage({ paused: { code: 'INVALID_KEY', message: 'x' } }),
    'no vendor info yet': liveStorage({ vendor: undefined }),
  };
  for (const [name, storage] of Object.entries(cases)) {
    const { sockets } = await boot(storage);
    const connected = sockets.some((s) => s.connectCalls > 0);
    assert.equal(connected, false, `${name}: must not connect`);
  }
});

test('the line opens as soon as the missing piece arrives (WhatsApp number learned later)', async () => {
  const { bg, sockets } = await boot(liveStorage({ waNumber: undefined }));
  assert.ok(!sockets.some((s) => s.connectCalls > 0));
  await bg.send({ type: 'WA_LOGGER_WA_INFO', waNumber: '923001112222' }, WA_TAB.sender);
  await flush();
  assert.ok(sockets.some((s) => s.connectCalls > 0));
});

test('a hard refusal pauses the plugin (and the line stays down)', async () => {
  const { bg, sockets } = await boot(liveStorage());
  sockets[0].fire('connect_error', new Error('NUMBER_MISMATCH'));
  await flush();
  await flush();
  assert.equal(bg.store.paused?.code, 'NUMBER_MISMATCH');
  assert.equal(sockets[0].disconnectCalls >= 1, true);
});

test('the keep-alive alarm exists and re-opens a lost line', async () => {
  const { bg, sockets } = await boot(liveStorage());
  assert.deepEqual(plain(bg.alarms.created[0].info), { periodInMinutes: 0.5 });
  sockets[0].open();
  sockets[0].connected = false;
  sockets[0].active = false;
  const before = sockets[0].connectCalls;
  bg.alarms.handler({ name: bg.alarms.created[0].name });
  await flush();
  await flush();
  assert.equal(sockets[0].connectCalls, before + 1);
  bg.alarms.handler({ name: 'something-else' });
  await flush();
  assert.equal(sockets[0].connectCalls, before + 1);
});

test('a job from the gateway: acknowledged, sent to the WhatsApp tab, result reported', async () => {
  const { bg, sockets } = await boot(liveStorage());
  bg.store.waState = 'connected';
  bg.store.waStateAt = Date.now();
  sockets[0].open();
  sockets[0].fire('job', JOB);
  await flush();
  await flush();

  const acks = sockets[0].emitted.filter((e) => e.event === 'job_ack');
  assert.deepEqual(plain(acks[0].payload), { jobId: JOB.jobId, attempt: 1, stage: 'started' });
  const command = bg.sentToTabs.find((m) => m.message.type === 'OUTBOUND_COMMAND');
  assert.equal(command.tabId, 7);
  assert.deepEqual(plain(command.message.command), { type: 'SEND', job: { jobId: JOB.jobId, attempt: 1, to: JOB.to, message: JOB.message }, resume: false });

  await bg.send({ type: 'WA_GATEWAY_OUTBOUND_EVENT', event: { jobId: JOB.jobId, attempt: 1, event: 'sending' } }, WA_TAB.sender);
  await bg.send({ type: 'WA_GATEWAY_OUTBOUND_EVENT', event: { jobId: JOB.jobId, attempt: 1, event: 'result', status: 'sent', waAck: 1 } }, WA_TAB.sender);
  await flush();
  const results = sockets[0].emitted.filter((e) => e.event === 'job_result');
  assert.deepEqual(plain(results[0].payload), { jobId: JOB.jobId, attempt: 1, status: 'sent', waAck: 1 });
});

test('a job while WhatsApp is not loaded fails with WA_NOT_READY and sends nothing', async () => {
  const { bg, sockets } = await boot(liveStorage()); // no waState stored: the tab never reported
  sockets[0].open();
  sockets[0].fire('job', JOB);
  await flush();
  await flush();
  const results = sockets[0].emitted.filter((e) => e.event === 'job_result');
  assert.deepEqual(plain(results[0].payload), { jobId: JOB.jobId, attempt: 1, status: 'failed', errorCode: 'WA_NOT_READY' });
  assert.ok(!bg.sentToTabs.some((m) => m.message.type === 'OUTBOUND_COMMAND'));
});

test('a stale WhatsApp state (tab gone) counts as not loaded', async () => {
  const { bg, sockets } = await boot(liveStorage({ waState: 'connected', waStateAt: Date.now() - 60000 }));
  sockets[0].open();
  sockets[0].fire('job', JOB);
  await flush();
  await flush();
  assert.equal(sockets[0].emitted.filter((e) => e.event === 'job_result')[0].payload.errorCode, 'WA_NOT_READY');
  assert.equal((await bg.send({ type: 'GET_STATUS' })).waState, 'not_loaded');
});

test('the page reports WhatsApp state: stored, and a change goes to the gateway at once', async () => {
  const { bg, sockets } = await boot(liveStorage());
  sockets[0].open();
  const heartbeatsBefore = sockets[0].emitted.filter((e) => e.event === 'heartbeat').length;
  await bg.send({ type: 'WA_GATEWAY_WA_STATE', state: 'connected' }, WA_TAB.sender);
  assert.equal(bg.store.waState, 'connected');
  const heartbeats = sockets[0].emitted.filter((e) => e.event === 'heartbeat');
  assert.equal(heartbeats.length, heartbeatsBefore + 1);
  assert.equal(heartbeats.at(-1).payload.waState, 'connected');
  assert.equal((await bg.send({ type: 'GET_STATUS' })).waState, 'connected');
});

test('garbage WhatsApp states are not stored', async () => {
  const { bg } = await boot(liveStorage());
  for (const bad of ['', 'x', '<script>', 'a'.repeat(40), 5, null]) {
    await bg.send({ type: 'WA_GATEWAY_WA_STATE', state: bad }, WA_TAB.sender);
  }
  assert.equal(bg.store.waState, undefined);
});

test('only a WhatsApp tab can feed the outbound engine, never a web page without a tab', async () => {
  const { bg } = await boot(liveStorage());
  await assert.rejects(bg.send({ type: 'WA_GATEWAY_OUTBOUND_EVENT', event: { jobId: JOB.jobId, attempt: 1, event: 'result', status: 'sent' } }, {}), /not handled/);
  await assert.rejects(bg.send({ type: 'WA_GATEWAY_WA_STATE', state: 'connected' }, {}), /not handled/);
});

test('a reload of the WhatsApp page tells the engine the tab is ready', async () => {
  const { bg } = await boot(liveStorage());
  const reply = await bg.send({ type: 'WA_GATEWAY_TAB_READY' }, WA_TAB.sender);
  assert.deepEqual(reply, { ok: true });
});

test('GET_STATUS shows the line, WhatsApp and the queue to the popup', async () => {
  const { bg, sockets } = await boot(liveStorage());
  sockets[0].open();
  const status = await bg.send({ type: 'GET_STATUS' });
  assert.equal(status.realtime.status, 'connected');
  assert.equal(status.waState, 'not_loaded');
  assert.deepEqual(Object.keys(status.outbound).sort(), ['lastResult', 'outboxLength', 'queueLength', 'sendingJobId']);
  assert.ok(!JSON.stringify(status).includes('SUPERSECRET'));
});

test('the vendor\'s message text never reaches the status shown in the popup', async () => {
  const { bg, sockets } = await boot(liveStorage({ waState: 'connected', waStateAt: Date.now() }));
  sockets[0].open();
  sockets[0].fire('job', JOB);
  await flush();
  await flush();
  assert.ok(!JSON.stringify(await bg.send({ type: 'GET_STATUS' })).includes('482913'));
});
