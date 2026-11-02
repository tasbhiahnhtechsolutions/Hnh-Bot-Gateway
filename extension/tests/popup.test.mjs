import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

import { EXTENSION_DIR, flush } from './helpers.mjs';

const IDS = [
  'backendBadge', 'backendText', 'apiKeyInput', 'saveBtn', 'keyHint', 'keyMessage', 'connectedSection',
  'connectedName', 'connectedNumber', 'numberWarning', 'removeKeyBtn', 'autoReplyToggle', 'resumeBtn', 'lastError',
];

function fakeElement() {
  const classes = new Set();
  const handlers = {};
  return {
    textContent: '', value: '', checked: false, disabled: false,
    classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), contains: (name) => classes.has(name) },
    set className(value) { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
    get className() { return [...classes].join(' '); },
    addEventListener: (type, fn) => { handlers[type] = fn; },
    click: () => handlers.click?.(),
    fire: (type, event = {}) => handlers[type]?.(event),
    hidden: () => classes.has('hidden'),
  };
}

async function openPopup(status, onSend = async () => ({ ok: true })) {
  const elements = Object.fromEntries(IDS.map((id) => [id, fakeElement()]));
  const sent = [];
  let current = status;
  const chrome = {
    runtime: {
      sendMessage: async (message) => {
        sent.push(JSON.parse(JSON.stringify(message)));
        if (message.type === 'GET_STATUS') return current;
        return onSend(message, (next) => { current = next; });
      },
    },
  };
  const context = vm.createContext({ chrome, document: { getElementById: (id) => elements[id] }, console });
  context.self = context;
  vm.runInContext(readFileSync(join(EXTENSION_DIR, 'gateway-client.js'), 'utf8'), context);
  vm.runInContext(readFileSync(join(EXTENSION_DIR, 'popup.js'), 'utf8'), context);
  await flush();
  return { elements, sent };
}

const connected = {
  backend: 'online', hasKey: true, keyHint: 'wab_live_k3f9a1xz_\u2022\u2022\u2022\u2022', autoReplyEnabled: true,
  vendor: { businessName: 'Ali Boutique', whatsappNumber: '923001112222', keyKind: 'live' },
  paused: null, lastError: null, waNumber: '923001112222', waNumberMismatch: false, gatewayEnv: 'prod',
};

test('shows backend, who is connected and the toggle', async () => {
  const { elements: e } = await openPopup(connected);
  assert.equal(e.backendText.textContent, 'Online');
  assert.ok(e.backendBadge.className.includes('online'));
  assert.equal(e.connectedName.textContent, 'Ali Boutique');
  assert.equal(e.connectedNumber.textContent, '+923001112222');
  assert.equal(e.connectedSection.hidden(), false);
  assert.equal(e.autoReplyToggle.checked, true);
  assert.equal(e.autoReplyToggle.disabled, false);
  assert.equal(e.lastError.textContent, 'None');
  assert.equal(e.resumeBtn.hidden(), true);
  assert.equal(e.apiKeyInput.value, '', 'the saved key is never put back into the input');
  assert.ok(e.keyHint.textContent.includes('wab_live_k3f9a1xz_'));
});

test('without a key: offline backend, no "connected", toggle disabled, a hint', async () => {
  const { elements: e } = await openPopup({ backend: 'offline', hasKey: false, keyHint: '', autoReplyEnabled: true, vendor: null, paused: null, lastError: null, gatewayEnv: 'dev' });
  assert.equal(e.backendText.textContent, 'Offline (dev)');
  assert.equal(e.connectedSection.hidden(), true);
  assert.equal(e.autoReplyToggle.disabled, true);
  assert.match(e.lastError.textContent, /Enter your API key/);
});

test('shows the last error with its time, and a pause with a Resume button', async () => {
  const withError = { ...connected, lastError: { text: 'Gateway or bot error 504 (BOT_TIMEOUT). No reply sent.', severity: 'yellow', at: Date.now() } };
  const a = await openPopup(withError);
  assert.match(a.elements.lastError.textContent, /504 \(BOT_TIMEOUT\)/);

  const paused = { ...connected, paused: { code: 'INVALID_KEY', message: 'API key rejected. Enter a valid key and save.', until: null } };
  const b = await openPopup(paused);
  assert.equal(b.elements.lastError.textContent, 'API key rejected. Enter a valid key and save.');
  assert.equal(b.elements.resumeBtn.hidden(), false);
  b.elements.resumeBtn.click();
  await flush();
  assert.ok(b.sent.some((m) => m.type === 'RESUME'));
});

test('warns when this WhatsApp is not the registered number', async () => {
  const { elements: e } = await openPopup({ ...connected, waNumber: '923335556666', waNumberMismatch: true });
  assert.equal(e.numberWarning.hidden(), false);
  assert.match(e.numberWarning.textContent, /\+923335556666/);
});

test('Save sends the key to the background, then shows "Invalid key" or refreshes', async () => {
  const bad = await openPopup({ ...connected, hasKey: false, vendor: null }, async () => ({ ok: false, error: 'Invalid key.' }));
  bad.elements.apiKeyInput.value = ' wab_live_nope_nope ';
  bad.elements.saveBtn.click();
  await flush();
  assert.deepEqual(bad.sent.find((m) => m.type === 'SAVE_KEY'), { type: 'SAVE_KEY', apiKey: 'wab_live_nope_nope' });
  assert.equal(bad.elements.keyMessage.textContent, 'Invalid key.');
  assert.equal(bad.elements.keyMessage.hidden(), false);
  assert.equal(bad.elements.saveBtn.disabled, false);

  const good = await openPopup({ ...connected, hasKey: false, vendor: null }, async (_message, setStatus) => {
    setStatus(connected);
    return { ok: true, vendor: connected.vendor };
  });
  good.elements.apiKeyInput.value = 'wab_live_k3f9a1xz_SECRET';
  good.elements.saveBtn.click();
  await flush();
  await flush();
  assert.equal(good.elements.apiKeyInput.value, '', 'the typed key is cleared after saving');
  assert.equal(good.elements.connectedName.textContent, 'Ali Boutique');
  assert.equal(good.elements.keyMessage.hidden(), true);
});

test('Save with an empty field asks for a key without calling the background', async () => {
  const { elements: e, sent } = await openPopup({ ...connected, hasKey: false, vendor: null });
  e.saveBtn.click();
  await flush();
  assert.equal(e.keyMessage.textContent, 'Enter an API key.');
  assert.ok(!sent.some((m) => m.type === 'SAVE_KEY'));
});

test('the toggle and "Remove key" go through the background', async () => {
  const { elements: e, sent } = await openPopup(connected);
  e.autoReplyToggle.checked = false;
  e.autoReplyToggle.fire('change');
  e.removeKeyBtn.click();
  await flush();
  assert.ok(sent.some((m) => m.type === 'SET_AUTO_REPLY' && m.enabled === false));
  assert.ok(sent.some((m) => m.type === 'CLEAR_KEY'));
});

test('the popup does not do any network itself and has no dashboard, health or log toggle', () => {
  const popupJs = readFileSync(join(EXTENSION_DIR, 'popup.js'), 'utf8');
  const popupHtml = readFileSync(join(EXTENSION_DIR, 'popup.html'), 'utf8');
  assert.doesNotMatch(popupJs, /fetch\(|XMLHttpRequest|HEALTH_URL|innerHTML/);
  assert.doesNotMatch(popupHtml, /Log Messages|Reset &amp; Re-Sync|Reset & Re-Sync|serverLink|http:\/\//);
});
