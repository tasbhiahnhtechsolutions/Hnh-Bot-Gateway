// The spike script itself can only be judged on real WhatsApp Web (that is its whole point).
// These tests only protect what we promise the person who pastes it: it never sends without
// confirmation, never leaks text or full numbers, never touches the network, and a failing
// probe is reported as a failure instead of being hidden.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const SOURCE = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'spike.js'), 'utf8');
const OWN = '923009990001';
const TEST_NUMBER = '923001234567';

function makeWindow({ withSend = true } = {}) {
  const sent = [];
  const chats = new Map();
  const wid = (user, server) => ({ user, server, _serialized: `${user}@${server}` });
  const chat = (user, server, extra = {}) => {
    const c = { id: wid(user, server), msgs: { getModelsArray: () => c.models }, models: [], ...extra };
    chats.set(c.id._serialized, c);
    return c;
  };
  chat('923007770001', 'c.us');
  chat('11122233344455', 'lid', { contact: { phoneNumber: wid('923007770002', 'c.us') } });
  chat('99988877766655', 'lid', { contact: {} });

  const modules = {
    WAWebUserPrefsMeUser: { getMaybeMePnUser: () => wid(OWN, 'c.us') },
    WAWebChatCollection: { ChatCollection: { get: (id) => chats.get(id) || null, getModelsArray: () => [...chats.values()] } },
    WAWebWidFactory: { createWid: (id) => wid(...id.split('@')) },
    WAWebFindChatAction: {
      findOrCreateLatestChat: async (w) => ({ chat: chats.get(w._serialized) || chat(w.user, w.server) }),
    },
    WAWebQueryExist: { queryExists: async (w) => (w.user === TEST_NUMBER ? { wid: w } : null) },
    WAWebSocketModel: { Socket: { state: 'CONNECTED', hasSynced: true } },
    WAWebStreamModel: { Stream: { mode: 'MAIN', info: 'NORMAL' } },
    WAWebPollSendAction: { sendPollCreation: () => {} },
    WAWebLidPhoneUtils: { getPhoneNumber: (w) => (w.user === '11122233344455' ? wid('923007770002', 'c.us') : null) },
    __debug: { modulesMap: { WAWebPollSendAction: {}, WAWebLidPhoneUtils: {}, WAWebSocketModel: {}, WAWebQueryExist: {} } },
  };
  if (withSend) {
    modules.WAWebSendMsgChatAction = {
      addAndSendMsgToChat(c, data) {
        sent.push(data.body);
        const msg = { id: { fromMe: true }, body: data.body, ack: 0 };
        c.models.push(msg);
        setTimeout(() => { msg.ack = 1; }, 150);
        setTimeout(() => { msg.ack = 2; }, 400);
        return [Promise.resolve(msg), Promise.resolve()];
      },
    };
  }

  const logs = [];
  const window = {
    require: (name) => {
      if (!(name in modules)) throw new Error(`no module ${name}`);
      return modules[name];
    },
    Debug: { VERSION: '2.3000.1' },
    console: { log: (...a) => logs.push(a.join(' ')) },
  };
  const context = {
    window, navigator: { userAgent: 'test', onLine: true }, location: { pathname: '/', search: '' },
    document: { querySelectorAll: () => [], querySelector: () => null },
    console: window.console, setTimeout, clearTimeout, Date, JSON, Math, Promise, Array, Object, String,
    Uint8Array, crypto: { getRandomValues: (a) => a },
  };
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  return { spike: window.WASpike, sent, logs, window };
}

test('the file makes no network calls of any kind', () => {
  const banned = [/\bfetch\s*\(/, /\bXMLHttpRequest\b/, /\bnew\s+WebSocket\b/, /\bsendBeacon\b/, /\bimportScripts\b/, /\beval\s*\(/, /\bnew\s+Function\b/];
  for (const pattern of banned) {
    assert.ok(!pattern.test(SOURCE), `spike.js must not match ${pattern}`);
  }
});

test('runSafe sends nothing and reports what exists in this build', async () => {
  const { spike, sent } = makeWindow();
  const report = await spike.runSafe(TEST_NUMBER);
  assert.deepEqual(sent, []);
  assert.equal(report.probes.env.known.WAWebQueryExist.present, true);
  assert.equal(report.probes.env.known.WAWebFindChatAction.present, true);
  assert.equal(report.probes.env.known.WAWebCmd.present, false); // missing modules are said to be missing
  assert.ok(report.probes.env.discovered.interactive.includes('WAWebPollSendAction'));
  assert.equal(report.probes.account.chatsByServer.lid, 2);
  assert.equal(report.probes.connection.snapshot.socketState, 'CONNECTED');
  assert.equal(report.probes.lid.resolvedCount, 1); // one of two @lid chats could be mapped
  assert.equal(report.probes.lid.lidChatsChecked, 2);
});

test('exists reports yes, no, and failures honestly', async () => {
  const { spike } = makeWindow();
  const yes = await spike.exists(TEST_NUMBER);
  assert.equal(yes.ok, true);
  assert.equal(yes.hasWid, true);
  const no = await spike.exists('923000000000');
  assert.equal(no.answer, 'null/undefined');
});

test('send refuses without the confirm word and does nothing', async () => {
  const { spike, sent } = makeWindow();
  for (const options of [undefined, {}, { confirm: true }, { confirm: 'send' }]) {
    const result = await spike.send(TEST_NUMBER, 'hello', options);
    assert.equal(result.ok, false);
    assert.match(result.error, /REAL WhatsApp message/);
  }
  assert.deepEqual(sent, []);
});

test('a confirmed send records the ack timeline', async () => {
  const { spike, sent } = makeWindow();
  const result = await spike.send(TEST_NUMBER, 'spike hello', { confirm: 'SEND', ackSeconds: 1 });
  assert.equal(result.ok, true);
  assert.equal(result.sent, true);
  assert.deepEqual(sent, ['spike hello']);
  assert.equal(result.messageModelFound, true);
  assert.equal(result.reachedServerAck, true);
  assert.equal(result.maxAck, 2);
  assert.equal(JSON.stringify(result.ackTimeline.map((e) => e.ack)), '[0,1,2]');
});

test('at most five sends per page load', async () => {
  const { spike, sent } = makeWindow();
  for (let i = 0; i < 5; i++) {
    assert.equal((await spike.send(TEST_NUMBER, `m${i}`, { confirm: 'SEND', ackSeconds: 0.1 })).sent, true);
  }
  const sixth = await spike.send(TEST_NUMBER, 'm5', { confirm: 'SEND', ackSeconds: 0.1 });
  assert.equal(sixth.ok, false);
  assert.match(sixth.error, /limit of 5/);
  assert.equal(sent.length, 5);
});

test('a missing send function is a reported failure, not a fake success', async () => {
  const { spike } = makeWindow({ withSend: false });
  const result = await spike.send(TEST_NUMBER, 'x', { confirm: 'SEND' });
  assert.equal(result.ok, false);
  assert.match(result.error, /addAndSendMsgToChat not found/);
});

test('the report masks numbers and never contains message text', async () => {
  const { spike } = makeWindow();
  await spike.runSafe(TEST_NUMBER);
  await spike.send(TEST_NUMBER, 'my private customer text', { confirm: 'SEND', ackSeconds: 0.1 });
  const text = JSON.stringify(spike.report());
  assert.ok(!text.includes('my private customer text'));
  for (const full of [TEST_NUMBER, OWN, '923007770001', '923007770002']) {
    assert.ok(!text.includes(full), `report must not contain the full number ${full}`);
  }
  assert.ok(text.includes('*******4567')); // last four digits survive
});

test('interactive attempts also need the confirm word and a known kind', async () => {
  const { spike, sent } = makeWindow();
  assert.equal((await spike.tryInteractive(TEST_NUMBER, 'poll', {})).ok, false);
  assert.equal((await spike.tryInteractive(TEST_NUMBER, 'carousel', { confirm: 'SEND' })).ok, false);
  assert.deepEqual(sent, []);
});

test('loading the script twice does not replace the first copy', () => {
  const { window } = makeWindow();
  const first = window.WASpike;
  vm.runInContext(SOURCE, vm.createContext({ window, navigator: {}, location: {}, document: {}, console: window.console }));
  assert.equal(window.WASpike, first);
});
