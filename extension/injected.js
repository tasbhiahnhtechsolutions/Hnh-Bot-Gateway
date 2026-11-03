/* WhatsApp Auto-Reply Gateway - Injected Script (Store Hook, MAIN world)
* Hooks WhatsApp Web's internal WAWebMsgCollection, groups each customer's 1-on-1
* messages into a burst, hands the burst to the gateway (through content.js and
* background.js) and sends the reply bubbles back into the chat.
* Groups are ignored. Nothing is logged or synced anywhere else.
*/

(function () {
  'use strict';

  console.log('[WA Gateway] Injected script started in MAIN world.');

  let isHooked = false;
  // Switched by background.js through content.js (key saved, not paused, toggle on).
  // Off until the first control message arrives, so nothing is sent too early.
  let autoReplyEnabled = false;
  let attempts = 0;
  const MAX_ATTEMPTS = 60;

  // Real-time cutoff timestamp in seconds (strict cutoff for ignoring historical messages)
  let activationTimeSec = Math.floor(Date.now() / 1000);
  const seenMsgIds = new Set();

  // Quiet time after the last customer message before a burst is sent to the gateway.
  const BURST_WAIT_MS = 7000;

  // Loop guard: texts this script just sent, remembered per chat for a short time.
  // Keyed by "chatId|text" so a reply in one chat never hides the same words from
  // another chat, and it expires so a customer can repeat "ok" two minutes later.
  const SENT_TEXT_TTL_MS = 2 * 60 * 1000;
  const sentByAiTexts = new Map(); // "chatId|text" -> expiry (ms epoch)

  function sentTextKey(chatId, text) {
    return `${chatId}|${text}`;
  }

  function rememberSentText(chatId, text) {
    const now = Date.now();
    sentByAiTexts.set(sentTextKey(chatId, text), now + SENT_TEXT_TTL_MS);
    if (sentByAiTexts.size > 500) {
      sentByAiTexts.forEach((expiry, key) => {
        if (expiry <= now) sentByAiTexts.delete(key);
      });
    }
  }

  function wasSentByUs(chatId, text) {
    const key = sentTextKey(chatId, text);
    const expiry = sentByAiTexts.get(key);
    if (expiry === undefined) return false;
    if (expiry <= Date.now()) {
      sentByAiTexts.delete(key);
      return false;
    }
    return true;
  }

  // Burst buffering map: chatId -> { timer, messages: [], chatObj, lastMsg, meta }
  const dmBurstBuffers = new Map();
  const lastReceivedMsgMap = new Map(); // chatId -> last incoming msg
  const lastMessageAtByChat = new Map(); // chatId -> Date.now() of last incoming msg
  const aiRequestStartedAt = new Map(); // chatId -> Date.now() when AI request was dispatched

  // One gateway request may be in flight per chat that still counts: the latest.
  // A reply whose requestId is no longer the latest (a newer burst was sent for
  // the same chat meanwhile) or that arrives too late is dropped, so the customer
  // never gets old, out-of-order or double answers. The newer request already
  // carries the earlier messages in its chatHistory.
  const MAX_REPLY_AGE_MS = 90 * 1000;
  const latestAiRequest = new Map(); // chatId -> { requestId, startedAt }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function formatTime(unixSec) {
    if (!unixSec) return new Date().toLocaleTimeString();
    return new Date(unixSec * 1000).toLocaleTimeString();
  }

  function formatPhoneNumber(raw) {
    if (!raw || typeof raw !== 'string') return '';
    let cleaned = raw.replace(/@(c\.us|s\.whatsapp\.net|g\.us|lid|newsletter|broadcast)$/i, '').trim();
    if (!cleaned) return '';
    if (cleaned.startsWith('+')) return cleaned;
    const digits = cleaned.replace(/\D/g, '');
    if (digits.length >= 7 && digits.length <= 16) {
      return '+' + digits;
    }
    return cleaned;
  }

  const ALLOWED_MSG_TYPES = new Set([
    'chat',
    'image',
    'video',
    'audio',
    'ptt',
    'document',
    'sticker',
    'location',
    'vcard'
  ]);

  const MAX_MEDIA_BYTES = 15 * 1024 * 1024; // 15 MB
  const SUPPORTED_MEDIA_TYPES = new Set(['image', 'sticker', 'document', 'ptt', 'audio']);

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const res = reader.result;
        if (typeof res === 'string') {
          const commaIdx = res.indexOf(',');
          resolve(commaIdx !== -1 ? res.slice(commaIdx + 1) : res);
        } else {
          resolve(null);
        }
      };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  function bufferToBase64(buffer) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    let binary = '';
    const len = bytes.byteLength;
    const chunkSize = 8192;
    for (let i = 0; i < len; i += chunkSize) {
      const chunk = bytes.subarray(i, Math.min(i + chunkSize, len));
      binary += String.fromCharCode.apply(null, chunk);
    }
    return btoa(binary);
  }

  function formatMediaDisplayText(m) {
    if (!m) return '';
    const type = m.type || 'chat';
    const caption = (m.caption || '').trim();
    const filename = m.filename || '';

    if (type === 'chat') {
      return (m.body || '').trim();
    }
    if (type === 'document') {
      return caption ? `📄 [Document: ${filename || 'File'}] ${caption}` : `📄 [Document: ${filename || 'File'}]`;
    }
    if (type === 'image') {
      return caption ? `🖼️ [Image] ${caption}` : `🖼️ [Image]`;
    }
    if (type === 'ptt') {
      const dur = m.duration ? ` • ${m.duration}s` : '';
      return `🎤 [Voice Message${dur}]`;
    }
    if (type === 'audio') {
      return `🎵 [Audio${filename ? ': ' + filename : ''}]`;
    }
    if (type === 'video') {
      return caption ? `🎬 [Video] ${caption}` : `🎬 [Video]`;
    }
    if (type === 'sticker') {
      return '🏷️ [Sticker]';
    }
    return caption || (filename ? `[File: ${filename}]` : `[${type.toUpperCase()}]`);
  }

  const THUMBNAIL_MSG_TYPES = new Set(['image', 'video', 'document', 'sticker']);

  function isBase64Thumbnail(value) {
    if (typeof value !== 'string') return false;
    const cleaned = value.trim();
    return cleaned.startsWith('/9j/') ||
      cleaned.startsWith('iVBOR') ||
      cleaned.startsWith('R0lGOD') ||
      cleaned.startsWith('UklGR') ||
      cleaned.startsWith('data:image/');
  }

  function extractThumbnailBase64(m) {
    if (!m || !THUMBNAIL_MSG_TYPES.has(m.type)) return null;
    if (!isBase64Thumbnail(m.body)) return null;
    return m.body.replace(/^data:image\/[^;]+;base64,/i, '').trim();
  }

  function detectImageMimeFromBase64(base64) {
    if (!base64 || typeof base64 !== 'string') return 'image/jpeg';
    if (base64.startsWith('/9j/')) return 'image/jpeg';
    if (base64.startsWith('iVBOR')) return 'image/png';
    if (base64.startsWith('UklGR')) return 'image/webp';
    if (base64.startsWith('R0lGOD')) return 'image/gif';
    // ISO-BMFF (HEIC/HEIF) base64 usually carries 'ftyp' a few bytes in.
    if (base64.slice(0, 40).includes('ZnR5c')) return 'image/heic';
    return 'image/jpeg';
  }

  const MIME_BY_EXTENSION = {
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp',
    gif: 'image/gif', heic: 'image/heic', heif: 'image/heif', bmp: 'image/bmp',
    pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    doc: 'application/msword', xls: 'application/vnd.ms-excel',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    txt: 'text/plain', csv: 'text/csv', json: 'application/json',
    mp3: 'audio/mpeg', ogg: 'audio/ogg', opus: 'audio/opus', m4a: 'audio/m4a',
    wav: 'audio/wav', aac: 'audio/aac', flac: 'audio/flac',
    mp4: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm'
  };

  function inferMimeFromFileName(fileName) {
    if (!fileName || typeof fileName !== 'string') return '';
    const ext = (fileName.split('.').pop() || '').toLowerCase();
    return MIME_BY_EXTENSION[ext] || '';
  }

  function getPreviewBlob(msg) {
    const preview = msg?.mediaData?.preview || msg?.mediaObject?.preview;
    if (!preview) return null;
    if (preview instanceof Blob) return preview;
    if (preview._blob instanceof Blob) return preview._blob;
    if (preview.blob instanceof Blob) return preview.blob;
    return null;
  }

  /**
   * In-Memory Media Decryption & Base64 Extraction Helper
   * Handles Images, Stickers, Documents (PDF), Voice Notes (PTT), and Audio
   */
  async function extractMessageMedia(msg) {
    if (!msg || !SUPPORTED_MEDIA_TYPES.has(msg.type)) return null;

    const fileSize = msg.size || msg.fileSize || msg.mediaData?.size || 0;

    // 15 MB File Size Guard
    if (fileSize > MAX_MEDIA_BYTES) {
      console.log(`[WA Gateway] Skipping media download: file size (${Math.round(fileSize / (1024 * 1024))} MB) exceeds 15 MB guard.`);
      return {
        hasMedia: true,
        skippedLargeFile: true,
        mediaType: msg.type,
        fileName: msg.filename || `${msg.type}_${msg.t || Date.now()}`,
        mimeType: msg.mimetype || '',
        fileSize,
        caption: msg.caption || '',
        base64Data: null
      };
    }

    console.log(`[WA Gateway] Downloading media (${msg.type}, ${msg.mimetype || 'unknown'}, ${Math.round(fileSize / 1024)} KB)...`);

    let cleanMimeType = '';
    if (msg.mimetype) {
      cleanMimeType = msg.mimetype.split(';')[0].trim().toLowerCase();
    }
    if (!cleanMimeType || cleanMimeType === 'application/octet-stream') {
      // WhatsApp sometimes omits mimetype entirely. Infer it from the filename
      // first so an image sent as a document is not mistaken for a PDF.
      const inferredMime = inferMimeFromFileName(msg.filename);
      if (inferredMime) cleanMimeType = inferredMime;
      else if (msg.type === 'ptt') cleanMimeType = 'audio/ogg';
      else if (msg.type === 'audio') cleanMimeType = 'audio/mpeg';
      else if (msg.type === 'image') cleanMimeType = 'image/jpeg';
      else if (msg.type === 'sticker') cleanMimeType = 'image/webp';
      else if (msg.type === 'document') cleanMimeType = 'application/pdf';
      else cleanMimeType = 'application/octet-stream';
    }

    let rawBlobOrBuffer = null;
    let rawBase64String = null;
    let downloadedMimeType = '';

    // Convert OpaqueData / wrapper objects into something we can read.
    function unwrapMediaBlob(value) {
      if (!value) return null;
      if (value instanceof Blob ||
        value instanceof ArrayBuffer ||
        ArrayBuffer.isView(value) ||
        typeof value === 'string') {
        return value;
      }
      if (typeof value.forceToBlob === 'function') {
        try {
          const blob = value.forceToBlob();
          if (blob && typeof blob.then === 'function') return blob;
          if (blob instanceof Blob) return blob;
        } catch (_) { }
      }
      if (value._blob instanceof Blob) return value._blob;
      if (value.blob instanceof Blob) return value.blob;
      if (value.mediaBlob instanceof Blob) return value.mediaBlob;
      if (typeof value.blob === 'function') {
        try {
          const blob = value.blob();
          if (blob && (blob instanceof Blob || typeof blob.then === 'function')) return blob;
        } catch (_) { }
      }
      if (value.data instanceof Blob || typeof value.data === 'string') return value.data;
      return null;
    }

    function readFromBlobCache() {
      try {
        const cache = window.require?.('WAWebMediaInMemoryBlobCache')?.InMemoryMediaBlobCache;
        const cached = cache?.get?.(msg.mediaObject?.filehash);
        return unwrapMediaBlob(cached);
      } catch (_) {
        return null;
      }
    }

    function readFromMessage() {
      return unwrapMediaBlob(msg.mediaObject?.mediaBlob) ||
        unwrapMediaBlob(msg.mediaData?.mediaBlob) ||
        unwrapMediaBlob(msg.mediaObject?._mediaBlob) ||
        unwrapMediaBlob(msg.mediaObject) ||
        null;
    }

    try {
      // Mirror whatsapp-web.js resolveMediaBlob: await msg.downloadMedia with
      // isUserInitiated: true, then read the blob from InMemoryMediaBlobCache
      // or from msg.mediaObject.mediaBlob.forceToBlob().
      if (typeof msg.downloadMedia === 'function') {
        try {
          const downloadPromise = msg.downloadMedia({
            downloadEvenIfExpensive: true,
            rmrReason: 1,
            isUserInitiated: true
          });

          if (downloadPromise && typeof downloadPromise.then === 'function') {
            const result = await Promise.race([
              downloadPromise.catch((err) => {
                console.warn('[WA Gateway] downloadMedia rejected:', err?.message || err);
                return null;
              }),
              sleep(6000).then(() => null)
            ]);
            if (result) rawBlobOrBuffer = unwrapMediaBlob(result) || result;
          } else if (downloadPromise) {
            rawBlobOrBuffer = unwrapMediaBlob(downloadPromise) || downloadPromise;
          }
        } catch (downloadErr) {
          console.warn('[WA Gateway] downloadMedia threw:', downloadErr?.message || downloadErr);
          try {
            const legacy = await msg.downloadMedia(true, 1);
            if (legacy) rawBlobOrBuffer = unwrapMediaBlob(legacy) || legacy;
          } catch (_) { }
        }
      }

      // The awaited call can still leave mediaStage as FETCHING briefly.
      let mediaPoll = 0;
      while (!rawBlobOrBuffer && mediaPoll < 10) {
        rawBlobOrBuffer = readFromBlobCache() || readFromMessage();
        if (rawBlobOrBuffer) break;

        const stage = msg.mediaData?.mediaStage || '';
        if (stage.includes('ERROR')) {
          console.warn(`[WA Gateway] Media download error stage: ${stage}`);
          break;
        }

        await sleep(200);
        mediaPoll++;
      }

      // Resolve any thenable blob accessor before reading bytes.
      if (rawBlobOrBuffer && typeof rawBlobOrBuffer.then === 'function') {
        try { rawBlobOrBuffer = await rawBlobOrBuffer; } catch (_) { rawBlobOrBuffer = null; }
        rawBlobOrBuffer = unwrapMediaBlob(rawBlobOrBuffer) || rawBlobOrBuffer;
      }

      if (rawBlobOrBuffer && !(rawBlobOrBuffer instanceof Blob) &&
        !(rawBlobOrBuffer instanceof ArrayBuffer) &&
        !ArrayBuffer.isView(rawBlobOrBuffer) &&
        typeof rawBlobOrBuffer !== 'string') {
        rawBlobOrBuffer = unwrapMediaBlob(rawBlobOrBuffer);
      }

      // Last resort: a blob:/data: URL exposed by the media wrapper.
      if (!rawBlobOrBuffer) {
        const mb = msg.mediaObject?.mediaBlob || msg.mediaData?.mediaBlob;
        const blobUrl = (typeof mb?.url === 'function' ? mb.url() : null) ||
          (typeof mb?.getUrl === 'function' ? mb.getUrl() : null) ||
          mb?.blobUrl || mb?._blobUrl;
        if (typeof blobUrl === 'string' && (blobUrl.startsWith('blob:') || blobUrl.startsWith('data:'))) {
          try {
            const resp = await fetch(blobUrl);
            if (resp.ok) rawBlobOrBuffer = await resp.blob();
          } catch (fetchErr) {
            console.warn('[WA Gateway] Error fetching media blobUrl:', fetchErr);
          }
        }
      }

      if (rawBlobOrBuffer) {
        const blobInfo = rawBlobOrBuffer instanceof Blob
          ? `Blob ${rawBlobOrBuffer.size} bytes (${rawBlobOrBuffer.type || 'no type'})`
          : (rawBlobOrBuffer.constructor?.name || typeof rawBlobOrBuffer);
        console.log(`[WA Gateway] Media blob ready for ${msg.type}: ${blobInfo}`);
      } else {
        console.warn(`[WA Gateway] No media blob resolved for ${msg.type} (stage: ${msg.mediaData?.mediaStage || 'unknown'})`);
      }

      // Check 5: convert the resolved blob/buffer into raw base64.
      if (rawBlobOrBuffer) {
        if (rawBlobOrBuffer instanceof Blob) {
          if (rawBlobOrBuffer.type && rawBlobOrBuffer.type !== 'application/octet-stream') {
            downloadedMimeType = rawBlobOrBuffer.type.split(';')[0].trim().toLowerCase();
          }
          rawBase64String = await blobToBase64(rawBlobOrBuffer);
        } else if (rawBlobOrBuffer instanceof ArrayBuffer || ArrayBuffer.isView(rawBlobOrBuffer)) {
          rawBase64String = bufferToBase64(rawBlobOrBuffer);
        } else if (typeof rawBlobOrBuffer === 'string') {
          if (rawBlobOrBuffer.startsWith('blob:')) {
            try {
              const resp = await fetch(rawBlobOrBuffer);
              if (resp.ok) {
                const blob = await resp.blob();
                if (blob.type && blob.type !== 'application/octet-stream') {
                  downloadedMimeType = blob.type.split(';')[0].trim().toLowerCase();
                }
                rawBase64String = await blobToBase64(blob);
              }
            } catch (fetchErr) {
              console.warn('[WA Gateway] Error fetching blob string:', fetchErr);
            }
          } else {
            const commaIdx = rawBlobOrBuffer.indexOf(',');
            rawBase64String = commaIdx !== -1 ? rawBlobOrBuffer.slice(commaIdx + 1) : rawBlobOrBuffer;
          }
        }
      }

      // Prefer a real preview blob over msg.body when available.
      let thumbnailBase64 = extractThumbnailBase64(msg);
      if (!thumbnailBase64) {
        const previewBlob = getPreviewBlob(msg);
        if (previewBlob) {
          try { thumbnailBase64 = await blobToBase64(previewBlob); } catch (_) { }
        }
      }

      const isImageLike = msg.type === 'image' || msg.type === 'sticker';
      // An image sent as a document is still an image; its thumbnail is valid
      // low-resolution content Gemini can analyze if the full download fails.
      const isImageDocument = msg.type === 'document' && cleanMimeType.startsWith('image/');
      const showsAsImage = isImageLike || isImageDocument;

      // For images/stickers a thumbnail is still a valid image Gemini can see.
      // For non-image documents/videos the thumbnail is only a visual preview.
      let base64Data = rawBase64String;
      let resolvedMimeType = downloadedMimeType || cleanMimeType;

      if (!base64Data && showsAsImage && thumbnailBase64) {
        base64Data = thumbnailBase64;
        resolvedMimeType = detectImageMimeFromBase64(thumbnailBase64);
        console.log(`[WA Gateway] Used thumbnail fallback for ${msg.type} (${resolvedMimeType}).`);
      }

      // Keep audio MIME usable even when WhatsApp reports octet-stream.
      if ((msg.type === 'ptt' || msg.type === 'audio') && !resolvedMimeType.startsWith('audio/')) {
        resolvedMimeType = msg.type === 'ptt' ? 'audio/ogg' : 'audio/mpeg';
      }

      const ok = Boolean(base64Data || thumbnailBase64);
      console.log(`[WA Gateway] Media extracted for ${msg.type}: ${ok ? (Math.round((base64Data || thumbnailBase64).length * 0.75 / 1024) + ' KB Base64 OK') : 'FAILED (using fallback)'}`);

      return {
        hasMedia: true,
        mediaType: msg.type,
        mimeType: resolvedMimeType,
        fileName: msg.filename || `${msg.type}_${msg.t || Date.now()}`,
        fileSize: fileSize || (base64Data ? Math.round(base64Data.length * 0.75) : 0),
        caption: msg.caption || '',
        // The bytes go out once, in base64Data. No separate thumbnail or
        // preview copy is sent (it only made the request bigger).
        base64Data: base64Data || null
      };
    } catch (err) {
      console.warn(`[WA Gateway] extractMessageMedia error for ${msg.type}:`, err);
      return {
        hasMedia: true,
        mediaType: msg.type,
        mimeType: cleanMimeType,
        fileName: msg.filename || `${msg.type}_${msg.t || Date.now()}`,
        fileSize,
        caption: msg.caption || '',
        base64Data: null
      };
    }
  }



  // =========================================================================
  // Control (background.js -> content.js -> here) and own WhatsApp number
  // =========================================================================

  window.addEventListener('message', (event) => {
    if (event.source !== window || !event.data) return;

    // "active" = API key saved, not paused, auto-reply switched on. The
    // decision is made by background.js; this script only obeys it.
    if (event.data.source === 'WA_LOGGER_CONTROL') {
      const wasActive = autoReplyEnabled;
      autoReplyEnabled = Boolean(event.data.active);
      if (autoReplyEnabled && !wasActive) {
        // Never answer anything that arrived while the plugin was off.
        activationTimeSec = Math.floor(Date.now() / 1000);
        console.log('[WA Gateway] Auto-reply is ON.');
      } else if (!autoReplyEnabled && wasActive) {
        dmBurstBuffers.forEach((buffer) => clearTimeout(buffer.timer));
        dmBurstBuffers.clear();
        latestAiRequest.clear();
        console.log('[WA Gateway] Auto-reply is OFF.');
      }
      return;
    }

    // Reply bubbles from the gateway (via background.js)
    if (event.data.source === 'WA_LOGGER_AI_RESPONSE') {
      handleAiResponse(event.data);
    }

    // A message the vendor asked us to send (gateway -> background.js -> content.js -> here)
    if (event.data.source === 'WA_GATEWAY_OUTBOUND_COMMAND') {
      handleOutboundCommand(event.data.command);
    }
  });

  // The WhatsApp number this browser profile is logged in with, digits only.
  // Sent as X-WA-Number so the gateway can spot a key used on the wrong
  // number. Returns '' when WhatsApp does not expose it (header is then omitted).
  function getOwnWaNumber() {
    try {
      const UserPrefs = cachedUserPrefs
        || (typeof window.require === 'function' ? window.require('WAWebUserPrefsMeUser') : null)
        || window.Store?.UserPrefs;
      const meWid = UserPrefs?.getMaybeMePnUser?.()
        || UserPrefs?.getMaybeMeUser?.()
        || UserPrefs?.getMeUser?.();
      const raw = meWid?.user || String(meWid?._serialized || '').split('@')[0];
      const digits = String(raw || '').replace(/\D/g, '');
      return digits.length >= 10 && digits.length <= 15 ? digits : '';
    } catch (_) {
      return '';
    }
  }

  let lastReportedWaNumber = '';

  function reportWaNumber() {
    const waNumber = getOwnWaNumber();
    if (!waNumber || waNumber === lastReportedWaNumber) return;
    lastReportedWaNumber = waNumber;
    window.postMessage({ source: 'WA_LOGGER_WA_INFO', waNumber }, '*');
  }

  let lastSentMsgId = ''; // id of the message sendBackgroundAutoReply handed to WhatsApp last
  let cachedChatCollection = null;
  let cachedSendMsgAction = null;
  let cachedUserPrefs = null;
  let cachedMsgKey = null;
  let cachedWidFactory = null;
  let cachedCmd = null;

  function getChatCollection() {
    if (cachedChatCollection && (typeof cachedChatCollection.getModelsArray === 'function' || typeof cachedChatCollection.get === 'function')) {
      return cachedChatCollection;
    }
    try {
      if (typeof window.require === 'function') {
        const ChatMod = window.require('WAWebChatCollection') || window.require('WAWebCollections');
        const coll = ChatMod?.ChatCollection || ChatMod?.default?.ChatCollection || ChatMod?.default || ChatMod;
        if (coll && (typeof coll.getModelsArray === 'function' || typeof coll.get === 'function')) {
          cachedChatCollection = coll;
          return coll;
        }
      }
    } catch (_) { }

    if (window.Store?.Chat) {
      cachedChatCollection = window.Store.Chat;
      return window.Store.Chat;
    }
    return null;
  }

  function findChatModel(chatId) {
    if (!chatId) return null;
    const coll = getChatCollection();
    if (coll?.get) {
      try {
        const c = coll.get(chatId);
        if (c) return c;
      } catch (_) { }
    }
    if (window.Store?.Chat?.get) {
      try {
        const c = window.Store.Chat.get(chatId);
        if (c) return c;
      } catch (_) { }
    }
    if (coll && typeof coll.getModelsArray === 'function') {
      try {
        const models = coll.getModelsArray();
        for (const m of models) {
          const mid = m.id?._serialized || String(m.id || '');
          if (mid === chatId || m.id?.user === chatId || m.phoneNumber?._serialized === chatId) {
            return m;
          }
        }
      } catch (_) { }
    }
    return null;
  }

  // =========================================================================
  // DOM Compose Fallback
  // =========================================================================

  async function triggerDomComposeFallback(targetChat, replyText) {
    try {
      console.log('[WA Auto-Reply] Executing DOM Compose Fallback...');

      const Cmd = cachedCmd
        || (window.require ? (window.require('WAWebCmd')?.Cmd || window.require('WAWebCmd')?.default || window.require('WAWebCmd')) : null)
        || window.Store?.Cmd;

      if (targetChat && Cmd?.openChatBottom) {
        Cmd.openChatBottom(targetChat);
      } else if (targetChat && Cmd?.openChatAt) {
        Cmd.openChatAt({ chat: targetChat });
      }

      await sleep(350);

      const inputBox = document.querySelector('#main footer div[contenteditable="true"]') ||
        document.querySelector('footer div[contenteditable="true"]') ||
        document.querySelector('div[contenteditable="true"][data-tab="10"]') ||
        document.querySelector('div[contenteditable="true"][role="textbox"]');

      if (!inputBox) {
        console.warn('[WA Auto-Reply] DOM Fallback: Input box not found in DOM.');
        return false;
      }

      inputBox.focus();
      document.execCommand('insertText', false, replyText);

      await sleep(150);

      const sendBtn = document.querySelector('#main footer button[aria-label="Send"]') ||
        document.querySelector('#main footer span[data-icon="send"]') ||
        document.querySelector('#main footer span[data-icon="wds-ic-send-filled"]') ||
        document.querySelector('button[data-tab="11"]') ||
        document.querySelector('span[data-icon="send"]');

      if (sendBtn) {
        const clickable = sendBtn.closest('button') || sendBtn;
        clickable.click();
        console.log('[WA Auto-Reply] Successfully sent via DOM compose button click.');
        return true;
      } else {
        const enterEvt = new KeyboardEvent('keydown', {
          key: 'Enter',
          code: 'Enter',
          which: 13,
          keyCode: 13,
          bubbles: true
        });
        inputBox.dispatchEvent(enterEvt);
        console.log('[WA Auto-Reply] Successfully sent via DOM Enter keypress.');
        return true;
      }
    } catch (fallbackErr) {
      console.warn('[WA Auto-Reply] DOM Compose Fallback failed:', fallbackErr);
      return false;
    }
  }

  // =========================================================================
  // Background Auto-Reply Send Engine (WAWebMsgKey with Remote)
  // =========================================================================

  async function sendBackgroundAutoReply(chatObj, arg2, arg3, arg4, options = {}) {
    let targetChat = null;
    let chatId = '';
    let replyText = '';
    let msg = null;

    if (typeof arg2 === 'string' && typeof arg3 === 'string') {
      // (chatObj, chatId, replyText, msg)
      chatId = arg2;
      replyText = arg3;
      msg = arg4 || null;
    } else if (typeof arg2 === 'object' && typeof arg3 === 'string' && typeof arg4 === 'string') {
      // (chatObj, lastMsg, chatId, bubbleText)
      msg = arg2;
      chatId = arg3;
      replyText = arg4;
    } else {
      chatId = String(arg2 || '');
      replyText = String(arg3 || '');
      msg = arg4 || null;
    }

    try {
      // 1. Resolve the Real Chat Model
      targetChat = findChatModel(chatId) || chatObj || msg?.chat;

      // 2. Resolve Send Actions & Key Factories
      const SendMsgAction = cachedSendMsgAction
        || (window.require ? window.require('WAWebSendMsgChatAction') : null)
        || window.Store?.SendMsgChatAction;
      const MsgKeyMod = cachedMsgKey
        || (window.require ? window.require('WAWebMsgKey') : null)
        || window.Store?.MsgKey;
      const MsgKey = MsgKeyMod?.default || MsgKeyMod;
      const UserPrefs = cachedUserPrefs
        || (window.require ? window.require('WAWebUserPrefsMeUser') : null)
        || window.Store?.UserPrefs;
      const WidFactory = cachedWidFactory
        || (window.require ? window.require('WAWebWidFactory') : null)
        || window.Store?.WidFactory;

      // 3. Resolve 'to' Wid
      const toWid = targetChat?.id || msg?.id?.remote || msg?.from || (WidFactory?.createWid ? WidFactory.createWid(chatId) : null);
      const isLid = String(toWid?._serialized || chatId).endsWith('@lid');

      // 4. Resolve 'from' (me) Wid
      const meWid = (isLid && UserPrefs?.getMaybeMeLidUser ? UserPrefs.getMaybeMeLidUser() : null)
        || UserPrefs?.getMaybeMePnUser?.()
        || UserPrefs?.getMaybeMeUser?.()
        || UserPrefs?.getMeUser?.();

      // 5. Generate message ID string
      let randomHex = '3EB0' + Array.from({ length: 16 }, () => Math.floor(Math.random() * 16).toString(16)).join('').toUpperCase();
      if (typeof MsgKey?.newId === 'function') {
        try { randomHex = await MsgKey.newId(); } catch (_) { }
      }

      // 6. Instantiate MsgKey so msgKey.remote is properly defined
      let msgKey;
      if (typeof MsgKey === 'function') {
        try {
          msgKey = new MsgKey({
            from: meWid,
            to: toWid,
            id: randomHex,
            selfDir: 'out'
          });
        } catch (_) { }
      }

      if (!msgKey || !msgKey.remote) {
        msgKey = {
          fromMe: true,
          remote: toWid,
          id: randomHex,
          self: 'out',
          _serialized: `true_${toWid?._serialized || chatId}_${randomHex}`
        };
      }

      lastSentMsgId = msgKey?._serialized || (typeof msgKey?.toString === 'function' ? String(msgKey) : '');

      // 7. Build full msgData payload expected by addAndSendMsgToChat
      const ephemeral = typeof targetChat?.getEphemeralSetting === 'function' ? targetChat.getEphemeralSetting() : undefined;
      const msgData = {
        id: msgKey,
        body: replyText,
        type: 'chat',
        subtype: null,
        t: Math.floor(Date.now() / 1000),
        from: meWid,
        to: toWid,
        self: 'out',
        ack: 0,
        local: true,
        isNewMsg: true,
        star: false,
        kicNotified: false,
        isForwarded: false,
        mentionedJidList: [],
        ...(ephemeral ? { ephemeralDuration: ephemeral } : {})
      };

      if (SendMsgAction?.addAndSendMsgToChat && targetChat) {
        try {
          const sendResult = SendMsgAction.addAndSendMsgToChat(targetChat, msgData);
          if (Array.isArray(sendResult)) {
            await Promise.all(sendResult);
          } else if (sendResult && typeof sendResult.then === 'function') {
            await sendResult;
          }
          console.log(`[WA Auto-Reply] Successfully sent via addAndSendMsgToChat to ${chatId}`);
          return true;
        } catch (sendErr) {
          console.warn(`[WA Auto-Reply] addAndSendMsgToChat failed for ${chatId}, trying chat.sendMessage:`, sendErr?.message || sendErr);
        }
      }

      // Fallback: chat.sendMessage
      if (targetChat && typeof targetChat.sendMessage === 'function') {
        try {
          await targetChat.sendMessage(replyText);
          console.log(`[WA Auto-Reply] Successfully sent via chat.sendMessage to ${chatId}`);
          return true;
        } catch (chatErr) {
          console.warn(`[WA Auto-Reply] chat.sendMessage failed for ${chatId}:`, chatErr?.message || chatErr);
        }
      }

      throw new Error('No direct internal send method available.');
    } catch (primaryErr) {
      console.warn(`[WA Auto-Reply] Primary send method threw for ${chatId}:`, primaryErr?.message || primaryErr);
      // A job for the vendor must not go through the typing fallback: it cannot tell us whether
      // the text was really sent, and an OTP must never be sent twice.
      if (options.strict) return false;
      return await triggerDomComposeFallback(targetChat, replyText);
    }
  }


  // =========================================================================
  // Send lock: one send at a time, so a vendor message and an auto-reply never overlap
  // =========================================================================

  let sendChain = Promise.resolve();
  function withSendLock(task) {
    const run = sendChain.then(task, task);
    sendChain = run.catch(() => undefined);
    return run;
  }

  // =========================================================================
  // Outbound: send what a vendor asked for (plan section 8.8)
  //
  // UNVERIFIED against a real WhatsApp Web build (plan section 11, spike/SPIKE_REPORT.md):
  //   - the connection state signal (readWaState)
  //   - "is this number on WhatsApp" (checkNumberOnWhatsApp)
  //   - creating a chat for a number that is not a contact (createChatForNumber)
  //   - the invalid-number popup after /send?phone= (invalidNumberPopupShown)
  //   - reading the ack level of a sent message (waitForAck)
  // Every probe below tries a few known names and says "I do not know" (null / 'not_loaded')
  // when none works. None of them pretends: the job then ends as failed or unknown, never as
  // a made-up "sent". Nothing here has been proven on a real account until the Spike report says so.
  // =========================================================================

  const OUTBOUND_ACK_WAIT_MS = 15000; // plan 8.8 step 6
  const WA_READY_WAIT_MS = 20000; // after a page load WhatsApp needs a moment
  const CHAT_APPEAR_WAIT_MS = 15000; // after /send?phone= opened
  const WA_STATE_REPORT_MS = 5000;
  const OUTBOUND_JOB_ID = /^job_[0-9a-f]{16}$/;
  const WA_MODULE_UNKNOWN = 'WAWeb module not found';

  function requireModule(names) {
    if (typeof window.require !== 'function') return null;
    for (const name of names) {
      try {
        const mod = window.require(name);
        if (mod) return mod;
      } catch (_) { /* try the next name */ }
    }
    return null;
  }

  function mapConnectionState(raw) {
    const value = String(raw || '').toLowerCase();
    if (!value) return '';
    if (['connected', 'open', 'main'].includes(value)) return 'connected';
    if (['connecting', 'opening', 'pairing', 'unlaunched', 'resuming', 'syncing'].includes(value)) return 'connecting';
    if (['timeout', 'disconnected', 'offline', 'no_connection', 'proxy_blocked'].includes(value)) return 'offline';
    if (['unpaired', 'unpaired_idle', 'conflict', 'logout', 'logged_out'].includes(value)) return 'logged_out';
    return '';
  }

  // connected | connecting | offline | logged_out | not_loaded
  function readWaState() {
    try {
      const mod = requireModule(['WAWebConnModel', 'WAWebConn', 'WAWebStreamModel']);
      const conn = mod?.Conn || mod?.default?.Conn || mod?.default || mod;
      const mapped = mapConnectionState(conn?.state ?? conn?.stream);
      if (mapped) return mapped;
    } catch (_) { /* fall through to the page itself */ }
    try {
      // The QR code screen means nobody is logged in.
      if (document.querySelector('canvas[aria-label*="QR" i], div[data-ref][data-ref!=""]')) return 'logged_out';
      if (document.querySelector('#pane-side')) return 'connected';
    } catch (_) { /* no DOM access */ }
    return typeof window.require === 'function' ? 'connecting' : 'not_loaded';
  }

  let lastReportedWaState = '';
  function reportWaState() {
    const state = readWaState();
    lastReportedWaState = state;
    window.postMessage({ source: 'WA_GATEWAY_WA_STATE', state }, '*');
  }

  // true: on WhatsApp. false: WhatsApp said it is not. null: this build gave no clear answer.
  async function checkNumberOnWhatsApp(digits) {
    try {
      const mod = requireModule(['WAWebQueryExistsJob', 'WAWebExistsAction', 'WAWebContactExistsAction']);
      const query = mod?.queryExists || mod?.queryWidExists || mod?.default?.queryExists;
      if (typeof query !== 'function') return null;
      const answer = await query(`${digits}@c.us`);
      if (answer && (answer.exists === true || answer.wid || answer.jid)) return true;
      if (answer && answer.exists === false) return false;
    } catch (_) { /* unclear */ }
    return null;
  }

  async function createChatForNumber(jid) {
    try {
      const mod = requireModule(['WAWebFindChatAction', 'WAWebChatFindAction']);
      const find = mod?.findOrCreateLatestChat || mod?.default?.findOrCreateLatestChat;
      if (typeof find !== 'function') return null;
      const wid = cachedWidFactory?.createWid?.(jid)
        || requireModule(['WAWebWidFactory'])?.createWid?.(jid)
        || { _serialized: jid };
      const found = await find(wid);
      const chat = found?.chat || found;
      return chat?.id ? chat : null;
    } catch (_) {
      return null;
    }
  }

  function invalidNumberPopupShown() {
    try {
      const dialogs = document.querySelectorAll('[role="dialog"], [data-animate-modal-popup], [data-testid="popup"]');
      for (const dialog of dialogs) {
        if (/invalid|not on whatsapp|isn.t on whatsapp|not a whatsapp/i.test(dialog.textContent || '')) return true;
      }
    } catch (_) { /* no DOM */ }
    return false;
  }

  async function waitFor(check, timeoutMs, stepMs = 250) {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      try {
        const value = await check();
        if (value) return value;
      } catch (_) { /* keep waiting */ }
      if (Date.now() >= deadline) return null;
      await sleep(stepMs);
    }
  }

  function findSentMessage(chat, msgId) {
    if (!chat || !msgId) return null;
    try {
      const direct = chat.msgs?.get?.(msgId);
      if (direct) return direct;
      const models = chat.msgs?.getModelsArray?.() || chat.models || [];
      return models.find((m) => m?.id?._serialized === msgId || String(m?.id) === msgId) || null;
    } catch (_) {
      return null;
    }
  }

  // The ack level (1 = reached WhatsApp's server). Returns 0 when it did not show up in time
  // or this build does not expose it: the job then stays "unknown", it is never called "sent".
  async function waitForAck(chat, msgId) {
    const level = await waitFor(() => {
      const msg = findSentMessage(chat, msgId);
      const ack = Number(msg?.ack);
      return Number.isFinite(ack) && ack >= 1 ? ack : 0;
    }, OUTBOUND_ACK_WAIT_MS);
    return level || 0;
  }

  function outboundEvent(job, event, extra = {}) {
    window.postMessage(
      { source: 'WA_GATEWAY_OUTBOUND_EVENT', event: { jobId: job.jobId, attempt: job.attempt, event, ...extra } },
      '*',
    );
  }

  const outboundFailed = (job, errorCode) => outboundEvent(job, 'result', { status: 'failed', errorCode });

  async function runOutboundJob(job, resume) {
    const jid = `${job.to}@c.us`;

    // WhatsApp must be usable (after a reload it needs a few seconds).
    const ready = await waitFor(() => readWaState() === 'connected' && getChatCollection(), WA_READY_WAIT_MS);
    if (!ready) {
      outboundFailed(job, 'SEND_FAILED'); // nothing was sent
      return;
    }

    if (!resume && (await checkNumberOnWhatsApp(job.to)) === false) {
      outboundFailed(job, 'NOT_ON_WHATSAPP');
      return;
    }

    let chat = findChatModel(jid);
    if (!chat && !resume) chat = await createChatForNumber(jid);
    if (!chat && !resume) {
      // Plan 8.8 step 5c: open /send?phone= instead. The page reloads, background.js resumes us.
      outboundEvent(job, 'url_fallback');
      return;
    }
    if (!chat) {
      // We are the page that /send?phone= opened.
      chat = await waitFor(() => findChatModel(jid) || (invalidNumberPopupShown() ? 'invalid' : null), CHAT_APPEAR_WAIT_MS);
      if (chat === 'invalid') {
        outboundFailed(job, 'NOT_ON_WHATSAPP');
        return;
      }
      if (!chat) {
        outboundFailed(job, 'SEND_FAILED');
        return;
      }
    }

    // From here on a reload or a crash means "maybe sent": tell background.js first.
    outboundEvent(job, 'sending');
    lastSentMsgId = '';
    const accepted = await sendBackgroundAutoReply(chat, jid, job.message, null, { strict: true });
    if (!accepted) {
      outboundFailed(job, 'SEND_FAILED');
      return;
    }
    const ack = await waitForAck(chat, lastSentMsgId);
    if (ack >= 1) outboundEvent(job, 'result', { status: 'sent', waAck: ack });
    else outboundEvent(job, 'no_ack'); // not proven: never call it sent
  }

  function handleOutboundCommand(command) {
    const job = command?.job;
    if (
      command?.type !== 'SEND' ||
      !job ||
      !OUTBOUND_JOB_ID.test(String(job.jobId)) ||
      !Number.isInteger(job.attempt) ||
      !/^\d{8,15}$/.test(String(job.to)) ||
      typeof job.message !== 'string' ||
      !job.message
    ) {
      console.warn('[WA Gateway] Ignored a malformed outbound command.');
      return;
    }
    const safeJob = { jobId: job.jobId, attempt: job.attempt, to: String(job.to), message: job.message };
    withSendLock(() => runOutboundJob(safeJob, Boolean(command.resume))).catch((err) => {
      console.warn('[WA Gateway] Outbound job crashed:', err?.message || err);
      outboundFailed(safeJob, 'SEND_FAILED');
    });
  }

  setInterval(reportWaState, WA_STATE_REPORT_MS);
  setTimeout(reportWaState, 500);

  // =========================================================================
  // Multi-Bubble AI Reply Sequencer
  // =========================================================================

  async function handleAiResponse(data) {
    const { chatId, replies, requestId } = data || {};
    if (!chatId || !Array.isArray(replies) || replies.length === 0) return;

    // Only the latest request of this chat may answer, and only once.
    const current = latestAiRequest.get(chatId);
    if (!current || !requestId || current.requestId !== requestId) {
      console.log(`[WA Gateway] Dropped a stale or unknown reply for ${chatId} (a newer request exists or it was already used).`);
      return;
    }
    latestAiRequest.delete(chatId);
    if (Date.now() - current.startedAt > MAX_REPLY_AGE_MS) {
      console.log(`[WA Gateway] Dropped a reply for ${chatId}: it arrived after ${MAX_REPLY_AGE_MS / 1000}s.`);
      return;
    }
    if (!autoReplyEnabled) return;

    const requestAt = aiRequestStartedAt.get(chatId);
    if (requestAt) {
      console.log(`[WA Auto-Reply] AI response received in ${Date.now() - requestAt}ms (server+network).`);
      aiRequestStartedAt.delete(chatId);
    }
    const lastAt = lastMessageAtByChat.get(chatId);
    if (lastAt) {
      const total = Date.now() - lastAt;
      console.log(`[WA Auto-Reply] First bubble is ${total}ms after the last incoming message.`);
    }

    const targetChat = findChatModel(chatId);
    const lastMsg = lastReceivedMsgMap.get(chatId) || null;

    for (let i = 0; i < replies.length; i++) {
      const bubble = (replies[i] || '').trim();
      if (!bubble) continue;

      rememberSentText(chatId, bubble);

      // Short human-ish pause between bubbles. Kept small so the whole
      // reply stays inside the 10s window.
      if (i > 0) {
        const elapsed = lastAt ? Date.now() - lastAt : 0;
        const pauseMs = elapsed > 9000 ? 0 : Math.min(800, Math.max(400, bubble.length * 5));
        if (pauseMs > 0) {
          console.log(`[WA Auto-Reply] Typing pause: ${pauseMs}ms before bubble #${i + 1}...`);
          await sleep(pauseMs);
        }
      }

      console.log(`[WA Auto-Reply] Sending bubble (${i + 1}/${replies.length}) to ${chatId}: "${bubble}"`);
      const sent = await withSendLock(() => sendBackgroundAutoReply(targetChat, chatId, bubble, lastMsg));
      console.log(`[WA Auto-Reply] Bubble ${i + 1}/${replies.length} result: ${sent ? 'SENT' : 'FAILED'}`);
      if (!sent) {
        console.warn(`[WA Auto-Reply] Bubble ${i + 1}/${replies.length} was NOT sent — check the send error above.`);
      }
    }
  }

  // =========================================================================
  // 7-Second Burst Debounce Timer Execution
  // =========================================================================

  async function onBurstTimerFired(chatId) {
    const buffer = dmBurstBuffers.get(chatId);
    if (!buffer) return;
    dmBurstBuffers.delete(chatId);

    // The initial media extraction started when the message arrived, so it
    // has had the full burst wait. Don't block the reply beyond 150ms for late
    // media — the whole cycle must stay under 10s.
    await Promise.all(
      buffer.messages.map(async (item) => {
        if (item.mediaPromise) {
          try {
            item.media = await Promise.race([
              item.mediaPromise,
              sleep(150).then(() => null)
            ]);
          } catch (mediaErr) {
            console.warn('[WA Gateway] mediaPromise resolution error:', mediaErr);
            item.media = null;
          }
          delete item.mediaPromise;
        }

        if (!item.media) {
          console.log(`[WA Gateway] Media not ready at burst for ${item.msg?.type || 'message'}; proceeding to keep the 10s limit.`);
        }
      })
    );

    // Keep the total AI payload under the gateway's 40MB request limit and leave
    // headroom for multiple attachments in one burst.
    const MAX_AI_BURST_MEDIA_BASE64 = 24 * 1024 * 1024;
    let remainingMediaBudget = MAX_AI_BURST_MEDIA_BASE64;

    const incomingMessages = buffer.messages.map((item) => {
      let media = item.media || null;

      if (media?.base64Data) {
        if (media.base64Data.length > remainingMediaBudget) {
          media = { ...media, base64Data: null, skippedCombinedLimit: true };
        } else {
          remainingMediaBudget -= media.base64Data.length;
        }
      }

      return {
        text: item.text,
        timestamp: item.timestamp,
        media
      };
    });

    const chatObj = buffer.chatObj || (window.require ? window.require('WAWebChatCollection')?.ChatCollection?.get(chatId) : null);

    let chatHistory = [];
    if (chatObj?.msgs?.getModelsArray) {
      const allMsgs = chatObj.msgs.getModelsArray();
      const validHistorical = allMsgs.filter(m => {
        if (!m) return false;
        if (m.type === 'e2e_notification' || m.type === 'gp2' || m.type === 'protocol' || m.subtype === 'ciphertext') return false;
        const body = (m.body || m.caption || '').trim();
        return Boolean(body);
      });

      // Exclude the incoming burst items from the history tail
      let burstCount = incomingMessages.length;
      let idx = validHistorical.length - 1;
      const burstIds = new Set();
      while (idx >= 0 && burstCount > 0) {
        const m = validHistorical[idx];
        const isFromMe = Boolean(m.id?.fromMe || m.fromMe);
        if (!isFromMe) {
          burstIds.add(m.id?._serialized || m.id?.id);
          burstCount--;
        }
        idx--;
      }

      const historyCandidates = validHistorical.filter(m => !burstIds.has(m.id?._serialized || m.id?.id));
      chatHistory = historyCandidates.slice(-60).map(m => ({
        fromMe: Boolean(m.id?.fromMe || m.fromMe),
        text: formatMediaDisplayText(m),
        timestamp: formatTime(m.t)
      }));
    }

    const lastAt = lastMessageAtByChat.get(chatId);
    const sinceLast = lastAt ? Date.now() - lastAt : null;
    console.log(`[WA Gateway] Burst wait elapsed for ${buffer.meta.chatTitle || chatId}${sinceLast ? ` (${sinceLast}ms since last msg)` : ''}. Dispatching request (${incomingMessages.length} burst msgs, ${chatHistory.length} history msgs)...`);

    const requestId = crypto.randomUUID();
    const startedAt = Date.now();
    aiRequestStartedAt.set(chatId, startedAt);
    // From now on only this request may answer for the chat (see latestAiRequest).
    latestAiRequest.set(chatId, { requestId, startedAt });
    window.postMessage({
      source: 'WA_LOGGER_AI_REQUEST',
      requestId,
      waNumber: getOwnWaNumber(),
      payload: {
        chatId,
        chatTitle: buffer.meta.chatTitle,
        senderName: buffer.meta.senderName,
        senderNumber: buffer.meta.senderNumber,
        isSaved: buffer.meta.isSaved,
        incomingMessages,
        chatHistory
      }
    }, '*');
  }

  // =========================================================================
  // Incoming Message Handler
  // =========================================================================

  function handleIncomingMsg(msg) {
    try {
      if (!msg) return;
      if (!autoReplyEnabled) return;

      const chatId = msg.from?._serialized || msg.id?.remote?._serialized || String(msg.from || '');

      // 1. Strictly block WhatsApp Channels (@newsletter) & Status Broadcasts
      if (
        chatId.endsWith('@newsletter') ||
        chatId.endsWith('@broadcast') ||
        chatId === 'status@broadcast' ||
        Boolean(msg.chat?.isNewsletter) ||
        msg.type === 'newsletter' ||
        msg.broadcast ||
        (msg.id?.remote && String(msg.id.remote).endsWith('@newsletter'))
      ) {
        return;
      }

      // 2. Strictly ignore outgoing messages
      if (msg.id?.fromMe || msg.fromMe) return;

      // 3. Strictly ignore historical/old messages (must be isNewMsg and received after activation cutoff - 5s)
      if (!msg.isNewMsg) return;
      if (msg.t && msg.t < (activationTimeSec - 5)) return;

      // 4. Handle Ciphertext / Pending Decryption
      // WhatsApp adds media models as 'ciphertext' first, then mutates msg.type
      // to image/document/ptt/audio a few hundred milliseconds later.
      const isCiphertext = msg.type === 'ciphertext' || (!msg.type && msg.subtype === 'ciphertext');
      if (isCiphertext) {
        // Guard against attaching duplicate listeners on repeated add events.
        if (msg.__waLoggerCiphertextWatched) return;
        msg.__waLoggerCiphertextWatched = true;

        const retryIfDecrypted = () => {
          if (msg.type && msg.type !== 'ciphertext') {
            try { msg.off('change:type', retryIfDecrypted); } catch (_) { }
            handleIncomingMsg(msg);
          }
        };

        if (typeof msg.on === 'function') {
          msg.on('change:type', retryIfDecrypted);
        }

        // Backbone events can stay silent, so poll at 300/800/1500/3000ms too.
        [300, 800, 1500, 3000].forEach(delay => {
          setTimeout(retryIfDecrypted, delay);
        });
        return;
      }

      // Strictly allow only real user messages
      if (!ALLOWED_MSG_TYPES.has(msg.type)) return;

      // 5. Extract Message Content (Handling Media vs Text correctly)
      const MEDIA_TYPES = new Set(['image', 'video', 'document', 'sticker', 'ptt', 'audio']);
      const isMedia = MEDIA_TYPES.has(msg.type);

      let messageBody = '';
      if (isMedia) {
        messageBody = formatMediaDisplayText(msg);
      } else {
        messageBody = (msg.body || '').trim();
      }

      if (!messageBody) return;

      // Guard: never process AI's own sent replies
      if (wasSentByUs(chatId, messageBody)) return;

      // 6. Deduplicate message IDs
      const msgId = msg.id?._serialized || msg.id?.id;
      if (!msgId || seenMsgIds.has(msgId)) return;
      seenMsgIds.add(msgId);
      if (seenMsgIds.size > 3000) {
        const ids = Array.from(seenMsgIds);
        ids.slice(0, 1500).forEach(id => seenMsgIds.delete(id));
      }

      // Cache as last received message for this chat
      lastReceivedMsgMap.set(chatId, msg);

      // 7. Group vs 1-on-1 Chat Resolution
      const isGroup = chatId.endsWith('@g.us') || Boolean(msg.author);

      let chat = msg.chat;
      if (!chat && typeof window.require === 'function') {
        try {
          chat = window.require('WAWebChatCollection')?.ChatCollection?.get(chatId);
        } catch (e) { }
      }

      if (chat?.isNewsletter) return;

      const chatDisplayName = chat?.formattedTitle || chat?.name || chat?.contact?.name || chat?.contact?.pushname || '';
      const rawSenderJid = isGroup ? (msg.author?._serialized || msg.author || '') : chatId;

      let ContactCollection = null;
      if (typeof window.require === 'function') {
        try {
          ContactCollection = window.require('WAWebContactCollection')?.ContactCollection;
        } catch (e) { }
      }

      const senderContact = msg.senderObj || (rawSenderJid && ContactCollection?.get ? ContactCollection.get(rawSenderJid) : null);

      let resolvedPhone = '';
      if (rawSenderJid.endsWith('@lid')) {
        const phoneFromObj = senderContact?.phoneNumber?._serialized || senderContact?.id?.user;
        if (phoneFromObj && !String(phoneFromObj).includes('_')) {
          resolvedPhone = formatPhoneNumber(String(phoneFromObj));
        }
      } else {
        resolvedPhone = formatPhoneNumber(rawSenderJid);
      }

      if (!resolvedPhone && senderContact?.id?.user) {
        resolvedPhone = formatPhoneNumber(String(senderContact.id.user));
      }

      const isAddressBook = senderContact?.isAddressBookContact === true || Boolean(senderContact?.name);
      const savedName = (isAddressBook && (senderContact?.name || senderContact?.shortName)) ||
        (!isGroup && chat?.contact?.name) ||
        null;

      let chatTitle = '';
      let senderName = '';
      let senderNumber = '';
      let isSaved = false;

      if (isGroup) {
        chatTitle = chatDisplayName || (msg.chatTitle || 'Group');
        senderName = savedName || senderContact?.pushname || msg.notifyName || 'Unknown';
        senderNumber = resolvedPhone || senderName;
        isSaved = Boolean(savedName);
      } else {
        if (savedName) {
          chatTitle = savedName;
          senderName = savedName;
          senderNumber = resolvedPhone || 'Saved';
          isSaved = true;
        } else {
          chatTitle = resolvedPhone || chatDisplayName || 'Unknown';
          senderName = 'Unknown';
          senderNumber = resolvedPhone || 'Unknown';
          isSaved = false;
        }
      }

      const timestamp = formatTime(msg.t);


      // Groups are never answered and never forwarded anywhere.
      if (isGroup) return;

      // 1-on-1 DM with auto-reply active: push to the burst buffer
      lastMessageAtByChat.set(chatId, Date.now());
      const hasMedia = SUPPORTED_MEDIA_TYPES.has(msg.type);
      const mediaPromise = hasMedia ? extractMessageMedia(msg) : null;
      const burstItem = { text: messageBody, timestamp, msg, mediaPromise, media: null };
      let buffer = dmBurstBuffers.get(chatId);

      if (buffer) {
        clearTimeout(buffer.timer);
        buffer.messages.push(burstItem);
        buffer.chatObj = chat;
        buffer.lastMsg = msg;
        buffer.meta = { chatId, chatTitle, senderName, senderNumber, isSaved };
      } else {
        buffer = {
          timer: null,
          messages: [burstItem],
          chatObj: chat,
          lastMsg: msg,
          meta: { chatId, chatTitle, senderName, senderNumber, isSaved }
        };
        dmBurstBuffers.set(chatId, buffer);
      }

      console.log(`[WA Gateway] Buffered a 1-on-1 message from ${chatTitle}. Burst count: ${buffer.messages.length}.${hasMedia ? ' Started in-memory media download.' : ''} Burst wait restarted.`);

      buffer.timer = setTimeout(() => {
        onBurstTimerFired(chatId);
      }, BURST_WAIT_MS);

    } catch (err) {
      console.warn('[WA Gateway] Error handling incoming message:', err);
    }
  }

  // =========================================================================
  // Internal Store Hooking
  // =========================================================================

  function tryHookInternalStore() {
    if (isHooked) return;

    try {
      // Method 1: window.require('WAWebMsgCollection')
      if (typeof window.require === 'function') {
        const MsgCollection =
          window.require('WAWebMsgCollection')?.MsgCollection ||
          window.require('WAWebCollections')?.MsgCollection;

        if (MsgCollection && typeof MsgCollection.on === 'function') {
          MsgCollection.on('add', (msg) => {
            if (msg) handleIncomingMsg(msg);
          });
          isHooked = true;
          console.log('[WA Gateway] 100% Hooked via window.require (WAWebMsgCollection).');
          window.postMessage({ source: 'WA_LOGGER_INTERNAL_READY', method: 'require' }, '*');

          // Tell the popup which WhatsApp number this is (re-checked every 15s)
          reportWaNumber();
          setInterval(reportWaNumber, 15000);
          return;
        }
      }

      // Method 2: Webpack chunk inspection
      if (window.webpackChunkwhatsapp_web_client && !isHooked) {
        window.webpackChunkwhatsapp_web_client.push([
          [['wa_internal_hook_' + Date.now()]],
          {},
          (req) => {
            try {
              if (!req?.c) return;
              for (const id in req.c) {
                const mod = req.c[id]?.exports;
                if (!mod) continue;

                if (!cachedChatCollection) {
                  const possibleChatCollection =
                    mod.ChatCollection ||
                    (mod.default && mod.default.ChatCollection) ||
                    (mod.Chat && (typeof mod.Chat.getModelsArray === 'function' || typeof mod.Chat.get === 'function') ? mod.Chat : null);
                  if (possibleChatCollection) {
                    cachedChatCollection = possibleChatCollection;
                  }
                }

                const MsgCollection =
                  mod.MsgCollection ||
                  (mod.default && mod.default.MsgCollection) ||
                  (mod.Msg && mod.Msg.collection);

                if (MsgCollection && typeof MsgCollection.on === 'function') {
                  MsgCollection.on('add', (msg) => {
                    if (msg) handleIncomingMsg(msg);
                  });
                  isHooked = true;
                  console.log('[WA Gateway] 100% Hooked via Webpack module cache.');
                  window.postMessage({ source: 'WA_LOGGER_INTERNAL_READY', method: 'webpack' }, '*');

                  // Tell the popup which WhatsApp number this is (re-checked every 15s)
                  reportWaNumber();
                  setInterval(reportWaNumber, 15000);
                  return;
                }
              }
            } catch (e) { }
          }
        ]);
      }
    } catch (err) { }

    attempts++;
    if (!isHooked && attempts < MAX_ATTEMPTS) {
      setTimeout(tryHookInternalStore, 1000);
    }
  }

  // Start hooking
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(tryHookInternalStore, 1000);
  } else {
    window.addEventListener('DOMContentLoaded', () => setTimeout(tryHookInternalStore, 1000));
  }
})();
