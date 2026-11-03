import assert from 'node:assert/strict';
import test from 'node:test';

import { flush, makeInjected } from './helpers.mjs';

const CHAT_A = '923009990001@c.us';
const CHAT_B = '923009990002@c.us';
const BURST_MS = 7000;

async function burst(page, chatId, text) {
  page.incoming(chatId, text);
  await page.advance(BURST_MS);
}

const reply = (request, replies, chatId) => ({ source: 'WA_LOGGER_AI_RESPONSE', chatId, replies, requestId: request.requestId });

test('a text burst becomes one request with the own number and a request id', async () => {
  const page = makeInjected();
  await page.start();
  page.incoming(CHAT_A, 'salam');
  await page.advance(1000);
  page.incoming(CHAT_A, 'price kya hai?');
  await page.advance(BURST_MS - 1);
  assert.equal(page.requests().length, 0, 'still inside the burst wait');
  await page.advance(1);

  assert.equal(page.requests().length, 1);
  const request = page.requests()[0];
  assert.match(request.requestId, /^[0-9a-f-]{36}$/);
  assert.equal(request.waNumber, '923001112222');
  assert.equal(request.payload.chatId, CHAT_A);
  assert.deepEqual(request.payload.incomingMessages.map((m) => m.text), ['salam', 'price kya hai?']);
});

test('the own WhatsApp number is reported once it is known', async () => {
  const page = makeInjected();
  await page.start();
  const info = page.posted.filter((m) => m.source === 'WA_LOGGER_WA_INFO');
  assert.deepEqual(info, [{ source: 'WA_LOGGER_WA_INFO', waNumber: '923001112222' }]);
});

test('when WhatsApp does not expose the number, the request carries an empty one', async () => {
  const page = makeInjected({ ownNumber: '' });
  await page.start();
  await burst(page, CHAT_A, 'hi');
  assert.equal(page.requests()[0].waNumber, '');
  assert.equal(page.posted.filter((m) => m.source === 'WA_LOGGER_WA_INFO').length, 0);
});

test('nothing happens while the plugin is not active, and old messages are not answered after it turns on', async () => {
  const page = makeInjected();
  await page.start({ active: false });
  await burst(page, CHAT_A, 'hello while off');
  assert.equal(page.requests().length, 0);

  await page.advance(60000);
  page.deliver({ source: 'WA_LOGGER_CONTROL', active: true });
  page.incoming(CHAT_A, 'old message', { t: Math.floor(page.clock.now / 1000) - 120 });
  await page.advance(BURST_MS);
  assert.equal(page.requests().length, 0, 'a message from before the activation is ignored');
  await burst(page, CHAT_A, 'new message');
  assert.equal(page.requests().length, 1);
});

test('switching the plugin off drops a burst that is still waiting', async () => {
  const page = makeInjected();
  await page.start();
  page.incoming(CHAT_A, 'hi');
  await page.advance(1000);
  page.deliver({ source: 'WA_LOGGER_CONTROL', active: false });
  await page.advance(BURST_MS);
  assert.equal(page.requests().length, 0);
});

test('group chats are ignored and nothing is posted for them', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, '120363000000000000@g.us', 'hello group', { author: { _serialized: CHAT_A } });
  assert.equal(page.requests().length, 0);
  assert.deepEqual([...new Set(page.posted.map((m) => m.source))].sort(), ['WA_GATEWAY_WA_STATE', 'WA_LOGGER_INTERNAL_READY', 'WA_LOGGER_WA_INFO']);
});

test('a reply is sent into the chat once, for the matching request', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'salam');
  const request = page.requests()[0];

  page.deliver(reply(request, ['Walaikum salam'], CHAT_A));
  await flush();
  assert.deepEqual(page.sent, [{ chatId: CHAT_A, text: 'Walaikum salam' }]);

  page.deliver(reply(request, ['Walaikum salam'], CHAT_A));
  await flush();
  assert.equal(page.sent.length, 1, 'the same answer is never sent twice');
});

test('a reply that does not belong to a request of this tab is dropped', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'salam');
  const request = page.requests()[0];
  page.deliver({ source: 'WA_LOGGER_AI_RESPONSE', chatId: CHAT_A, replies: ['x'], requestId: 'someone-else' });
  page.deliver({ source: 'WA_LOGGER_AI_RESPONSE', chatId: CHAT_A, replies: ['x'] });
  page.deliver(reply(request, ['x'], CHAT_B));
  await flush();
  assert.equal(page.sent.length, 0);
});

test('an older reply is dropped when a newer request for the same chat exists', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'first question');
  const first = page.requests()[0];
  await burst(page, CHAT_A, 'second question');
  const second = page.requests()[1];
  assert.notEqual(first.requestId, second.requestId);

  page.deliver(reply(first, ['answer to the first'], CHAT_A));
  await flush();
  assert.equal(page.sent.length, 0, 'stale answer is not sent');

  page.deliver(reply(second, ['answer to the second'], CHAT_A));
  await flush();
  assert.deepEqual(page.sent, [{ chatId: CHAT_A, text: 'answer to the second' }]);
});

test('requests of different chats do not cancel each other', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'from A');
  await burst(page, CHAT_B, 'from B');
  const [a, b] = page.requests();
  page.deliver(reply(b, ['to B'], CHAT_B));
  page.deliver(reply(a, ['to A'], CHAT_A));
  await flush();
  assert.deepEqual(page.sent.map((s) => s.text).sort(), ['to A', 'to B']);
});

test('a reply that arrives after 90 seconds is dropped', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'salam');
  const request = page.requests()[0];
  await page.advance(91000);
  page.deliver(reply(request, ['too late'], CHAT_A));
  await flush();
  assert.equal(page.sent.length, 0);
});

test('the loop guard is per chat: a sent text only hides that text in that chat', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'salam');
  page.deliver(reply(page.requests()[0], ['ok'], CHAT_A));
  await flush();
  assert.equal(page.sent.length, 1);

  await burst(page, CHAT_B, 'ok');
  assert.equal(page.requests().length, 2, 'another chat saying "ok" is a normal customer message');

  await burst(page, CHAT_A, 'ok');
  assert.equal(page.requests().length, 2, 'the same text in the same chat right after our reply is ignored');
});

test('the loop guard expires after two minutes', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'salam');
  page.deliver(reply(page.requests()[0], ['ok'], CHAT_A));
  await flush();

  await page.advance(2 * 60 * 1000 + 1000);
  await burst(page, CHAT_A, 'ok');
  assert.equal(page.requests().length, 2, 'a customer may say "ok" again later');
});

test('media goes out once as base64Data, without thumbnail or preview copies', async () => {
  const page = makeInjected();
  await page.start();
  const bytes = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
  page.incoming(CHAT_A, '/9j/THUMBNAILTHUMBNAIL', {
    type: 'image',
    caption: 'dekho',
    mimetype: 'image/jpeg',
    filename: 'photo.jpg',
    size: bytes.length,
    downloadMedia: async () => bytes,
  });
  await page.advance(100); // the download finishes within milliseconds, long before the burst wait ends
  await page.advance(BURST_MS - 100);

  const media = page.requests()[0].payload.incomingMessages[0].media;
  assert.equal(media.mediaType, 'image');
  assert.equal(media.mimeType, 'image/jpeg');
  assert.equal(media.base64Data, Buffer.from(bytes).toString('base64'));
  for (const field of ['thumbnailBase64', 'previewUrl', 'isThumbnailFallback']) {
    assert.ok(!(field in media), `${field} must not be sent`);
  }
  assert.ok(!JSON.stringify(page.requests()[0]).includes('THUMBNAILTHUMBNAIL'));
});

test('a file over 15 MB is announced without bytes and without thumbnails', async () => {
  const page = makeInjected();
  await page.start();
  page.incoming(CHAT_A, '/9j/THUMBNAILTHUMBNAIL', { type: 'document', filename: 'big.pdf', mimetype: 'application/pdf', size: 20 * 1024 * 1024 });
  await page.advance(BURST_MS);
  const media = page.requests()[0].payload.incomingMessages[0].media;
  assert.equal(media.skippedLargeFile, true);
  assert.equal(media.base64Data, null);
  assert.ok(!('thumbnailBase64' in media) && !('previewUrl' in media));
});

test('the page exposes no global controller and posts no dashboard messages', async () => {
  const page = makeInjected();
  await page.start();
  await burst(page, CHAT_A, 'salam');
  assert.equal(page.win.WA_LOGGER, undefined);
  const sources = new Set(page.posted.map((m) => m.source));
  for (const source of sources) {
    assert.ok(['WA_LOGGER_INTERNAL_READY', 'WA_LOGGER_WA_INFO', 'WA_LOGGER_AI_REQUEST', 'WA_GATEWAY_WA_STATE'].includes(source), `unexpected message ${source}`);
  }
});
