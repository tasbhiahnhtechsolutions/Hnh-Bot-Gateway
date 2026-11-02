/* Fake web.whatsapp.com (plan section 7.4). Served instead of the real site by Playwright.
 *
 * It offers the few WhatsApp Web internals the extension uses (window.require modules) and a
 * small control API for the tests (window.__fake). It never touches the network and no real
 * number is involved: "sent" messages only land in the `sent` list.
 */
(function () {
  'use strict';

  const me = new URLSearchParams(location.search).get('me') || '923000000000';
  const listeners = [];
  const chats = new Map();
  const sent = [];
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
      msgs: { getModelsArray: () => models },
    };
    chats.set(chatId, chat);
    return chat;
  }

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
      ChatCollection: { get: (id) => chats.get(id) || null, getModelsArray: () => [...chats.values()] },
    },
    WAWebContactCollection: { ContactCollection: { get: () => null } },
    WAWebUserPrefsMeUser: { getMaybeMePnUser: () => wid(me, 'c.us') },
    WAWebSendMsgChatAction: {
      addAndSendMsgToChat(chat, data) {
        if (window.__fake.failSends) return Promise.reject(new Error('fake send failure'));
        sent.push({ chatId: chat.id._serialized, text: data.body, at: Date.now() });
        chat.models.push({
          id: { fromMe: true, _serialized: `true_${chat.id._serialized}_${++counter}` },
          type: 'chat',
          body: data.body,
          t: Math.floor(Date.now() / 1000),
        });
        return Promise.resolve();
      },
    },
  };

  window.require = (name) => modules[name];

  window.__fake = {
    me,
    hooked: false,
    failSends: false,
    sent: () => sent.map((m) => ({ ...m })),
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

  document.title = `Fake WhatsApp (${me})`;
})();
