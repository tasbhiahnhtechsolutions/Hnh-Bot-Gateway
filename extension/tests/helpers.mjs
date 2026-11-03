// Test harnesses: run the real extension scripts in a node:vm context with
// fake chrome / window objects. No browser and no real WhatsApp involved.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

export const EXTENSION_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);

export const gatewayClient = require(join(EXTENSION_DIR, 'gateway-client.js'));

const readSource = (name) => readFileSync(join(EXTENSION_DIR, name), 'utf8');

// Objects made inside a vm context have another Object prototype; copy them so
// deepStrictEqual compares plain data.
const plain = (value) => (value === undefined ? value : JSON.parse(JSON.stringify(value)));

export const flush = () => new Promise((resolve) => setImmediate(resolve));
// Long promise chains (waitFor loops) need several turns before they register their next timer.
const flushDeep = async () => {
  for (let i = 0; i < 5; i += 1) await flush();
};

export function jsonResponse(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name] ?? headers[name.toLowerCase()] ?? null },
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  };
}

// ---------------------------------------------------------------------------
// background.js
// ---------------------------------------------------------------------------

export function makeBackground({ fetchImpl, storage = {}, tabs = [{ id: 7 }], config, io } = {}) {
  const store = { ...storage };
  const changeListeners = [];
  const handlers = {};
  const sentToTabs = [];
  const fetchCalls = [];
  const timerDelays = [];
  const badge = { text: '', color: '' };
  const spies = { setInterval: 0 };
  const alarms = { created: [], handler: null };
  const tabUpdates = [];

  const pick = (keys) => {
    const names = keys == null ? Object.keys(store) : [].concat(keys);
    return Object.fromEntries(names.filter((k) => k in store).map((k) => [k, store[k]]));
  };

  const chrome = {
    storage: {
      local: {
        get: async (keys) => pick(keys),
        set: async (items) => {
          const changes = Object.fromEntries(Object.keys(items).map((k) => [k, { newValue: items[k] }]));
          Object.assign(store, items);
          changeListeners.forEach((fn) => fn(changes, 'local'));
        },
        remove: async (keys) => {
          const names = [].concat(keys);
          const changes = Object.fromEntries(names.filter((k) => k in store).map((k) => [k, {}]));
          names.forEach((k) => delete store[k]);
          if (Object.keys(changes).length) changeListeners.forEach((fn) => fn(changes, 'local'));
        },
      },
      onChanged: { addListener: (fn) => changeListeners.push(fn) },
    },
    runtime: {
      getURL: (path = '') => `chrome-extension://testid/${path}`,
      getManifest: () => ({ version: '2.0.0' }),
      onMessage: { addListener: (fn) => { handlers.message = fn; } },
      onInstalled: { addListener: (fn) => { handlers.installed = fn; } },
      onStartup: { addListener: (fn) => { handlers.startup = fn; } },
    },
    action: {
      setBadgeText: async ({ text }) => { badge.text = text; },
      setBadgeBackgroundColor: async ({ color }) => { badge.color = color; },
    },
    tabs: {
      query: async () => tabs,
      sendMessage: async (tabId, message) => { sentToTabs.push({ tabId, message: plain(message) }); },
      update: async (tabId, props) => { tabUpdates.push({ tabId, ...props }); },
    },
    alarms: {
      create: (name, info) => alarms.created.push({ name, info }),
      onAlarm: { addListener: (fn) => { alarms.handler = fn; } },
    },
  };

  const context = vm.createContext({
    chrome,
    console: { log() {}, warn() {}, error() {} },
    crypto: globalThis.crypto,
    AbortController,
    URL,
    setInterval: () => { spies.setInterval += 1; return spies.setInterval; },
    clearInterval: () => {},
    clearTimeout,
    setTimeout: (fn, ms) => {
      timerDelays.push(ms);
      return setTimeout(fn, ms > 1000 ? 5 : ms); // run long timeouts (the 35s abort) quickly
    },
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      return fetchImpl(url, options);
    },
  });
  context.self = context;
  context.importScripts = (...files) => {
    for (const file of files) {
      if (file.startsWith('vendor/')) {
        // The real socket.io client needs a network; tests pass a fake through the `io` option.
        if (io) context.io = io;
        else throw new Error('vendor bundle not loaded in tests');
      } else if (file === 'config.js' && config) context.WA_GATEWAY_CONFIG = config;
      else vm.runInContext(readSource(file), context, { filename: file });
    }
  };
  vm.runInContext(readSource('background.js'), context, { filename: 'background.js' });

  const send = (message, sender = {}) =>
    new Promise((resolve, reject) => {
      const keepOpen = handlers.message(message, sender, (response) => resolve(plain(response)));
      if (keepOpen !== true) reject(new Error(`message ${message.type} was not handled`));
    });

  return { store, send, sentToTabs, fetchCalls, timerDelays, badge, spies, handlers, context, alarms, tabUpdates };
}

// ---------------------------------------------------------------------------
// injected.js
// ---------------------------------------------------------------------------

export function makeInjected({ ownNumber = '923001112222' } = {}) {
  const clock = { now: Date.UTC(2026, 10, 2, 10, 0, 0) };
  const timers = [];
  const posted = [];
  const listeners = [];
  const msgHandlers = [];
  const chats = new Map();
  const sent = []; // {chatId, text}
  // Knobs for the outbound tests (what this pretend WhatsApp does when the plugin sends for a vendor).
  // Nothing here is verified against a real WhatsApp Web build; see spike/SPIKE_REPORT.md.
  const fake = {
    connState: 'CONNECTED',
    ackLevel: 1, // 0 = the ack never comes
    ackDelayMs: 200,
    sendFails: false,
    exists: null, // async (jid) => answer, or null when this build has no such function
    primaryCreate: false, // does the "create chat" function exist and work
    popup: '', // text of a dialog on the page
  };

  class FakeDate extends Date {
    constructor(...args) {
      if (args.length) super(...args);
      else super(clock.now);
    }

    static now() {
      return clock.now;
    }
  }

  const modules = {
    WAWebMsgCollection: { MsgCollection: { on: (event, handler) => event === 'add' && msgHandlers.push(handler) } },
    WAWebUserPrefsMeUser: { getMaybeMePnUser: () => (ownNumber ? { user: ownNumber, _serialized: `${ownNumber}@c.us` } : null) },
    WAWebChatCollection: { ChatCollection: { get: (id) => chats.get(id) } },
    WAWebConnModel: { Conn: { get state() { return fake.connState; } } },
    WAWebSendMsgChatAction: {
      addAndSendMsgToChat: async (chat, msgData) => {
        if (fake.sendFails) throw new Error('fake send failure');
        sent.push({ chatId: chat.id._serialized, text: msgData.body });
        const model = { id: msgData.id, body: msgData.body, ack: 0 };
        chat.models.push(model);
        if (fake.ackLevel > 0) timers.push({ fn: () => { model.ack = fake.ackLevel; }, at: clock.now + fake.ackDelayMs });
      },
    },
  };

  const win = {
    require: (name) => {
      if (name === 'WAWebQueryExistsJob') return fake.exists ? { queryExists: fake.exists } : undefined;
      if (name === 'WAWebFindChatAction') {
        return fake.primaryCreate ? { findOrCreateLatestChat: async (wid) => addChat(wid._serialized) } : undefined;
      }
      return modules[name];
    },
    postMessage: (data) => posted.push(plain(data)),
    addEventListener: (type, fn) => type === 'message' && listeners.push(fn),
  };

  const context = vm.createContext({
    window: win,
    document: {
      readyState: 'complete',
      querySelector: () => null,
      querySelectorAll: () => (fake.popup ? [{ textContent: fake.popup }] : []),
    },
    console: { log() {}, warn() {}, error() {} },
    crypto: globalThis.crypto,
    Blob,
    btoa,
    Math,
    Date: FakeDate,
    setTimeout: (fn, ms = 0) => {
      const timer = { fn, at: clock.now + ms };
      timers.push(timer);
      return timer;
    },
    clearTimeout: (timer) => {
      const index = timers.indexOf(timer);
      if (index >= 0) timers.splice(index, 1);
    },
    setInterval: () => 0,
  });
  vm.runInContext(readSource('injected.js'), context, { filename: 'injected.js' });

  async function advance(ms) {
    const target = clock.now + ms;
    await flushDeep(); // let a just-delivered message register its first timer
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const next = timers[0];
      if (!next || next.at > target) break;
      timers.shift();
      clock.now = Math.max(clock.now, next.at);
      next.fn();
      await flushDeep();
    }
    clock.now = target;
    await flushDeep();
  }

  const deliver = (data) => listeners.forEach((fn) => fn({ source: win, data }));
  const requests = () => posted.filter((m) => m.source === 'WA_LOGGER_AI_REQUEST');

  let counter = 0;
  function addChat(chatId) {
    const models = [];
    const chat = {
      id: { _serialized: chatId },
      formattedTitle: `Customer ${chatId.split('@')[0]}`,
      models,
      msgs: {
        getModelsArray: () => models,
        get: (id) => models.find((m) => m.id?._serialized === id),
      },
    };
    chats.set(chatId, chat);
    return chat;
  }

  function incoming(chatId, text, extra = {}) {
    counter += 1;
    const chat = chats.get(chatId) || addChat(chatId);
    const msg = {
      id: { _serialized: `msg-${counter}`, fromMe: false, remote: { _serialized: chatId } },
      from: { _serialized: chatId },
      type: 'chat',
      body: text,
      t: Math.floor(clock.now / 1000),
      isNewMsg: true,
      chat,
      ...extra,
    };
    msgHandlers.forEach((handler) => handler(msg));
    return msg;
  }

  async function start({ active = true } = {}) {
    await advance(1000); // the script hooks WhatsApp one second after load
    if (active) deliver({ source: 'WA_LOGGER_CONTROL', active: true });
  }

  const outboundEvents = () => posted.filter((m) => m.source === 'WA_GATEWAY_OUTBOUND_EVENT').map((m) => m.event);
  const command = (job, resume = false) => deliver({ source: 'WA_GATEWAY_OUTBOUND_COMMAND', command: { type: 'SEND', job, resume } });

  return {
    win, clock, posted, sent, advance, deliver, requests, incoming, addChat, start, fake, chats,
    document: context.document,
    outboundEvents, command, hooked: () => msgHandlers.length > 0,
  };
}
