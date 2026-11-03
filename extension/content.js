/**
 * WhatsApp Auto-Reply Gateway - Content Script Bridge.
 * Injects injected.js into the page's MAIN world and relays messages between
 * the page and background.js. Whether the plugin is "active" (key saved, not
 * paused, auto-reply on) is decided by background.js and only forwarded here.
 */

(function () {
  'use strict';

  const LOG = '[WA Gateway]';

  // Helper to safely check if the extension context is still valid
  function isExtensionValid() {
    try {
      return Boolean(typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id);
    } catch (_) {
      return false;
    }
  }

  // Helper to safely send messages without throwing 'Extension context invalidated'
  function safeSendMessage(msg, callback) {
    try {
      if (!isExtensionValid()) return;
      chrome.runtime.sendMessage(msg, (res) => {
        try {
          if (chrome.runtime?.lastError) return;
          if (typeof callback === 'function') callback(res);
        } catch (_) {
          // Extension context invalidated during callback
        }
      });
    } catch (_) {
      // Suppress error if extension context was invalidated
    }
  }

  function sendControlToPage(active) {
    window.postMessage({ source: 'WA_LOGGER_CONTROL', active: Boolean(active) }, '*');
  }

  function requestControl() {
    safeSendMessage({ type: 'GET_CONTROL' }, (res) => {
      if (res && typeof res.active === 'boolean') sendControlToPage(res.active);
    });
  }

  // 1. Messages from background.js
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg) return;

    if (msg.type === 'CONTROL') {
      sendControlToPage(msg.active);
      sendResponse({ received: true });
      return;
    }

    // A job to send for the vendor (background.js -> page)
    if (msg.type === 'OUTBOUND_COMMAND') {
      window.postMessage({ source: 'WA_GATEWAY_OUTBOUND_COMMAND', command: msg.command }, '*');
      sendResponse({ forwarded: true });
      return;
    }

    // Reply bubbles from the gateway (via background.js)
    if (msg.type === 'WA_LOGGER_AI_RESPONSE') {
      window.postMessage({ source: 'WA_LOGGER_AI_RESPONSE', ...(msg.data || {}) }, '*');
      sendResponse({ forwarded: true });
    }
  });

  // 2. Inject injected.js into the page's MAIN context
  function injectMainScript() {
    try {
      const script = document.createElement('script');
      script.src = chrome.runtime.getURL('injected.js');
      script.type = 'text/javascript';
      script.onload = () => script.remove();
      (document.head || document.documentElement).appendChild(script);
    } catch (err) {
      console.warn(`${LOG} Error injecting script:`, err);
    }
  }

  injectMainScript();
  requestControl();

  // 3. Relay messages from injected.js (MAIN world) to background.js
  function handleWindowMessage(event) {
    if (event.source !== window || !event.data) return;

    // If the extension was reloaded, detach so the old script stops reacting.
    if (!isExtensionValid()) {
      window.removeEventListener('message', handleWindowMessage);
      console.warn(`${LOG} Extension was reloaded or updated. Please refresh the WhatsApp Web tab.`);
      return;
    }

    const data = event.data;

    if (data.source === 'WA_LOGGER_INTERNAL_READY') {
      // injected.js just finished hooking: tell it the current control state.
      requestControl();
      // A page that just (re)loaded: a job waiting for it (/send?phone=) may go on.
      safeSendMessage({ type: 'WA_GATEWAY_TAB_READY' });
      return;
    }

    // What WhatsApp Web is doing (connected, logged out, ...), reported every few seconds
    if (data.source === 'WA_GATEWAY_WA_STATE') {
      if (typeof data.state === 'string') safeSendMessage({ type: 'WA_GATEWAY_WA_STATE', state: data.state });
      return;
    }

    // Progress and result of a job to send (sending, url_fallback, result, no_ack)
    if (data.source === 'WA_GATEWAY_OUTBOUND_EVENT' && data.event && typeof data.event === 'object') {
      safeSendMessage({ type: 'WA_GATEWAY_OUTBOUND_EVENT', event: data.event });
      return;
    }

    // Own WhatsApp number (shown in the popup, sent as X-WA-Number)
    if (data.source === 'WA_LOGGER_WA_INFO') {
      if (typeof data.waNumber === 'string' && data.waNumber) {
        safeSendMessage({ type: 'WA_LOGGER_WA_INFO', waNumber: data.waNumber });
      }
      return;
    }

    // One customer burst for the gateway
    if (data.source === 'WA_LOGGER_AI_REQUEST' && data.payload) {
      safeSendMessage({
        type: 'WA_LOGGER_AI_REQUEST',
        requestId: data.requestId,
        waNumber: data.waNumber,
        payload: data.payload,
      });
    }
  }

  window.addEventListener('message', handleWindowMessage);
})();
