/**
 * Popup. Does no network itself: everything goes through background.js
 * (GET_STATUS, SAVE_KEY, CLEAR_KEY, SET_AUTO_REPLY, RESUME).
 * Text from the gateway (business name etc.) is only ever set with textContent.
 */
(function () {
  'use strict';

  const GW = self.WAGatewayClient;
  const $ = (id) => document.getElementById(id);

  const el = {
    backendBadge: $('backendBadge'),
    backendText: $('backendText'),
    apiKeyInput: $('apiKeyInput'),
    saveBtn: $('saveBtn'),
    keyHint: $('keyHint'),
    keyMessage: $('keyMessage'),
    connectedSection: $('connectedSection'),
    connectedName: $('connectedName'),
    connectedNumber: $('connectedNumber'),
    numberWarning: $('numberWarning'),
    removeKeyBtn: $('removeKeyBtn'),
    autoReplyToggle: $('autoReplyToggle'),
    resumeBtn: $('resumeBtn'),
    lastError: $('lastError'),
  };

  function send(message) {
    return chrome.runtime.sendMessage(message).catch(() => null);
  }

  function setVisible(node, visible) {
    node.classList.toggle('hidden', !visible);
  }

  function setMessage(text, kind) {
    el.keyMessage.textContent = text || '';
    el.keyMessage.className = `msg ${kind || ''}`.trim();
    setVisible(el.keyMessage, Boolean(text));
  }

  function formatTime(epochMs) {
    return new Date(epochMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function renderBackend(status) {
    const online = status && status.backend === 'online';
    el.backendBadge.className = `status-badge ${online ? 'online' : 'offline'}`;
    const env = status && status.gatewayEnv && status.gatewayEnv !== 'prod' ? ` (${status.gatewayEnv})` : '';
    el.backendText.textContent = (online ? 'Online' : 'Offline') + env;
  }

  function renderKey(status) {
    const hasKey = Boolean(status && status.hasKey);
    setVisible(el.keyHint, hasKey);
    el.keyHint.textContent = hasKey ? `Saved key: ${status.keyHint}` : '';
    setVisible(el.connectedSection, hasKey && Boolean(status.vendor));
    if (hasKey && status.vendor) {
      el.connectedName.textContent = status.vendor.businessName;
      el.connectedNumber.textContent = GW.formatPhone(status.vendor.whatsappNumber);
      const warn = Boolean(status.waNumberMismatch);
      setVisible(el.numberWarning, warn);
      el.numberWarning.textContent = warn
        ? `This WhatsApp is ${GW.formatPhone(status.waNumber)}, not the registered number.`
        : '';
    }
  }

  function renderAutoReply(status) {
    el.autoReplyToggle.checked = Boolean(status && status.autoReplyEnabled);
    el.autoReplyToggle.disabled = !(status && status.hasKey);
  }

  function renderLastError(status) {
    const paused = status && status.paused;
    const last = status && status.lastError;
    let text = 'None';
    if (paused) {
      text = paused.message;
    } else if (last) {
      text = `${last.text} (${formatTime(last.at)})`;
    } else if (status && !status.hasKey) {
      text = 'Enter your API key to start.';
    }
    el.lastError.textContent = text;
    el.lastError.className = `last-error${paused || last ? '' : ' none'}`;
    setVisible(el.resumeBtn, Boolean(paused));
  }

  function render(status) {
    renderBackend(status);
    renderKey(status);
    renderAutoReply(status);
    renderLastError(status);
  }

  async function refresh() {
    render(await send({ type: 'GET_STATUS' }));
  }

  el.saveBtn.addEventListener('click', async () => {
    const apiKey = el.apiKeyInput.value.trim();
    if (!apiKey) {
      setMessage('Enter an API key.', 'bad');
      return;
    }
    el.saveBtn.disabled = true;
    setMessage('Checking key...', '');
    const result = await send({ type: 'SAVE_KEY', apiKey });
    el.saveBtn.disabled = false;
    if (result && result.ok) {
      el.apiKeyInput.value = '';
      setMessage('', '');
      await refresh();
    } else {
      setMessage((result && result.error) || 'Could not save the key.', 'bad');
    }
  });

  el.apiKeyInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') el.saveBtn.click();
  });

  el.removeKeyBtn.addEventListener('click', async () => {
    await send({ type: 'CLEAR_KEY' });
    setMessage('', '');
    await refresh();
  });

  el.autoReplyToggle.addEventListener('change', async () => {
    await send({ type: 'SET_AUTO_REPLY', enabled: el.autoReplyToggle.checked });
    await refresh();
  });

  el.resumeBtn.addEventListener('click', async () => {
    await send({ type: 'RESUME' });
    await refresh();
  });

  refresh();
})();
