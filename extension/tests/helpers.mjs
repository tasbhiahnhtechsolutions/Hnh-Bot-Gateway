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

export function makeBackground({ fetchImpl, storage = {}, tabs = [{ id: 7 }], config } = {}) {
  const store = { ...storage };
  const changeListeners = [];
  const handlers = {};
  const sentToTabs = [];
  const fetchCalls = [];
  const timerDelays = [];
  const badge = { text: '', color: '' };
  const spies = { setInterval: 0 };

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
    },
  };

  const context = vm.createContext({
    chrome,
    console: { log() {}, warn() {}, error() {} },
    crypto: globalThis.crypto,
    AbortController,
    URL,
    setInterval: () => { spies.setInterval += 1; },
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
      if (file === 'config.js' && config) context.WA_GATEWAY_CONFIG = config;
      else vm.runInContext(readSource(file), context, { filename: file });
    }
  };
  vm.runInContext(readSource('background.js'), context, { filename: 'background.js' });

  const send = (message, sender = {}) =>
    new Promise((resolve, reject) => {
      const keepOpen = handlers.message(message, sender, (response) => resolve(plain(response)));
      if (keepOpen !== true) reject(new Error(`message ${message.type} was not handled`));
    });

  return { store, send, sentToTabs, fetchCalls, timerDelays, badge, spies, handlers, context };
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
    WAWebSendMsgChatAction: {
      addAndSendMsgToChat: async (chat, msgData) => {
        sent.push({ chatId: chat.id._serialized, text: msgData.body });
      },
    },
  };

  const win = {
    require: (name) => modules[name],
    postMessage: (data) => posted.push(plain(data)),
    addEventListener: (type, fn) => type === 'message' && listeners.push(fn),
  };

  const context = vm.createContext({
    window: win,
    document: { readyState: 'complete', querySelector: () => null },
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
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const next = timers[0];
      if (!next || next.at > target) break;
      timers.shift();
      clock.now = Math.max(clock.now, next.at);
      next.fn();
      await flush();
    }
    clock.now = target;
    await flush();
  }

  const deliver = (data) => listeners.forEach((fn) => fn({ source: win, data }));
  const requests = () => posted.filter((m) => m.source === 'WA_LOGGER_AI_REQUEST');

  let counter = 0;
  function addChat(chatId) {
    const chat = {
      id: { _serialized: chatId },
      formattedTitle: `Customer ${chatId.split('@')[0]}`,
      msgs: { getModelsArray: () => [] },
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

  return { win, clock, posted, sent, advance, deliver, requests, incoming, addChat, start, hooked: () => msgHandlers.length > 0 };
}
