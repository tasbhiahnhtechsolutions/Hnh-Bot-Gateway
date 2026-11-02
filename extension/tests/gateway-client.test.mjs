import assert from 'node:assert/strict';
import test from 'node:test';

import { gatewayClient as gw } from './helpers.mjs';

test('ai-reply headers follow the contract', () => {
  const headers = gw.buildAiReplyHeaders({ apiKey: 'wab_live_k3f9a1xz_secret', deviceId: 'dev-1', waNumber: '+92 300 1112222', requestId: 'req-1' });
  assert.equal(headers['X-API-Key'], 'wab_live_k3f9a1xz_secret');
  assert.equal(headers['X-Request-Id'], 'req-1');
  assert.equal(headers['X-Device-Id'], 'dev-1');
  assert.equal(headers['X-WA-Number'], '923001112222');
  assert.equal(headers['X-Contract-Version'], '1');
  assert.equal(headers['Content-Type'], 'application/json');
});

test('X-WA-Number and X-Device-Id are left out when unknown', () => {
  const headers = gw.buildAiReplyHeaders({ apiKey: 'k', requestId: 'r' });
  assert.ok(!('X-WA-Number' in headers));
  assert.ok(!('X-Device-Id' in headers));
});

test('the timeout is longer than the 30s the backend waits for the bot', () => {
  assert.ok(gw.AI_REPLY_TIMEOUT_MS >= 35000);
});

test('401 and 403 pause the plugin, 5xx and 413 do not', () => {
  assert.equal(gw.classifyHttpFailure({ status: 401 }).pause.code, 'INVALID_KEY');
  assert.equal(gw.classifyHttpFailure({ status: 403, errorCode: 'VENDOR_SUSPENDED' }).pause.code, 'VENDOR_SUSPENDED');
  assert.equal(gw.classifyHttpFailure({ status: 403, errorCode: 'NUMBER_MISMATCH' }).pause.code, 'NUMBER_MISMATCH');
  assert.equal(gw.classifyHttpFailure({ status: 403 }).pause.code, 'HTTP_403');
  for (const status of [400, 413, 500, 502, 504]) {
    const result = gw.classifyHttpFailure({ status, errorCode: 'BOT_TIMEOUT' });
    assert.equal(result.pause, null, `status ${status}`);
    assert.equal(result.severity, 'yellow');
  }
});

test('429 pauses until Retry-After has passed', () => {
  const result = gw.classifyHttpFailure({ status: 429, retryAfterSeconds: 30, now: 1000 });
  assert.equal(result.pause.code, 'RATE_LIMITED');
  assert.equal(result.pause.until, 31000);
  assert.equal(gw.classifyHttpFailure({ status: 429, now: 0 }).pause.until, 60000);
});

test('network failures are yellow and never pause', () => {
  const timeout = gw.classifyNetworkFailure({ name: 'AbortError' });
  assert.match(timeout.lastError, /35s/);
  assert.equal(timeout.pause, null);
  assert.match(gw.classifyNetworkFailure(new TypeError('fetch failed')).lastError, /unreachable/);
});

test('badge colours: green ok, yellow temporary, red needs the user, grey off', () => {
  const base = { hasKey: true, paused: false, autoReplyEnabled: true, lastErrorSeverity: undefined };
  assert.deepEqual(gw.badgeFor(base), { text: 'ON', color: '#188038' });
  assert.equal(gw.badgeFor({ ...base, lastErrorSeverity: 'yellow' }).color, '#f9ab00');
  assert.equal(gw.badgeFor({ ...base, paused: true }).color, '#d93025');
  assert.equal(gw.badgeFor({ ...base, hasKey: false }).color, '#d93025');
  assert.equal(gw.badgeFor({ ...base, autoReplyEnabled: false }).text, 'OFF');
});

test('maskKey never reveals the secret part', () => {
  const masked = gw.maskKey('wab_live_k3f9a1xz_SUPERSECRETVALUE');
  assert.ok(masked.startsWith('wab_live_k3f9a1xz_'));
  assert.ok(!masked.includes('SUPERSECRET'));
  assert.equal(gw.maskKey(''), '');
});

test('error code and Retry-After parsing never throw', () => {
  assert.equal(gw.errorCodeFromBody('{"error":{"code":"BOT_TIMEOUT","message":"x"}}'), 'BOT_TIMEOUT');
  assert.equal(gw.errorCodeFromBody('<html>502</html>'), '');
  assert.equal(gw.errorCodeFromBody('{"error":{"code":"<script>"}}'), '');
  assert.equal(gw.parseRetryAfter('15'), 15);
  assert.equal(gw.parseRetryAfter('soon'), 0);
  assert.equal(gw.parseRetryAfter(null), 0);
});

test('numbers compare by digits only', () => {
  assert.ok(gw.numbersMatch('+92 300 1112222', '923001112222'));
  assert.ok(!gw.numbersMatch('923001112222', '923001112223'));
  assert.ok(!gw.numbersMatch('', ''));
  assert.equal(gw.formatPhone('923001112222'), '+923001112222');
});
