/* Phase 2 Spike (plan section 11): find out what the REAL WhatsApp Web build supports.
 *
 * Paste this whole file into the DevTools console of a logged-in https://web.whatsapp.com tab
 * (a TEST account/number only). Then follow spike/README.md.
 *
 * Rules this script follows:
 *  - Everything is read-only EXCEPT send() and tryInteractive().
 *    Those refuse to run unless you pass { confirm: 'SEND' }, and stop after 5 sends per page load.
 *  - Nothing leaves the browser: this file contains no network code at all.
 *  - The report never contains message text, chat names or full phone numbers (numbers are
 *    masked to the last 4 digits). It is safe to paste into the spike report.
 *  - A probe that fails is recorded as a failure. Nothing is faked or guessed.
 */
(function () {
  'use strict';

  if (window.WASpike && window.WASpike.__loaded) {
    console.log('[Spike] already loaded. Reload the page to start fresh.');
    return;
  }

  const MAX_SENDS = 5;
  let sends = 0;
  const startedAt = Date.now();
  const R = { startedAt: new Date(startedAt).toISOString(), probes: {} };
  const marker = `spike-${startedAt}`;
  window.__spikeMarker = marker; // a page reload wipes this: send()/openChat() report it

  const log = (...args) => console.log('%c[Spike]', 'color:#0a7d3b;font-weight:bold', ...args);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const digits = (value) => String(value || '').replace(/\D/g, '');
  const mask = (value) => {
    const d = digits(value);
    return d.length > 4 ? `${'*'.repeat(d.length - 4)}${d.slice(-4)}` : d;
  };
  const errText = (err) => String((err && err.message) || err || 'unknown error').slice(0, 300);

  function req(name) {
    try {
      return typeof window.require === 'function' ? window.require(name) : null;
    } catch (_) {
      return null;
    }
  }

  function firstModule(names) {
    for (const name of names) {
      const mod = req(name);
      if (mod) return { name, mod };
    }
    return null;
  }

  function allModuleNames() {
    try {
      const map = window.require('__debug')?.modulesMap;
      return map ? Object.keys(map) : [];
    } catch (_) {
      return [];
    }
  }

  function findModuleNames(regex, cap = 60) {
    return allModuleNames().filter((n) => regex.test(n)).slice(0, cap);
  }

  function exportKeys(mod, cap = 40) {
    try {
      const target = mod && typeof mod === 'object' ? mod : {};
      return Object.keys(target).slice(0, cap);
    } catch (_) {
      return [];
    }
  }

  // Only primitives: never dump objects that may hold chats, contacts or messages.
  function primitive(value) {
    const t = typeof value;
    if (value === null || value === undefined) return null;
    if (t === 'string') return value.slice(0, 60);
    if (t === 'number' || t === 'boolean') return value;
    return `[${t}]`;
  }

  function note(key, value) {
    R.probes[key] = value;
    return value;
  }

  async function guarded(key, fn) {
    try {
      return note(key, { ok: true, ...(await fn()) });
    } catch (err) {
      return note(key, { ok: false, error: errText(err) });
    }
  }

  function chatCollection() {
    const mod = req('WAWebChatCollection') || req('WAWebCollections');
    return mod?.ChatCollection || mod?.default?.ChatCollection || mod?.default || mod;
  }

  function allChats() {
    const coll = chatCollection();
    try {
      return typeof coll?.getModelsArray === 'function' ? coll.getModelsArray() : [];
    } catch (_) {
      return [];
    }
  }

  function serverOf(chat) {
    const id = chat?.id?._serialized || String(chat?.id || '');
    return id.includes('@') ? id.split('@')[1] : 'unknown';
  }

  function widFor(number, server = 'c.us') {
    const factory = req('WAWebWidFactory');
    const id = `${digits(number)}@${server}`;
    return factory?.createWid ? factory.createWid(id) : { _serialized: id, user: digits(number), server };
  }

  // ---------------------------------------------------------------------------------------
  // 1. Environment: which internal modules exist in this build
  // ---------------------------------------------------------------------------------------

  const KNOWN_MODULES = [
    'WAWebMsgCollection', 'WAWebChatCollection', 'WAWebCollections', 'WAWebContactCollection',
    'WAWebUserPrefsMeUser', 'WAWebSendMsgChatAction', 'WAWebMsgKey', 'WAWebWidFactory', 'WAWebCmd',
    'WAWebFindChatAction', 'WAWebQueryExist', 'WAWebSocketModel', 'WAWebStreamModel',
  ];

  const TOPICS = {
    connection: /Socket|Stream(Model|Info)|Conn(ection)?State|Offline|Presence/i,
    exists: /QueryExist|ExistsJob|IsWhatsApp|CheckNumber|ContactExist/i,
    newChat: /FindChat|CreateChat|OpenChat|ChatOpen|Cmd$/i,
    lid: /Lid|PnLid|LidPn|PhoneNumber(Util|Mapping)/i,
    ack: /Ack|MsgInfo|Receipt/i,
    interactive: /Poll|Button|ListMessage|Interactive|Template|Carousel/i,
  };

  function env() {
    return note('env', {
      ok: true,
      userAgent: navigator.userAgent,
      waVersion: primitive(window.Debug?.VERSION),
      hasRequire: typeof window.require === 'function',
      moduleRegistryReadable: allModuleNames().length > 0,
      moduleCount: allModuleNames().length,
      known: Object.fromEntries(
        KNOWN_MODULES.map((name) => {
          const mod = req(name);
          return [name, mod ? { present: true, keys: exportKeys(mod, 25) } : { present: false }];
        }),
      ),
      discovered: Object.fromEntries(Object.entries(TOPICS).map(([k, re]) => [k, findModuleNames(re)])),
    });
  }

  // ---------------------------------------------------------------------------------------
  // 2. Account and chat list shape (counts only)
  // ---------------------------------------------------------------------------------------

  function account() {
    return guarded('account', async () => {
      const prefs = req('WAWebUserPrefsMeUser');
      const me = prefs?.getMaybeMePnUser?.() || prefs?.getMaybeMeUser?.() || prefs?.getMeUser?.();
      const chats = allChats();
      const byServer = {};
      chats.forEach((chat) => {
        const s = serverOf(chat);
        byServer[s] = (byServer[s] || 0) + 1;
      });
      return {
        ownNumberMasked: mask(me?.user || me?._serialized),
        hasLidIdentity: Boolean(prefs?.getMaybeMeLidUser?.()),
        chatCount: chats.length,
        chatsByServer: byServer,
      };
    });
  }

  // ---------------------------------------------------------------------------------------
  // 3. Connection state: what tells us connected / offline / logged out?
  // ---------------------------------------------------------------------------------------

  function connectionSnapshot() {
    const socket = req('WAWebSocketModel')?.Socket;
    const stream = req('WAWebStreamModel')?.Stream;
    const read = (obj, key) => {
      try {
        return primitive(typeof obj?.[key] === 'function' ? obj[key]() : obj?.[key]);
      } catch (_) {
        return null;
      }
    };
    return {
      navigatorOnLine: navigator.onLine,
      socketState: read(socket, 'state'),
      socketStream: read(socket, 'stream'),
      socketHasSynced: read(socket, 'hasSynced'),
      streamMode: read(stream, 'mode'),
      streamInfo: read(stream, 'info'),
      streamDisplayInfo: read(stream, 'displayInfo'),
      socketModulePresent: Boolean(socket),
      streamModulePresent: Boolean(stream),
    };
  }

  function connection() {
    return guarded('connection', async () => ({ snapshot: connectionSnapshot() }));
  }

  /** Poll for `seconds` and log every change. Toggle Wi-Fi / phone / log out while it runs. */
  async function watchConnection(seconds = 90) {
    const timeline = [];
    let last = '';
    const end = Date.now() + seconds * 1000;
    log(`watching the connection for ${seconds}s: now switch your network off/on, put the phone offline, etc.`);
    while (Date.now() < end) {
      const snap = connectionSnapshot();
      const key = JSON.stringify(snap);
      if (key !== last) {
        last = key;
        const entry = { atSec: Math.round((Date.now() - startedAt) / 1000), ...snap };
        timeline.push(entry);
        log('connection changed:', entry);
      }
      await sleep(1000);
    }
    return note('connectionTimeline', { ok: true, changes: timeline });
  }

  // ---------------------------------------------------------------------------------------
  // 4. Is this number on WhatsApp?
  // ---------------------------------------------------------------------------------------

  function summarizeExists(result) {
    if (result === null || result === undefined) return { answer: 'null/undefined' };
    if (typeof result !== 'object') return { answer: primitive(result) };
    return {
      answer: 'object',
      keys: exportKeys(result, 12),
      hasWid: Boolean(result.wid || result.id),
      status: primitive(result.status ?? result.exists ?? result.isInAddressBook),
    };
  }

  function exists(number) {
    return guarded(`exists:${mask(number)}`, async () => {
      const wid = widFor(number);
      const found = firstModule(['WAWebQueryExist', 'WAWebQueryExistsJob'].concat(findModuleNames(TOPICS.exists, 5)));
      if (!found) return { method: 'none', note: 'no existence-check module found, see env.discovered.exists' };
      const fns = ['queryExists', 'queryExist', 'default'].filter((k) => typeof found.mod[k] === 'function');
      const target = fns[0] ? found.mod[fns[0]] : typeof found.mod === 'function' ? found.mod : null;
      if (!target) return { method: 'none', module: found.name, keys: exportKeys(found.mod) };
      const started = Date.now();
      const result = await target(wid);
      return { method: `${found.name}.${fns[0] || '(module)'}`, ms: Date.now() - started, ...summarizeExists(result) };
    });
  }

  // ---------------------------------------------------------------------------------------
  // 5. Open / create a chat for a number that is not saved (primary method of plan 8.8 step 5b)
  // ---------------------------------------------------------------------------------------

  async function openChat(number, { open = false } = {}) {
    return guarded(`openChat:${mask(number)}`, async () => {
      const wid = widFor(number);
      const before = chatCollection()?.get?.(wid._serialized) || null;
      const found = firstModule(['WAWebFindChatAction', 'WAWebFindChat'].concat(findModuleNames(TOPICS.newChat, 5)));
      const fn = found && ['findOrCreateLatestChat', 'findChat', 'createChat'].find((k) => typeof found.mod[k] === 'function');
      if (!found || !fn) return { method: 'none', existedBefore: Boolean(before), note: 'no create-chat function found' };

      const started = Date.now();
      const result = await found.mod[fn](wid);
      const chat = result?.chat || result;
      const created = Boolean(chat && (chat.id || chat._serialized));
      let opened = false;
      if (created && open) {
        const Cmd = req('WAWebCmd')?.Cmd || req('WAWebCmd')?.default;
        if (Cmd?.openChatBottom) {
          await Cmd.openChatBottom(chat);
          opened = true;
        }
      }
      return {
        method: `${found.name}.${fn}`,
        ms: Date.now() - started,
        existedBefore: Boolean(before),
        chatReturned: created,
        chatServer: created ? serverOf(chat) : null,
        openedInUi: opened,
        pageReloaded: window.__spikeMarker !== marker,
      };
    });
  }

  // ---------------------------------------------------------------------------------------
  // 6. Real send + ack timeline. TEST NUMBER ONLY.
  // ---------------------------------------------------------------------------------------

  function requireConfirm(options, what) {
    if (!options || options.confirm !== 'SEND') {
      throw new Error(`${what} sends a REAL WhatsApp message. Pass { confirm: 'SEND' } to allow it.`);
    }
    if (sends >= MAX_SENDS) throw new Error(`limit of ${MAX_SENDS} sends per page load reached; reload to reset`);
  }

  async function newMsgKey(chat) {
    const keyMod = req('WAWebMsgKey');
    const MsgKey = keyMod?.default || keyMod;
    const prefs = req('WAWebUserPrefsMeUser');
    const me = prefs?.getMaybeMePnUser?.() || prefs?.getMaybeMeUser?.();
    let id = '3EB0' + Array.from({ length: 16 }, () => Math.floor(Math.random() * 16).toString(16)).join('').toUpperCase();
    if (typeof MsgKey?.newId === 'function') {
      try { id = await MsgKey.newId(); } catch (_) { /* keep random id */ }
    }
    if (typeof MsgKey === 'function') {
      try { return { key: new MsgKey({ from: me, to: chat.id, id, selfDir: 'out' }), me }; } catch (_) { /* fall through */ }
    }
    return { key: { fromMe: true, remote: chat.id, id, self: 'out', _serialized: `true_${chat.id._serialized}_${id}` }, me };
  }

  async function sendMessageData(chat, extra) {
    const action = req('WAWebSendMsgChatAction');
    if (!action?.addAndSendMsgToChat) throw new Error('WAWebSendMsgChatAction.addAndSendMsgToChat not found');
    const { key, me } = await newMsgKey(chat);
    const data = {
      id: key, type: 'chat', subtype: null, t: Math.floor(Date.now() / 1000),
      from: me, to: chat.id, self: 'out', ack: 0, local: true, isNewMsg: true,
      star: false, isForwarded: false, mentionedJidList: [], ...extra,
    };
    const result = action.addAndSendMsgToChat(chat, data);
    const parts = Array.isArray(result) ? result : [result];
    const resolved = await Promise.all(parts.map((p) => Promise.resolve(p).catch((e) => ({ __error: errText(e) }))));
    return { data, resolved };
  }

  function findSentMessage(chat, probeBody) {
    try {
      const msgs = chat.msgs?.getModelsArray?.() || [];
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if ((m.id?.fromMe || m.fromMe) && (m.body === probeBody || m.pollName === probeBody)) return m;
      }
    } catch (_) { /* ignore */ }
    return null;
  }

  async function watchAck(msg, seconds) {
    const timeline = [];
    const t0 = Date.now();
    let last = null;
    const record = () => {
      const ack = msg.ack;
      if (ack === last) return;
      last = ack;
      timeline.push({ ms: Date.now() - t0, ack: primitive(ack) });
      log(`ack ${ack} after ${Date.now() - t0}ms`);
    };
    // The event shows how fast WhatsApp tells us; polling is the fallback if it stays silent.
    try { msg.on?.('change:ack', record); } catch (_) { /* polling below still works */ }
    while (Date.now() - t0 < seconds * 1000) {
      record();
      await sleep(100);
    }
    try { msg.off?.('change:ack', record); } catch (_) { /* ignore */ }
    return timeline;
  }

  async function send(number, text, options = {}) {
    return guarded(`send:${mask(number)}`, async () => {
      requireConfirm(options, 'send()');
      sends += 1;
      const opened = await openChat(number);
      const wid = widFor(number);
      const chat = chatCollection()?.get?.(wid._serialized);
      if (!chat) return { sent: false, note: 'no chat model after openChat, see probes.openChat', openChat: opened };

      const body = text || `[spike test ${new Date().toISOString().slice(11, 19)}]`;
      const started = Date.now();
      const { resolved } = await sendMessageData(chat, { body });
      const error = resolved.find((r) => r && r.__error);
      if (error) return { sent: false, error: error.__error };

      const msg = findSentMessage(chat, body);
      const ackTimeline = msg ? await watchAck(msg, options.ackSeconds || 20) : [];
      const firstAck = ackTimeline.find((e) => typeof e.ack === 'number' && e.ack >= 1);
      return {
        sent: true,
        ms: Date.now() - started,
        messageModelFound: Boolean(msg),
        ackTimeline,
        reachedServerAck: Boolean(firstAck),
        msToServerAck: firstAck ? firstAck.ms : null,
        maxAck: ackTimeline.reduce((m, e) => (typeof e.ack === 'number' ? Math.max(m, e.ack) : m), -1),
        pageReloaded: window.__spikeMarker !== marker,
      };
    });
  }

  // ---------------------------------------------------------------------------------------
  // 7. The invalid-number popup after opening web.whatsapp.com/send?phone=NUMBER (fallback 5c)
  // ---------------------------------------------------------------------------------------

  function detectInvalidPopup() {
    const candidates = document.querySelectorAll(
      '[role="dialog"], [data-animate-modal-popup], [data-testid*="popup"], [aria-modal="true"]',
    );
    const composer = document.querySelector('footer div[contenteditable="true"], div[contenteditable="true"][data-tab="10"]');
    const found = Array.from(candidates).slice(0, 5).map((el) => ({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role'),
      dataAttributes: el.getAttributeNames().filter((n) => n.startsWith('data-')),
      textExcerpt: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120),
      buttons: Array.from(el.querySelectorAll('button')).slice(0, 4).map((b) => (b.textContent || '').trim().slice(0, 30)),
    }));
    return note('invalidPopup', {
      ok: true,
      url: location.pathname + (location.search ? '?...' : ''),
      popupCount: found.length,
      popups: found,
      composerPresent: Boolean(composer),
    });
  }

  // ---------------------------------------------------------------------------------------
  // 8. The @lid problem
  // ---------------------------------------------------------------------------------------

  function phoneFromLidViaModules(wid) {
    const attempts = [];
    for (const name of findModuleNames(TOPICS.lid, 12)) {
      const mod = req(name);
      for (const fnName of exportKeys(mod, 60).filter((k) => /phone|pn/i.test(k) && typeof mod[k] === 'function')) {
        try {
          const out = mod[fnName](wid);
          const value = out && typeof out.then === 'function' ? null : out;
          const d = digits(value?.user || value?._serialized || value);
          if (d.length >= 8 && d.length <= 15) attempts.push({ via: `${name}.${fnName}`, phoneMasked: mask(d) });
        } catch (_) { /* try the next one */ }
      }
    }
    return attempts;
  }

  function lid(number) {
    return guarded('lid', async () => {
      const lidChats = allChats().filter((c) => serverOf(c) === 'lid').slice(0, 20);
      const rows = lidChats.map((chat) => {
        const contact = chat.contact;
        const direct = digits(contact?.phoneNumber?._serialized || contact?.phoneNumber?.user || contact?.phoneNumber);
        const viaModules = phoneFromLidViaModules(chat.id);
        return {
          lidMasked: mask(chat.id?.user),
          phoneOnContact: direct.length >= 8 ? mask(direct) : null,
          viaModules,
          resolved: direct.length >= 8 || viaModules.length > 0,
        };
      });
      const result = {
        lidChatsChecked: rows.length,
        resolvedCount: rows.filter((r) => r.resolved).length,
        rows,
      };
      if (number) {
        const n = digits(number);
        const direct = chatCollection()?.get?.(`${n}@c.us`) || null;
        const viaLid = allChats().find((c) => serverOf(c) === 'lid' && digits(c.contact?.phoneNumber?._serialized || c.contact?.phoneNumber?.user) === n);
        result.lookupByNumber = { number: mask(n), foundAsCus: Boolean(direct), foundThroughLidChat: Boolean(viaLid) };
      }
      return result;
    });
  }

  // ---------------------------------------------------------------------------------------
  // 9. Interactive messages (buttons, polls). Discovery is read-only; attempts need confirm.
  // ---------------------------------------------------------------------------------------

  function interactive() {
    return guarded('interactive', async () => {
      const names = findModuleNames(TOPICS.interactive, 40);
      return {
        modules: names.map((name) => ({
          name,
          sendLikeFunctions: exportKeys(req(name), 60).filter((k) => /send|create/i.test(k)),
        })),
      };
    });
  }

  /** EXPERIMENTAL. Same payload fields the old whatsapp-web.js used. Failure is an expected result. */
  async function tryInteractive(number, kind, options = {}) {
    return guarded(`tryInteractive:${kind}:${mask(number)}`, async () => {
      requireConfirm(options, 'tryInteractive()');
      if (!['buttons', 'poll'].includes(kind)) throw new Error("kind must be 'buttons' or 'poll'");
      sends += 1;
      await openChat(number);
      const chat = chatCollection()?.get?.(widFor(number)._serialized);
      if (!chat) return { sent: false, note: 'no chat model' };
      const label = `[spike ${kind}]`;
      const extra = kind === 'buttons'
        ? {
            body: label, isDynamicReplyButtonsMsg: true,
            dynamicReplyButtons: [
              { buttonId: 'spike-yes', buttonText: { displayText: 'Yes' }, type: 1 },
              { buttonId: 'spike-no', buttonText: { displayText: 'No' }, type: 1 },
            ],
          }
        : {
            type: 'poll_creation', pollName: label, pollSelectableOptionsCount: 1,
            pollOptions: [{ name: 'Yes', localId: 0 }, { name: 'No', localId: 1 }],
            messageSecret: crypto.getRandomValues(new Uint8Array(32)),
          };
      const { resolved } = await sendMessageData(chat, extra);
      const error = resolved.find((r) => r && r.__error);
      const msg = findSentMessage(chat, label);
      const ackTimeline = msg ? await watchAck(msg, options.ackSeconds || 15) : [];
      return {
        sent: !error,
        error: error ? error.__error : null,
        messageModelFound: Boolean(msg),
        ackTimeline,
        humanCheckNeeded: 'Look at the recipient phone: did it show real buttons/a poll, plain text, or nothing?',
      };
    });
  }

  // ---------------------------------------------------------------------------------------

  /** All read-only probes. Sends nothing. Run this first. */
  async function runSafe(number) {
    env();
    await account();
    await connection();
    await lid(number);
    await interactive();
    if (number) await exists(number);
    log('read-only probes done. Next: WASpike.report()');
    return report();
  }

  function report() {
    return JSON.parse(JSON.stringify({ ...R, finishedAt: new Date().toISOString(), sendsThisPage: sends }));
  }

  function copyReport() {
    const text = JSON.stringify(report(), null, 2);
    if (typeof window.copy === 'function') {
      window.copy(text);
      log('report copied to the clipboard');
    } else {
      console.log(text);
    }
    return text;
  }

  function help() {
    log(`
  WASpike.runSafe('92300XXXXXXX')          read-only probes (no sends). Run first.
  WASpike.watchConnection(90)              log connection changes while you toggle network/phone
  WASpike.exists('92300XXXXXXX')           is this number on WhatsApp?
  WASpike.openChat('92300XXXXXXX')         create/open a chat without sending
  WASpike.send('92300XXXXXXX', 'hi', {confirm:'SEND'})   REAL message + ack timeline (test number!)
  WASpike.detectInvalidPopup()             after opening /send?phone=... in this tab
  WASpike.lid('92300XXXXXXX')              @lid mapping check
  WASpike.tryInteractive('92300XXXXXXX','buttons'|'poll',{confirm:'SEND'})   experimental
  WASpike.report() / WASpike.copyReport()  JSON for the spike report (numbers masked)`);
  }

  window.WASpike = {
    __loaded: true, help, env, account, connection, watchConnection, exists, openChat, send,
    detectInvalidPopup, lid, interactive, tryInteractive, runSafe, report, copyReport,
  };
  log('loaded. Type WASpike.help()');
})();
