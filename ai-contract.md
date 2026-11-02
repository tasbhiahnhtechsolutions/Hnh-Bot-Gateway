# AI Reply Contract (Vendor Guide)

**Contract version: 1**

This document tells you exactly what your chatbot will **receive** from our gateway and what it must **send back**. If you build your endpoint the way this document describes, it will work with our system.

You do not need to know anything about WhatsApp internals. You only build **one HTTP endpoint**.

---

## 1. The big picture (30 seconds)

```text
Customer (WhatsApp)  -->  Our Gateway  -->  YOUR BOT  (POST /ai-reply)
                                              |
Customer (WhatsApp)  <--  Our Gateway  <------+   { "replies": ["Hello!"] }
```

1. A customer sends a message (text, photo, voice note, PDF) to your WhatsApp number.
2. Our gateway collects the messages and sends them to **your bot** as an HTTP `POST`.
3. Your bot reads them, thinks (AI, rules, database, anything), and returns a short JSON answer.
4. Our gateway sends your answer to the customer on WhatsApp.

**Your job:** receive JSON, return JSON. That is all.

You give us **one base URL** (example: `https://bot.yourcompany.com`). We call:

```text
POST https://bot.yourcompany.com/ai-reply
```

---

## 2. What we give you, what you give us

| We give you | You give us |
|---|---|
| Test key (`wab_test_...`) | Your bot base URL (HTTPS in production) |
| Live key (`wab_live_...`) | Your WhatsApp business number |
| **Bot secret** (`whsec_...`): used to check that a request really came from us | (Optional) status webhook URL |

Keep the **bot secret** private. Put it in an environment variable. Never commit it to git.

---

## 3. The request you will receive

### 3.1 Headers

```text
Content-Type:          application/json
X-Gateway-Timestamp:   1730300000          (unix time in seconds)
X-Gateway-Signature:   v1=9c1d...          (proof the request is from us, see section 5)
X-Request-Id:          8f2c1a7e-...        (unique per request; the SAME id if we retry)
X-Vendor-Id:           7d1c...
X-Contract-Version:    1
```

### 3.2 Body

```json
{
  "contractVersion": 1,
  "requestId": "8f2c1a7e-...",
  "chatId": "923001234567@c.us",
  "chatTitle": "Sara Khan",
  "senderName": "Sara Khan",
  "senderNumber": "+923001234567",
  "isSaved": false,
  "incomingMessages": [
    { "text": "Is the red lehenga available?", "timestamp": "19:30:12", "media": null }
  ],
  "chatHistory": [
    { "fromMe": false, "text": "Hi", "timestamp": "19:10:05" },
    { "fromMe": true,  "text": "Hello, how can I help?", "timestamp": "19:10:20" }
  ]
}
```

### 3.3 Field reference

| Field | Type | Meaning |
|---|---|---|
| `contractVersion` | number | Always `1` for this document. |
| `requestId` | string | Unique id of this request. If we retry (network problem), you receive the **same** id again. |
| `chatId` | string | WhatsApp chat id of the customer. |
| `chatTitle` | string | Name of the chat as shown in WhatsApp. |
| `senderName` | string | Customer name (may be empty). |
| `senderNumber` | string | Customer phone number in international format. |
| `isSaved` | boolean | `true` if the customer is saved in the WhatsApp contacts of this number. |
| `incomingMessages` | array | **One or more** new messages from the customer (see 3.4). |
| `chatHistory` | array | Previous messages (about the last 60) for context. Text only. Media is replaced by a label such as `[Image]`. `fromMe: true` means your number sent it. |

### 3.4 Important: `incomingMessages` is a LIST

If a customer sends 3 messages quickly, we wait a few seconds (7 seconds) and send all of them in **one request**. So `incomingMessages` can have 1, 2, 3, or more items.

**What you should do:** read all of them together and send **one answer** for the whole group. Do not answer each one separately.

Example (customer typed three messages in a row):

```json
"incomingMessages": [
  { "text": "Hi",                 "timestamp": "19:30:01", "media": null },
  { "text": "I want a lehenga",   "timestamp": "19:30:04", "media": null },
  { "text": "Red color please",   "timestamp": "19:30:06", "media": null }
]
```

You send back one answer, for example: `"Sure! We have red lehengas starting from 85,000."`

---

## 4. The five kinds of messages you can receive

Every item in `incomingMessages` has the same shape:

```json
{ "text": "...", "timestamp": "HH:MM:SS", "media": null or { ... } }
```

- `text` is the typed text (or the caption of a photo). It can be an empty string.
- `media` is `null` for plain text. For files it is an object (below).

### 4.1 Plain text

```json
{ "text": "Is the red lehenga available?", "timestamp": "19:30:12", "media": null }
```

### 4.2 Image (with or without caption)

```json
{
  "text": "Is this available?",
  "timestamp": "19:32:10",
  "media": {
    "hasMedia": true,
    "mediaType": "image",
    "mimeType": "image/jpeg",
    "fileName": "image_1730000000",
    "fileSize": 245678,
    "caption": "Is this available?",
    "base64Data": "/9j/4AAQSkZJRgABAQ..."
  }
}
```

### 4.3 Voice note or audio

```json
{
  "text": "",
  "timestamp": "19:35:01",
  "media": {
    "hasMedia": true,
    "mediaType": "ptt",
    "mimeType": "audio/ogg",
    "fileName": "ptt_1730000100",
    "fileSize": 48200,
    "caption": "",
    "base64Data": "T2dnUwACAAAAAAAA..."
  }
}
```

`mediaType` is `ptt` (a voice note recorded in WhatsApp) or `audio` (an audio file). The usual format is `audio/ogg` (opus).

### 4.4 Document (PDF, Word, Excel...)

```json
{
  "text": "",
  "timestamp": "19:40:00",
  "media": {
    "hasMedia": true,
    "mediaType": "document",
    "mimeType": "application/pdf",
    "fileName": "measurements.pdf",
    "fileSize": 120000,
    "caption": "",
    "base64Data": "JVBERi0xLjQK..."
  }
}
```

### 4.5 Sticker

```json
{
  "text": "",
  "timestamp": "19:41:00",
  "media": {
    "hasMedia": true,
    "mediaType": "sticker",
    "mimeType": "image/webp",
    "fileName": "sticker_1730000200",
    "fileSize": 18000,
    "caption": "",
    "base64Data": "UklGRi..."
  }
}
```

You can ignore stickers, or treat them as "the customer sent a sticker".

### 4.6 Supported `mediaType` values

| `mediaType` | What it is |
|---|---|
| `image` | Photo |
| `sticker` | Sticker |
| `document` | PDF, Word, Excel, etc. |
| `ptt` | Voice note |
| `audio` | Audio file |

Video is **not** supported yet.

### 4.7 Large files: `base64Data` can be `null`

To keep requests fast, we do not send very large files. In that case `base64Data` is `null`:

```json
{
  "text": "",
  "timestamp": "19:45:00",
  "media": {
    "hasMedia": true,
    "mediaType": "document",
    "mimeType": "application/pdf",
    "fileName": "catalog.pdf",
    "fileSize": 31457280,
    "caption": "",
    "base64Data": null,
    "skippedLargeFile": true
  }
}
```

| Flag | Meaning |
|---|---|
| `skippedLargeFile: true` | This single file is bigger than **15 MB**. |
| `skippedCombinedLimit: true` | All media in this group together is bigger than **24 MB**. |

**What you should do:** do not crash. Reply politely, for example: *"Your file is too large for me. Please send a smaller one."*

### 4.8 What is Base64? (easy explanation)

JSON can only carry text. So we turn the file (photo, audio, PDF) into a long text string. That string is called Base64. To use the file, turn it back into bytes:

```python
# Python
file_bytes = base64.b64decode(media["base64Data"])
```

```js
// Node.js
const fileBytes = Buffer.from(media.base64Data, 'base64');
```

After that, treat `file_bytes` like a normal image/audio/PDF file (send it to your vision model, speech-to-text, PDF reader, etc.).

**Body size:** with media, one request body can be up to about **30 MB**. Increase the body-size limit of your web framework (for example, Express: `limit: '50mb'`).

---

## 5. Security: check that the request really came from us

Anyone on the internet could try to call your bot. The **signature** proves the request is from our gateway, was not changed on the way, and is not an old request being replayed.

### 5.1 How the signature is made

```text
signature = HMAC_SHA256( bot_secret, timestamp + "." + raw_body )
```

- `bot_secret`: the secret we gave you (`whsec_...`).
- `timestamp`: the value of header `X-Gateway-Timestamp`.
- `raw_body`: the **exact bytes** of the request body, as received.
- The header looks like: `X-Gateway-Signature: v1=<hex>`.

### 5.2 What you must check (4 steps)

1. The timestamp is within **5 minutes (300 seconds)** of your current time. Older requests are rejected (this stops replay attacks).
2. Calculate the signature yourself with your bot secret.
3. Compare it with the header using a **constant-time compare** (not `==`).
4. If anything is wrong, return **HTTP 401** and do nothing else.

### 5.3 Python example

```python
import hashlib
import hmac
import time


def verify_gateway_signature(secret: str, headers, raw_body: bytes, tolerance: int = 300) -> None:
    ts = int(headers["X-Gateway-Timestamp"])
    if abs(time.time() - ts) > tolerance:
        raise PermissionError("stale request")

    expected = hmac.new(
        secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()

    # The header can hold more than one signature (see "Secret rotation" below)
    given = [p.strip().removeprefix("v1=") for p in headers["X-Gateway-Signature"].split(",")]
    if not any(hmac.compare_digest(expected, g) for g in given):
        raise PermissionError("bad signature")
```

### 5.4 Node.js example (Express)

```js
const crypto = require('crypto');
const express = require('express');
const app = express();

// Do NOT parse JSON first. You need the RAW bytes.
app.post('/ai-reply', express.raw({ type: 'application/json', limit: '50mb' }), (req, res) => {
  const ts = Number(req.header('X-Gateway-Timestamp'));
  if (Math.abs(Date.now() / 1000 - ts) > 300) return res.sendStatus(401);

  const expected = crypto.createHmac('sha256', process.env.GATEWAY_SECRET)
    .update(`${ts}.`).update(req.body)      // req.body is a Buffer (raw bytes)
    .digest('hex');

  const given = req.header('X-Gateway-Signature').split(',').map(s => s.trim().replace('v1=', ''));
  const ok = given.some(g => g.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(g), Buffer.from(expected)));
  if (!ok) return res.sendStatus(401);

  const data = JSON.parse(req.body.toString('utf8'));
  // ... your bot logic ...
  res.json({ replies: ['Sure, how can I help?'] });
});
```

### 5.5 Most common mistakes

- **Parsing the JSON first and then re-creating the string** (for example `JSON.stringify`). Spaces and key order change, so the signature no longer matches. Always verify with the **raw body**.
- Comparing with `==` (timing attack). Use a constant-time compare.
- Not checking the timestamp (replay attack).
- Keeping the secret in code or git.

### 5.6 Secret rotation

When we change your secret, for a few days the header contains **two** signatures:

```text
X-Gateway-Signature: v1=<new>,v1=<old>
```

The examples above already handle this: they accept the request if **any one** signature matches. After the rotation period, remove the old secret.

---

## 6. The response you must send back

### 6.1 Format

```json
{ "replies": ["Yes, the red lehenga is available.", "Prices start from 85,000."] }
```

| Rule | Detail |
|---|---|
| HTTP status | `200` |
| Body | A JSON **object** with a key named `replies` |
| `replies` | A **list of strings**. Each string becomes **one separate WhatsApp message bubble**. |
| Max bubbles | **5**. Extra ones are ignored. |
| Max length | **4000 characters** per bubble. |
| Format | Plain text only. WhatsApp formatting works (`*bold*`, `_italic_`). No HTML. |
| Silence | `{ "replies": [] }` means "do not answer this time". We send nothing to the customer. |
| Extra fields | Ignored (so you may add a `debug` field if you like). |
| Images / audio replies | Not supported yet. Text only. |

### 6.2 Right and wrong

```text
RIGHT:  { "replies": ["How can I help?"] }
RIGHT:  { "replies": [] }                      (stay silent)
WRONG:  { "replies": "How can I help?" }       (a string; it must be a list)
WRONG:  { "reply": ["How can I help?"] }       (wrong key name)
WRONG:  ["How can I help?"]                    (must be an object, not a bare list)
```

### 6.3 What happens when something goes wrong

If your bot returns `4xx` / `5xx`, replies too slowly, or returns a wrong format, the customer gets **nothing**. We do **not** send any automatic error message to the customer. The problem is shown on our side (as "Last error" and in the request logs).

So handle errors inside your bot and, when you can, still return a friendly answer.

### 6.4 Time limit

- Please reply within **25 seconds**.
- We stop waiting at **30 seconds** (timeout).
- If your AI is slow, set your own shorter timeout and return a short fallback text such as "One moment, we will get back to you."

---

## 7. Recommended flow inside your bot

```text
1. Receive the request, keep the RAW body bytes.
2. Verify signature + timestamp.            -> if bad: return 401
3. (Optional) Seen this requestId before?   -> if yes: return the same saved reply
4. Go through every item in incomingMessages:
      text only        -> use the text
      image            -> decode base64 -> vision model
      ptt / audio      -> decode base64 -> speech-to-text
      document         -> decode base64 -> extract text
      sticker          -> ignore (or note it)
      base64Data null  -> tell the customer to send a smaller file
5. Combine everything + chatHistory, create ONE answer.
6. Return { "replies": [...] }.
```

### 7.1 Python (FastAPI) example

```python
import base64
import json

from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
SECRET = "whsec_..."          # read this from an environment variable in real code


@app.post("/ai-reply")
async def ai_reply(request: Request):
    raw = await request.body()                                  # RAW bytes first
    try:
        verify_gateway_signature(SECRET, request.headers, raw)  # from section 5.3
    except PermissionError:
        raise HTTPException(status_code=401)

    data = json.loads(raw)

    parts: list[str] = []
    for m in data["incomingMessages"]:
        media = m.get("media")
        if m.get("text"):
            parts.append(m["text"])
        if not media:
            continue
        if media.get("base64Data") is None:
            parts.append("[file too large, ask the customer to send a smaller one]")
            continue

        blob = base64.b64decode(media["base64Data"])
        kind = media["mediaType"]
        if kind == "image":
            parts.append(await describe_image(blob, media["mimeType"]))   # your vision model
        elif kind in ("ptt", "audio"):
            parts.append(await transcribe(blob, media["mimeType"]))       # your speech-to-text
        elif kind == "document":
            parts.append(extract_document_text(blob, media["mimeType"]))  # your PDF/DOCX reader
        # sticker: ignore

    replies = await generate_reply("\n".join(parts), data["chatHistory"])  # your AI / logic
    return {"replies": replies}
```

`describe_image`, `transcribe`, `extract_document_text`, and `generate_reply` are **your** functions. We do not care how you build them.

### 7.2 Notes

- **Audio format:** WhatsApp voice notes are usually `audio/ogg` (opus). Some speech-to-text services accept this directly; others need `mp3` or `wav` (convert with ffmpeg). Please check your service documentation.
- **`requestId` cache (optional, saves you money):** if the network fails we may send the same request again with the **same** `requestId`. If you save your answer by `requestId` (5 to 15 minutes is enough) and return it again, your AI is not called twice. If you do not do this, our system still works; your AI may just run twice for that request.

---

## 8. How to test before going live

### 8.1 Test with a request you sign yourself

You can test your endpoint **today**, without us. This script creates a correctly signed request and sends it to your bot:

```python
import hashlib, hmac, json, time
import httpx

BOT_URL = "http://localhost:9000/ai-reply"
SECRET = "whsec_test_secret"          # use the same value your bot reads

body = json.dumps({
    "contractVersion": 1,
    "requestId": "test-0001",
    "chatId": "923001234567@c.us",
    "chatTitle": "Test User",
    "senderName": "Test User",
    "senderNumber": "+923001234567",
    "isSaved": False,
    "incomingMessages": [
        {"text": "Is the red lehenga available?", "timestamp": "19:30:12", "media": None}
    ],
    "chatHistory": [],
}).encode()

ts = int(time.time())
sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

r = httpx.post(BOT_URL, content=body, headers={
    "Content-Type": "application/json",
    "X-Gateway-Timestamp": str(ts),
    "X-Gateway-Signature": f"v1={sig}",
    "X-Request-Id": "test-0001",
    "X-Contract-Version": "1",
})
print(r.status_code, r.text)
```

Expected output: `200 {"replies": [...]}`.

### 8.2 Test cases you should try

| # | Test | Expected |
|---|---|---|
| 1 | Valid signed request, plain text | `200` and `{ "replies": [...] }` |
| 2 | Wrong signature | `401` |
| 3 | Timestamp older than 5 minutes | `401` |
| 4 | Three text messages in `incomingMessages` | One combined answer |
| 5 | Image message | Your bot decodes and understands it |
| 6 | Voice note | Your bot transcribes it |
| 7 | PDF document | Your bot reads it |
| 8 | Large file (`base64Data: null`) | Polite "send a smaller file" reply, no crash |
| 9 | Sticker | No crash |
| 10 | Body larger than 20 MB | Your server accepts it |
| 11 | Same `requestId` twice (optional) | Same answer, AI called once |
| 12 | Slow AI | Answer (or fallback text) within 25 seconds |

### 8.3 Test with our system

When our system is ready we will give you:

- A **test key** (`wab_test_...`) and your **bot secret**.
- `POST /v1/sandbox/simulate-incoming`: we send a real-format request to your bot (without WhatsApp) and return a **report**: what we sent, what you answered, and what was wrong (for example, wrong response shape).

No real WhatsApp message is needed for this step.

---

## 9. Rules and limits (quick table)

| Item | Value |
|---|---|
| Endpoint | `POST {baseUrl}/ai-reply` |
| Request grouping window | 7 seconds (messages in this window arrive together) |
| `chatHistory` size | About 60 messages, text only |
| One file | up to 15 MB, else `base64Data: null` + `skippedLargeFile: true` |
| All media in one request | up to 24 MB, else `skippedCombinedLimit: true` |
| Request body size | up to about 30 MB |
| Supported `mediaType` | `image`, `sticker`, `document`, `ptt`, `audio` (no video) |
| Signature tolerance | 300 seconds |
| Your reply time | within 25 seconds (we time out at 30) |
| Max bubbles per reply | 5 |
| Max characters per bubble | 4000 |
| Response status | `200` with `{ "replies": [...] }` |
| Silence | `{ "replies": [] }` |

---

## 10. Go-live checklist

- [ ] `POST /ai-reply` works and is reachable over HTTPS.
- [ ] Signature check uses the **raw body** and a constant-time compare.
- [ ] Requests older than 5 minutes are rejected with `401`.
- [ ] Wrong signature returns `401`.
- [ ] Text, image, voice, PDF, sticker, and large-file (`base64Data: null`) cases are all handled without crashing.
- [ ] All messages in `incomingMessages` are read together; you send **one** combined answer.
- [ ] Response is always `{ "replies": [...] }`, max 5 bubbles, 4000 characters each.
- [ ] Your server accepts bodies of at least 30 MB.
- [ ] You answer within 25 seconds (or return a short fallback text).
- [ ] The bot secret is in an environment variable, not in code.
- [ ] (Optional) Same `requestId` returns the same saved answer.
- [ ] You tested with `simulate-incoming` using the test key.
- [ ] One end-to-end test with a real number using the live key.

---

## 11. FAQ

**Do I need to know how WhatsApp works?**
No. You only receive JSON and return JSON.

**Can I send an image or a voice note back?**
Not yet. Replies are text only.

**What if I do not want to answer?**
Return `{ "replies": [] }`. The customer gets nothing.

**What if my bot is down or returns an error?**
The customer gets nothing. We do not send any automatic error message. We only see the error on our side.

**The signature fails even though my secret is correct. Why?**
Most likely you parsed the JSON and rebuilt the string before checking. Use the raw request bytes exactly as received.

**Can a customer message arrive twice?**
Only if we retry after a network problem. The retry has the **same** `requestId`. If you store answers by `requestId`, you can return the saved answer.

**How do I know the customer's name and number?**
Use `senderName` and `senderNumber`.

**Will this contract change?**
If it ever changes, `contractVersion` / `X-Contract-Version` will go up. This document describes version `1`.

---

*This document covers the customer-message flow (`/ai-reply`). Sending messages to customers yourself (OTP, order updates, marketing) is a separate API and is described in a separate document.*
