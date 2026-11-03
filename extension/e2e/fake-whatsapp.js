/* Fake web.whatsapp.com (plan section 7.4). Served instead of the real site by Playwright.
 *
 * It offers the few WhatsApp Web internals the extension uses (window.require modules) and a
 * small control API for the tests (window.__fake). It never touches the network and no real
 * number is involved: "sent" messages only land in the `sent` list.
 *
 * IMPORTANT: this is OUR guess of how WhatsApp Web behaves (connection state, "does this number
 * exist", creating a chat, ack levels, the /send?phone= page). A green test here does NOT prove
 * the real WhatsApp does the same. That is what spike/ on a real test account is for.
 *
 * The settings and the list of sent messages live in localStorage, so they survive the page
 * reload that /send?phone= causes (exactly like a real page reload would lose in-memory state).
 */
(function () {
  'use strict';

  const STORE_KEY = '__fake_state';
  const params = new URLSearchParams(location.search);

  function loadState() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
    } catch (_) {
      return {};
    }
  }

  const state = loadState();
  if (params.get('me')) state.me = params.get('me');
  state.me = state.me || '923000000000';
  state.loads = (state.loads || 0) + 1;
  state.sent = state.sent || [];
  state.cfg = {
    connState: 'CONNECTED', // what WAWebConnModel.Conn.state says
    existsAvailable: true, // does the "is this number on WhatsApp" function exist
    registered: [], // numbers that are on WhatsApp (digits only)
    contacts: [], // numbers that already have a chat
    primaryCreate: false, // does the "create a chat for this number" function exist and work
    ackLevel: 1, // 0 = the ack never comes
    ackDelayMs: 300,
    failSends: false,
    sendUrlDelayMs: 500, // /send?phone= opens the chat after this long
    sendUrlNever: false, // /send?phone= never opens a chat
    ...(state.cfg || {}),
  };
  const cfg = state.cfg;
  const save = () => localStorage.setItem(STORE_KEY, JSON.stringify(state));
  save();

  const me = state.me;
  const listeners = [];
  const chats = new Map();
  let counter = 0;

  function wid(user, server) {
    const serialized = `${user}@${server}`;
    return { user, server, _serialized: serialized, toString: () => serialized };
  }

  function chatFor(chatId) {
    if (chats.has(chatId)) return chats.get(chatId);
    const [user, server] = chatId.split('@');
    const models = [];
    const chat = {
      id: wid(user, server),
      formattedTitle: '',
      name: '',
      isNewsletter: false,
      contact: { name: null },
      models,
      msgs: {
        getModelsArray: () => models,
        get: (id) => models.find((m) => m.id?._serialized === id) || null,
      },
    };
    chats.set(chatId, chat);
    return chat;
  }

  for (const number of cfg.contacts) chatFor(`${number}@c.us`);

  const MsgCollection = {
    on(event, handler) {
      if (event === 'add') {
        listeners.push(handler);
        window.__fake.hooked = true;
      }
    },
    off() {},
  };

  const modules = {
    WAWebMsgCollection: { MsgCollection },
    WAWebChatCollection: {
      ChatCollection: {
        get: (id) => chats.get(id) || null,
        getModelsArray: () => [...chats.values()],
      },
    },
    WAWebContactCollection: { ContactCollection: { get: () => null } },
    WAWebUserPrefsMeUser: { getMaybeMePnUser: () => wid(me, 'c.us') },
    WAWebConnModel: {
      Conn: {
        get state() {
          return cfg.connState;
        },
      },
    },
    WAWebSendMsgChatAction: {
      addAndSendMsgToChat(chat, data) {
        if (window.__fake.failSends || cfg.failSends) return Promise.reject(new Error('fake send failure'));
        state.sent.push({ chatId: chat.id._serialized, text: data.body, at: Date.now() });
        save();
        const model = {
          id: data.id && data.id._serialized ? data.id : { fromMe: true, _serialized: `true_${chat.id._serialized}_${++counter}` },
          type: 'chat',
          body: data.body,
          ack: 0,
          t: Math.floor(Date.now() / 1000),
        };
        chat.models.push(model);
        if (cfg.ackLevel > 0) setTimeout(() => { model.ack = cfg.ackLevel; }, cfg.ackDelayMs);
        return Promise.resolve();
      },
    },
  };

  window.require = (name) => {
    if (name === 'WAWebQueryExistsJob') {
      if (!cfg.existsAvailable) return undefined;
      return {
        queryExists: async (jid) => {
          const digits = String(jid).split('@')[0];
          return cfg.registered.includes(digits) ? { exists: true, wid: wid(digits, 'c.us') } : { exists: false };
        },
      };
    }
    if (name === 'WAWebFindChatAction') {
      if (!cfg.primaryCreate) return undefined;
      return { findOrCreateLatestChat: async (w) => ({ chat: chatFor(w._serialized) }) };
    }
    return modules[name];
  };

  window.__fake = {
    me,
    hooked: false,
    failSends: false,
    loads: state.loads,
    sent: () => state.sent.map((m) => ({ ...m })),
    config: () => ({ ...cfg }),
    /** Change what this pretend WhatsApp does. Kept across page reloads. */
    configure(patch) {
      Object.assign(cfg, patch);
      for (const number of cfg.contacts) chatFor(`${number}@c.us`);
      save();
    },
    /** A customer writes to this number. `group: true` simulates a group chat. */
    deliverIncoming({ from, text, type = 'chat', group = false }) {
      const server = group ? 'g.us' : 'c.us';
      const chatId = `${from}@${server}`;
      const chat = chatFor(chatId);
      const msg = {
        id: { fromMe: false, id: `FAKE${++counter}`, _serialized: `false_${chatId}_FAKE${counter}` },
        from: wid(from, server),
        type,
        body: text,
        t: Math.floor(Date.now() / 1000),
        isNewMsg: true,
        ...(group ? { author: wid(from, 'c.us') } : {}),
      };
      chat.models.push(msg);
      listeners.forEach((fn) => fn(msg));
      return chatId;
    },
  };

  // The page that "click to chat" links open: web.whatsapp.com/send?phone=NUMBER
  if (location.pathname === '/send') {
    const phone = (params.get('phone') || '').replace(/\D/g, '');
    if (cfg.registered.includes(phone)) {
      if (!cfg.sendUrlNever) setTimeout(() => chatFor(`${phone}@c.us`), cfg.sendUrlDelayMs);
    } else {
      const dialog = document.createElement('div');
      dialog.setAttribute('role', 'dialog');
      dialog.textContent = 'Phone number shared via url is invalid.';
      document.body.appendChild(dialog);
    }
  }

  document.title = `Fake WhatsApp (${me})`;
})();
