import assert from 'node:assert/strict';
import test from 'node:test';

import { flush, jsonResponse, makeBackground } from './helpers.mjs';

const GATEWAY = 'http://localhost:8000';
const KEY = 'wab_live_k3f9a1xz_SUPERSECRETVALUE';
const TAB = { tab: { id: 7 } };
const PAYLOAD = { chatId: '923009990001@c.us', senderName: 'Ali', incomingMessages: [{ text: 'salam', timestamp: '10:00:00', media: null }], chatHistory: [] };
const aiRequest = (extra = {}) => ({ type: 'WA_LOGGER_AI_REQUEST', requestId: 'req-1', waNumber: '923001112222', payload: PAYLOAD, ...extra });
const WHO = { vendorId: 'v1', businessName: 'Ali Boutique', whatsappNumber: '923001112222', keyKind: 'live', inboundMode: 'forward', contractVersion: 1 };

const withKey = (extra = {}) => ({ apiKey: KEY, autoReplyEnabled: true, deviceId: 'device-123', ...extra });

test('ai-reply goes to the gateway with every contract header and the replies reach the tab', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(200, { replies: ['Walaikum salam', '  ', 'Kaise madad karun?'] }) });
  const result = await bg.send(aiRequest(), TAB);

  assert.deepEqual(result, { success: true, repliesCount: 2 });
  assert.equal(bg.fetchCalls.length, 1);
  const { url, options } = bg.fetchCalls[0];
  assert.equal(url, `${GATEWAY}/v1/ai-reply`);
  assert.equal(options.method, 'POST');
  assert.equal(options.headers['X-API-Key'], KEY);
  assert.equal(options.headers['X-Request-Id'], 'req-1');
  assert.equal(options.headers['X-Device-Id'], 'device-123');
  assert.equal(options.headers['X-WA-Number'], '923001112222');
  assert.equal(options.headers['X-Contract-Version'], '1');
  assert.deepEqual(JSON.parse(options.body), PAYLOAD);
  assert.equal(options.redirect, 'error');

  const delivered = bg.sentToTabs.find((m) => m.message.type === 'WA_LOGGER_AI_RESPONSE');
  assert.equal(delivered.tabId, 7);
  assert.deepEqual(delivered.message.data, { chatId: PAYLOAD.chatId, replies: ['Walaikum salam', 'Kaise madad karun?'], requestId: 'req-1' });
  assert.equal(bg.store.waNumber, '923001112222');
});

test('the request timeout is 35 seconds', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(200, { replies: [] }) });
  await bg.send(aiRequest(), TAB);
  assert.ok(bg.timerDelays.includes(35000), `timers: ${bg.timerDelays}`);
});

test('without an API key nothing is sent anywhere', async () => {
  const bg = makeBackground({ storage: { autoReplyEnabled: true }, fetchImpl: async () => assert.fail('no request expected') });
  assert.deepEqual(await bg.send(aiRequest(), TAB), { success: false, reason: 'no_api_key' });
  assert.equal(bg.fetchCalls.length, 0);
});

test('with auto-reply switched off nothing is sent', async () => {
  const bg = makeBackground({ storage: withKey({ autoReplyEnabled: false }), fetchImpl: async () => assert.fail('no request expected') });
  assert.equal((await bg.send(aiRequest(), TAB)).reason, 'auto_reply_disabled');
});

test('401 pauses the plugin: no reply, no retry, later bursts are skipped until Resume', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(401, { error: { code: 'INVALID_KEY', message: 'bad' } }) });
  const first = await bg.send(aiRequest(), TAB);
  assert.deepEqual(first, { success: false, reason: 'http', status: 401 });
  assert.equal(bg.fetchCalls.length, 1, 'never retried');
  assert.equal(bg.store.paused.code, 'INVALID_KEY');
  assert.equal(bg.sentToTabs.filter((m) => m.message.type === 'WA_LOGGER_AI_RESPONSE').length, 0);
  await flush();
  assert.equal(bg.badge.color, '#d93025');
  const control = bg.sentToTabs.filter((m) => m.message.type === 'CONTROL').at(-1);
  assert.equal(control.message.active, false, 'the page script is told to stop');

  const second = await bg.send(aiRequest({ requestId: 'req-2' }), TAB);
  assert.equal(second.reason, 'paused');
  assert.equal(bg.fetchCalls.length, 1, 'no request while paused');

  const status = await bg.send({ type: 'GET_STATUS' });
  assert.equal(status.paused.code, 'INVALID_KEY');
  await bg.send({ type: 'RESUME' });
  assert.equal(bg.store.paused, undefined);
});

test('403 suspended also pauses', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(403, { error: { code: 'VENDOR_SUSPENDED', message: 'x' } }) });
  await bg.send(aiRequest(), TAB);
  assert.equal(bg.store.paused.code, 'VENDOR_SUSPENDED');
});

test('a 5xx records "Last error", sends nothing to the customer and does not retry', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(504, { error: { code: 'BOT_TIMEOUT', message: 'x' } }) });
  const result = await bg.send(aiRequest(), TAB);
  assert.deepEqual(result, { success: false, reason: 'http', status: 504 });
  assert.equal(bg.fetchCalls.length, 1);
  assert.equal(bg.store.paused, undefined);
  assert.equal(bg.store.lastError.severity, 'yellow');
  assert.match(bg.store.lastError.text, /504.*BOT_TIMEOUT/);
  assert.equal(bg.sentToTabs.filter((m) => m.message.type === 'WA_LOGGER_AI_RESPONSE').length, 0);
  await flush();
  assert.equal(bg.badge.color, '#f9ab00');
});

test('a timeout is recorded and never retried', async () => {
  const bg = makeBackground({
    storage: withKey(),
    fetchImpl: (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    }),
  });
  const result = await bg.send(aiRequest(), TAB);
  assert.equal(result.reason, 'timeout');
  assert.equal(bg.fetchCalls.length, 1);
  assert.match(bg.store.lastError.text, /35s/);
});

test('a network failure is recorded as unreachable', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => { throw new TypeError('fetch failed'); } });
  assert.equal((await bg.send(aiRequest(), TAB)).reason, 'network');
  assert.match(bg.store.lastError.text, /unreachable/);
});

test('429 waits for Retry-After without switching the page script off', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(429, { error: { code: 'RATE_LIMITED', message: 'x' } }, { 'Retry-After': '30' }) });
  await bg.send(aiRequest(), TAB);
  assert.equal(bg.store.paused.code, 'RATE_LIMITED');
  assert.ok(bg.store.paused.until > Date.now());
  await flush();
  const control = bg.sentToTabs.filter((m) => m.message.type === 'CONTROL').at(-1);
  assert.equal(control.message.active, true);
  assert.equal((await bg.send(aiRequest({ requestId: 'req-2' }), TAB)).reason, 'paused');
});

test('an expired rate-limit pause lets requests through again', async () => {
  let calls = 0;
  const bg = makeBackground({
    storage: withKey({ paused: { code: 'RATE_LIMITED', message: 'x', until: Date.now() - 1000 } }),
    fetchImpl: async () => { calls += 1; return jsonResponse(200, { replies: ['ok'] }); },
  });
  assert.equal((await bg.send(aiRequest(), TAB)).success, true);
  assert.equal(calls, 1);
  assert.equal(bg.store.paused, undefined);
});

test('an unreadable 200 answer is an error, not a reply', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => ({ ...jsonResponse(200, {}), json: async () => { throw new SyntaxError('bad json'); } }) });
  assert.equal((await bg.send(aiRequest(), TAB)).reason, 'invalid_response');
  assert.ok(bg.store.lastError);
});

test('a success clears an old "Last error"', async () => {
  const bg = makeBackground({ storage: withKey({ lastError: { text: 'old', severity: 'yellow', at: 1 } }), fetchImpl: async () => jsonResponse(200, { replies: ['hi'] }) });
  await bg.send(aiRequest(), TAB);
  assert.equal(bg.store.lastError, undefined);
});

test('a bad request id from the page is replaced, not forwarded', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(200, { replies: [] }) });
  await bg.send(aiRequest({ requestId: 'bad id\r\nX-Evil: 1' }), TAB);
  assert.match(bg.fetchCalls[0].options.headers['X-Request-Id'], /^[0-9a-f-]{36}$/);
});

test('the device id is created once and kept in storage.local', async () => {
  const bg = makeBackground({ storage: { apiKey: KEY, autoReplyEnabled: true }, fetchImpl: async () => jsonResponse(200, { replies: [] }) });
  await bg.send(aiRequest(), TAB);
  const id = bg.store.deviceId;
  assert.match(id, /^[0-9a-f-]{36}$/);
  await bg.send(aiRequest({ requestId: 'req-2' }), TAB);
  assert.equal(bg.store.deviceId, id);
  assert.equal(bg.fetchCalls[1].options.headers['X-Device-Id'], id);
});

test('saving a key calls whoami, stores the key and shows who is connected', async () => {
  const bg = makeBackground({
    fetchImpl: async (url) => (url.endsWith('/v1/plugin/whoami') ? jsonResponse(200, WHO) : jsonResponse(200, { status: 'ok' })),
  });
  const result = await bg.send({ type: 'SAVE_KEY', apiKey: `  ${KEY}  ` });
  assert.deepEqual(result, { ok: true, vendor: { businessName: 'Ali Boutique', whatsappNumber: '923001112222', keyKind: 'live' } });
  assert.equal(bg.fetchCalls[0].url, `${GATEWAY}/v1/plugin/whoami`);
  assert.equal(bg.fetchCalls[0].options.headers['X-API-Key'], KEY);
  assert.equal(bg.store.apiKey, KEY);
  await flush();
  assert.equal(bg.badge.text, 'ON');
  assert.equal(bg.sentToTabs.filter((m) => m.message.type === 'CONTROL').at(-1).message.active, true);

  const status = await bg.send({ type: 'GET_STATUS' });
  assert.equal(status.backend, 'online');
  assert.equal(status.vendor.businessName, 'Ali Boutique');
  assert.ok(status.keyHint.startsWith('wab_live_k3f9a1xz_'));
  assert.ok(!JSON.stringify(status).includes('SUPERSECRET'), 'the status never contains the key');
});

test('saving a wrong key shows "Invalid key" and stores nothing', async () => {
  const bg = makeBackground({ fetchImpl: async () => jsonResponse(401, { error: { code: 'INVALID_KEY', message: 'x' } }) });
  assert.deepEqual(await bg.send({ type: 'SAVE_KEY', apiKey: 'wab_live_nope_nope' }), { ok: false, error: 'Invalid key.' });
  assert.equal(bg.store.apiKey, undefined);
});

test('saving a key while the backend is down reports it', async () => {
  const bg = makeBackground({ fetchImpl: async () => { throw new TypeError('fetch failed'); } });
  assert.match((await bg.send({ type: 'SAVE_KEY', apiKey: KEY })).error, /unreachable/i);
  assert.equal(bg.store.apiKey, undefined);
  assert.equal((await bg.send({ type: 'SAVE_KEY', apiKey: '   ' })).ok, false);
});

test('saving a good key lifts an earlier pause', async () => {
  const bg = makeBackground({ storage: { paused: { code: 'INVALID_KEY', message: 'x' }, lastError: { text: 'x', severity: 'red', at: 1 } }, fetchImpl: async () => jsonResponse(200, WHO) });
  await bg.send({ type: 'SAVE_KEY', apiKey: KEY });
  assert.equal(bg.store.paused, undefined);
  assert.equal(bg.store.lastError, undefined);
});

test('GET_STATUS reports Offline when /health fails and warns about a different WhatsApp number', async () => {
  const bg = makeBackground({
    storage: withKey({ vendor: { businessName: 'Ali Boutique', whatsappNumber: '923001112222', keyKind: 'live' }, waNumber: '923335556666' }),
    fetchImpl: async () => { throw new TypeError('fetch failed'); },
  });
  const status = await bg.send({ type: 'GET_STATUS' });
  assert.equal(status.backend, 'offline');
  assert.equal(status.waNumberMismatch, true);
  assert.equal(bg.fetchCalls[0].url, `${GATEWAY}/health`);
});

test('removing the key clears the saved state and switches the page script off', async () => {
  const bg = makeBackground({ storage: withKey({ vendor: { businessName: 'x', whatsappNumber: '923001112222', keyKind: 'live' } }), fetchImpl: async () => assert.fail('no request') });
  await bg.send({ type: 'CLEAR_KEY' });
  assert.equal(bg.store.apiKey, undefined);
  assert.equal(bg.store.vendor, undefined);
  await flush();
  assert.equal(bg.sentToTabs.filter((m) => m.message.type === 'CONTROL').at(-1).message.active, false);
});

test('the toggle switches the page script on and off', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => assert.fail('no request') });
  await bg.send({ type: 'SET_AUTO_REPLY', enabled: false });
  await flush();
  assert.equal(bg.sentToTabs.filter((m) => m.message.type === 'CONTROL').at(-1).message.active, false);
  assert.equal(bg.badge.text, 'OFF');
  await bg.send({ type: 'SET_AUTO_REPLY', enabled: true });
  await flush();
  assert.equal(bg.sentToTabs.filter((m) => m.message.type === 'CONTROL').at(-1).message.active, true);
});

test('content scripts can ask for the control state', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => assert.fail('no request') });
  assert.deepEqual(await bg.send({ type: 'GET_CONTROL' }, TAB), { active: true });
});

test('popup-only actions are refused when they come from a web page tab', async () => {
  const bg = makeBackground({ fetchImpl: async () => assert.fail('no request') });
  for (const type of ['SAVE_KEY', 'CLEAR_KEY', 'SET_AUTO_REPLY', 'RESUME', 'GET_STATUS']) {
    await assert.rejects(bg.send({ type, apiKey: KEY }, TAB), /not handled/);
  }
  assert.equal(bg.store.apiKey, undefined);
});

test('the popup page works even when Chrome reports a tab for it, a web page still cannot', async () => {
  const bg = makeBackground({ fetchImpl: async () => jsonResponse(200, { vendor: { businessName: 'Ali', whatsappNumber: '923001234567' } }) });
  const popupInTab = { tab: { id: 9 }, url: 'chrome-extension://testid/popup.html' };
  const result = await bg.send({ type: 'SAVE_KEY', apiKey: KEY }, popupInTab);
  assert.equal(result.ok, true);

  // a page that merely claims to be our popup has the page's own URL, not ours
  const fakePopup = { tab: { id: 9 }, url: 'https://web.whatsapp.com/?chrome-extension://testid/popup.html' };
  await assert.rejects(bg.send({ type: 'CLEAR_KEY' }, fakePopup), /not handled/);
  const lookalike = { tab: { id: 9 }, url: 'chrome-extension://otherid/popup.html' };
  await assert.rejects(bg.send({ type: 'CLEAR_KEY' }, lookalike), /not handled/);
  assert.ok(bg.store.apiKey);
});

test('the service worker only talks to the gateway and never polls', async () => {
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(200, { replies: ['hi'] }) });
  await bg.send(aiRequest(), TAB);
  await bg.send({ type: 'GET_STATUS' });
  assert.ok(bg.fetchCalls.every(({ url }) => url.startsWith(GATEWAY)));
  assert.deepEqual(new Set(bg.fetchCalls.map(({ url }) => new URL(url).pathname)), new Set(['/v1/ai-reply', '/health']));
  assert.equal(bg.spies.setInterval, 0);
});

test('the API key is never written to the console', async () => {
  const logged = [];
  const bg = makeBackground({ storage: withKey(), fetchImpl: async () => jsonResponse(500, { error: { code: 'INTERNAL_ERROR', message: 'x' } }) });
  bg.context.console.warn = (...args) => logged.push(args.join(' '));
  bg.context.console.log = (...args) => logged.push(args.join(' '));
  await bg.send(aiRequest(), TAB);
  assert.ok(logged.length > 0);
  assert.ok(logged.every((line) => !line.includes('SUPERSECRET') && !line.includes('salam')));
});
