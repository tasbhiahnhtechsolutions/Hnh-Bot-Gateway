// The page side of vendor sending, against a pretend WhatsApp. Whether the real WhatsApp Web
// behaves like this pretend one is NOT proven here: that is what spike/ is for.
import assert from 'node:assert/strict';
import test from 'node:test';
import { makeInjected } from './helpers.mjs';

const JOB = { jobId: 'job_0123456789abcdef', attempt: 1, to: '923001234567', message: 'Aap ka OTP 482913 hai.' };
const JID = `${JOB.to}@c.us`;

async function ready() {
  const page = makeInjected();
  await page.start();
  page.fake.exists = async () => ({ exists: true });
  return page;
}

const events = (page) => page.posted.filter((m) => m.source === 'WA_GATEWAY_OUTBOUND_EVENT').map((m) => m.event);
const names = (page) => events(page).map((e) => e.event);
const result = (page) => events(page).find((e) => e.event === 'result');

test('chat list on screen while Conn.state is still connecting: treated as connected', async () => {
  const page = makeInjected();
  page.fake.connState = 'connecting';
  page.document.querySelector = (sel) => (String(sel).includes('pane-side') ? {} : null);
  await page.start();
  page.addChat(JID);
  page.fake.exists = async () => ({ exists: true });
  page.command(JOB);
  await page.advance(2000);
  assert.equal(result(page).status, 'sent');
});

test('existing chat: sends, then reports sent with the ack level', async () => {
  const page = await ready();
  page.addChat(JID);
  page.command(JOB);
  await page.advance(2000);
  assert.deepEqual(page.sent, [{ chatId: JID, text: JOB.message }]);
  assert.deepEqual(names(page), ['sending', 'result']);
  assert.deepEqual(result(page), { jobId: JOB.jobId, attempt: 1, event: 'result', status: 'sent', waAck: 1 });
});

test('"sending" is announced before the message is handed to WhatsApp', async () => {
  const page = await ready();
  page.addChat(JID);
  page.command(JOB);
  await page.advance(0);
  assert.equal(names(page)[0], 'sending');
});

test('the number is not on WhatsApp: failed NOT_ON_WHATSAPP, nothing sent', async () => {
  const page = await ready();
  page.fake.exists = async () => ({ exists: false });
  page.command(JOB);
  await page.advance(2000);
  assert.deepEqual(page.sent, []);
  assert.equal(result(page).errorCode, 'NOT_ON_WHATSAPP');
});

test('a clear "exists" answer and no chat: the chat is created through the primary function', async () => {
  const page = await ready();
  page.fake.primaryCreate = true;
  page.command(JOB);
  await page.advance(2000);
  assert.deepEqual(page.sent, [{ chatId: JID, text: JOB.message }]);
  assert.equal(result(page).status, 'sent');
});

test('this WhatsApp build has no exists-query: still tries, it just cannot pre-check', async () => {
  const page = await ready();
  page.fake.exists = null;
  page.addChat(JID);
  page.command(JOB);
  await page.advance(2000);
  assert.equal(result(page).status, 'sent');
});

test('no chat and no create function: asks for the /send?phone= fallback, sends nothing', async () => {
  const page = await ready();
  page.fake.primaryCreate = false;
  page.command(JOB);
  await page.advance(2000);
  assert.deepEqual(names(page), ['url_fallback']);
  assert.deepEqual(page.sent, []);
});

test('resumed on the page that /send?phone= opened: waits for the chat, then sends', async () => {
  const page = await ready();
  page.command(JOB, true);
  await page.advance(1000);
  assert.deepEqual(page.sent, []);
  page.addChat(JID);
  await page.advance(2000);
  assert.deepEqual(page.sent, [{ chatId: JID, text: JOB.message }]);
  assert.equal(result(page).status, 'sent');
});

test('resumed: the invalid-number popup means NOT_ON_WHATSAPP', async () => {
  const page = await ready();
  page.fake.popup = 'Phone number shared via url is invalid.';
  page.command(JOB, true);
  await page.advance(2000);
  assert.equal(result(page).errorCode, 'NOT_ON_WHATSAPP');
  assert.deepEqual(page.sent, []);
});

test('resumed: neither a chat nor a popup in 15 seconds is SEND_FAILED (nothing was sent)', async () => {
  const page = await ready();
  page.command(JOB, true);
  await page.advance(16000);
  assert.equal(result(page).errorCode, 'SEND_FAILED');
  assert.deepEqual(page.sent, []);
});

test('resumed never asks for another reload and never re-checks the number', async () => {
  const page = await ready();
  let asked = 0;
  page.fake.exists = async () => {
    asked += 1;
    return { exists: true };
  };
  page.command(JOB, true);
  await page.advance(16000);
  assert.equal(asked, 0);
  assert.ok(!names(page).includes('url_fallback'));
});

test('no ack within 15 seconds: no_ack, and never "sent"', async () => {
  const page = await ready();
  page.fake.ackLevel = 0;
  page.addChat(JID);
  page.command(JOB);
  await page.advance(16000);
  assert.deepEqual(page.sent.length, 1, 'it was handed to WhatsApp once');
  assert.deepEqual(names(page), ['sending', 'no_ack']);
  assert.equal(result(page), undefined);
});

test('a late ack (after a few seconds) is still caught in time', async () => {
  const page = await ready();
  page.fake.ackDelayMs = 6000;
  page.addChat(JID);
  page.command(JOB);
  await page.advance(10000);
  assert.equal(result(page).status, 'sent');
});

test('WhatsApp refuses the send: failed SEND_FAILED and no typing fallback', async () => {
  const page = await ready();
  page.fake.sendFails = true;
  page.addChat(JID);
  page.command(JOB);
  await page.advance(2000);
  assert.deepEqual(page.sent, []);
  assert.equal(result(page).errorCode, 'SEND_FAILED');
});

test('WhatsApp not connected: waits, then SEND_FAILED without sending', async () => {
  const page = await ready();
  page.fake.connState = 'TIMEOUT';
  page.addChat(JID);
  page.command(JOB);
  await page.advance(21000);
  assert.deepEqual(page.sent, []);
  assert.equal(result(page).errorCode, 'SEND_FAILED');
  assert.ok(!names(page).includes('sending'));
});

test('WhatsApp connects a few seconds late: the job waits and then goes out', async () => {
  const page = await ready();
  page.fake.connState = 'CONNECTING';
  page.addChat(JID);
  page.command(JOB);
  await page.advance(3000);
  page.fake.connState = 'CONNECTED';
  await page.advance(3000);
  assert.equal(result(page).status, 'sent');
});

test('two commands never overlap: the second starts only after the first finished', async () => {
  const page = await ready();
  const other = { ...JOB, jobId: 'job_aaaaaaaaaaaaaaaa', to: '923009998888', message: 'second' };
  page.addChat(JID);
  page.addChat(`${other.to}@c.us`);
  page.command(JOB);
  page.command(other);
  await page.advance(10000);
  assert.deepEqual(page.sent.map((s) => s.text), [JOB.message, 'second']);
  const order = events(page).map((e) => `${e.jobId.slice(4, 6)}:${e.event}`);
  assert.deepEqual(order, ['01:sending', '01:result', 'aa:sending', 'aa:result']);
});

test('malformed commands are ignored without a word', async () => {
  const page = await ready();
  page.addChat(JID);
  for (const job of [null, {}, { ...JOB, jobId: 'x' }, { ...JOB, to: '12' }, { ...JOB, message: '' }, { ...JOB, attempt: 'a' }]) {
    page.deliver({ source: 'WA_GATEWAY_OUTBOUND_COMMAND', command: { type: 'SEND', job } });
  }
  page.deliver({ source: 'WA_GATEWAY_OUTBOUND_COMMAND', command: { type: 'DROP_TABLE', job: JOB } });
  await page.advance(2000);
  assert.deepEqual(page.sent, []);
  assert.deepEqual(events(page), []);
});

test('connection state is reported to the extension, mapped to our five words', async () => {
  const cases = { CONNECTED: 'connected', CONNECTING: 'connecting', TIMEOUT: 'offline', UNPAIRED: 'logged_out', WEIRD: 'connecting' };
  for (const [raw, expected] of Object.entries(cases)) {
    const page = makeInjected();
    page.fake.connState = raw;
    await page.advance(1000);
    const states = page.posted.filter((m) => m.source === 'WA_GATEWAY_WA_STATE').map((m) => m.state);
    assert.equal(states.at(-1), expected, `${raw} -> ${expected}`);
  }
});
