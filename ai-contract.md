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

You give us the **full inbound URL** in the admin panel (not only a host). We `POST` to **exactly that URL**. We do not add `/ai-reply` ourselves.

Examples:

```text
POST https://bot.yourcompany.com/ai-reply
POST https://api.yourcompany.com/clinics/gulberg/whatsapp
POST https://clinic-dha.yourcompany.com/v2/bot
```

A path like `/ai-reply` is fine. It is not required. Each WhatsApp number (each clinic) can have a different URL.

---

## 2. What we give you, what you give us

| We give you | You give us |
|---|---|
| Test key (`wab_test_...`) | Your full inbound bot URL (HTTPS in production), set in the admin panel |
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
  ],
  "vendorContext": {
    "clinicId": "gulberg",
    "city": "LHR"
  }
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
| `vendorContext` | object | Extra JSON that **your admin saved for this WhatsApp number**. The same object is sent on every inbound call for that number. It is not taken from the customer message. Example: `{ "clinicId": "gulberg" }`. If nothing was saved, this is `{}`. You may ignore keys you do not use. |

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
| Endpoint | `POST` to the **full URL** saved in the admin panel (we do not append a path) |
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

# Outbound: send a message to a customer yourself

This second part of the document is independent of `/ai-reply`. You use it when **you** want to send a message to a customer (OTP, order update, offer). The customer does not have to write to you first.

You do not need to know anything about WhatsApp internals. You only call **one HTTP endpoint**. Status can be read with a GET, or pushed to your webhook (optional).

---

## 12. The big picture (30 seconds)

```text
YOUR APP  -->  POST /v1/message-to-customer  -->  Our Gateway  -->  Customer (WhatsApp)

YOUR APP  <--  GET  /v1/message-to-customer/{jobId}     (you poll the status)
YOUR APP  <--  POST your webhook                        (optional: we push the status)
```

1. Your app calls our API with the customer's number and the text.
2. We put the message in a queue and return a `jobId` immediately (HTTP `202`).
3. Our Chrome plugin, connected to your WhatsApp number, sends the text on WhatsApp.
4. You can poll the status, or we can POST the final status to your webhook.

**Your job:** send a small JSON body, then handle the status (`sent`, `failed`, `expired`, `unknown`). That is all.

---

## 13. What we give you, what you give us

| We give you | You give us |
|---|---|
| Live key (`wab_live_...`) | The customer's number and the message text |
| Test key (`wab_test_...`) | `Idempotency-Key` on every OTP (and recommended on every call) |
| **Webhook secret** (`whsec_...`), if you use a webhook | (Optional) a status webhook URL |

Keep the **API key** and **webhook secret** private. Put them in environment variables. Never commit them to git.

The live key and the test key are different. With the **test key**, no real WhatsApp message is sent (see section 21).

---

## 14. The request you send

### 14.1 Endpoint and headers

```text
POST https://api.ourcompany.com/v1/message-to-customer
X-API-Key: wab_live_...                (use wab_test_... while testing)
Idempotency-Key: otp-order-9981        (required for OTP, recommended for every call)
Content-Type: application/json
```

The exact host is given to you at onboarding. Do not hard-code a host from this document.

### 14.2 Body (only 4 fields)

```json
{
  "number": "03486102017",
  "message": "Aap ka OTP 482913 hai.",
  "type": "otp",
  "ttlSeconds": 300
}
```

| Field | Required | Meaning |
|---|---|---|
| `number` | yes | Customer's phone number, in any common format (`0348...`, `+92348...`, `92348...`). We normalise it. |
| `message` | yes | The full text the customer will see. Plain text. Max **4000** characters. WhatsApp formatting works (`*bold*`, `_italic_`). No HTML. No images. |
| `type` | no | `otp`, `transactional`, or `marketing`. Default: `transactional`. |
| `ttlSeconds` | no | How long this message may wait in the queue. Min **30**, max **86400**. If you omit it, we use the default for that `type` (see the table below). |

We do **not** accept templates, buttons, or "Yes/No" options. You send the finished text. We forward that text.

---

## 15. `type`: which one to use

| `type` | When to use | Priority | Gap before the next message | Default TTL | Daily cap | Auto-retry if send fails |
|---|---|---|---|---|---|---|
| `otp` | Login / verification code | Highest (sent first) | 1–2 seconds | 300 (5 min) | none | **No** (so the customer never gets two OTPs) |
| `transactional` | Order confirm, delivery update, bill. **Default.** | Medium | 1–2 seconds | 3600 (1 hour) | none | No after send has started |
| `marketing` | Offer, sale, promotion | Lowest (sent last) | 8–20 seconds, random | 86400 (1 day) | yes (admin sets it per vendor, default **200** / day) | No after send has started |

**In plain words:** if you send 100 marketing messages at once, we **accept all of them** (HTTP `202`), but they go out slowly on WhatsApp (about one every 10–15 seconds). If an OTP arrives while marketing is waiting, the OTP jumps the queue and is sent first.

Pick `ttlSeconds` to match the real life of the message. For an OTP that expires in 5 minutes, send `ttlSeconds: 300` (or a little less). After the TTL, we never send that message.

TTL example:

```text
OTP, ttlSeconds=300, sent to us at 19:00:00.
  19:00:04  plugin is online   -> OTP is sent.
  19:06:00  plugin still offline -> job expired. The old OTP is never sent.
```

---

## 16. The response you get

### 16.1 Success (HTTP `202`)

```json
{ "success": true, "jobId": "job_7f3a", "status": "queued", "channelOnline": true }
```

| Field | Meaning |
|---|---|
| `jobId` | Id of this message. Use it to poll the status. |
| `status` | At this moment always `queued`. |
| `channelOnline` | Whether our plugin is online **right now**. `false` still means the message is queued; it will go out when the plugin comes back, as long as the TTL has not passed. |

### 16.2 Errors

Error body:

```json
{ "error": { "code": "INVALID_NUMBER", "message": "..." } }
```

| HTTP | `error.code` | Meaning |
|---|---|---|
| `400` | `INVALID_NUMBER` | Number is not valid (we expect 10–15 digits after normalisation). |
| `400` | `INVALID_REQUEST` | Empty `message`, unknown `type`, or `ttlSeconds` out of range. |
| `401` | `INVALID_KEY` | API key is missing, wrong, or revoked. |
| `403` | `VENDOR_SUSPENDED` | Your account is suspended. |
| `413` | `MESSAGE_TOO_LONG` | `message` is longer than 4000 characters. |
| `429` | `DAILY_CAP_EXCEEDED` | Marketing daily limit reached. Header `Retry-After` = seconds until tomorrow. |
| `429` | `RATE_LIMITED` | Too many requests in a short time. |
| `503` | `QUEUE_FULL` | Your queue is too full. Wait for older jobs to leave, then retry. |

A `202` does **not** mean the customer has the message. It only means we accepted the job. Check the status (section 18 or 19).

---

## 17. Idempotency-Key (how you stop duplicate OTPs)

**The problem:** you send an OTP, the network fails, you do not get our response, you call again. Without protection, the customer receives **two** OTPs.

**The fix:** send the **same** `Idempotency-Key` on the retry.

```text
1st call:   Idempotency-Key: otp-order-9981  ->  202  jobId=job_7f3a   (new job)
Retry:      Idempotency-Key: otp-order-9981  ->  200  jobId=job_7f3a   (same job, not a new one)
New OTP:    Idempotency-Key: otp-order-9982  ->  202  jobId=job_7f3b   (a different message)
```

Rules:

- **Required for `type: otp`.** Recommended for every call.
- Use something unique to the action, for example your order id or login-attempt id: `otp-order-9981`.
- A new real OTP (a new code) **must** use a new key.
- Keep the key at most 256 characters.

If you omit the key on an OTP, we reject the request (`400 INVALID_REQUEST`).

---

## 18. Poll the status of a message

```text
GET /v1/message-to-customer/job_7f3a
X-API-Key: wab_live_...
```

```json
{
  "jobId": "job_7f3a",
  "status": "sent",
  "sentAt": "2026-10-30T19:00:03Z",
  "errorCode": null
}
```

You can only read **your** jobs. Another vendor's `jobId` returns `404`.

### 18.1 Status values

| Status | Meaning | What you should do |
|---|---|---|
| `queued` | Waiting in the queue. | Wait, or poll again. |
| `sending` | The plugin is sending it now. | Wait. |
| `sent` | WhatsApp's **server** accepted the message. This is **not** "the customer has read it". | Done. |
| `failed` | Could not send. See `errorCode`. | Do **not** retry the same OTP. Use SMS or another channel if you must. |
| `expired` | TTL passed before we could send it. | Generate a **new** OTP (new `Idempotency-Key`) if the user still needs one. |
| `unknown` | We do not know if it was sent (network problem after send started). | **Do not send again.** Ask the customer, or think carefully before using a **new** `Idempotency-Key`. |
| `cancelled` | Stopped (for example the API key was revoked). | Do not retry this job. |

`errorCode` when `failed`:

| `errorCode` | Meaning |
|---|---|
| `NOT_ON_WHATSAPP` | This number is not on WhatsApp. Do not send again. |
| `SEND_FAILED` | Send failed for another reason. |

---

## 19. Webhook (optional): we push the status to you

If you give us a `status_webhook_url` at onboarding, we POST the **final** status to that URL. You do not have to poll.

The signature is the **same method** as section 5 of this document (HMAC of `timestamp + "." + raw_body`), but it is signed with your **webhook secret**, not the bot secret.

```text
POST https://yourapp.com/whatsapp/status
Content-Type: application/json
X-Gateway-Timestamp: 1730300000
X-Gateway-Signature: v1=9c1d...
X-Event-Id: evt_91ab...
```

```json
{
  "eventId": "evt_91ab",
  "event": "message.sent",
  "jobId": "job_7f3a",
  "number": "923486102017",
  "status": "sent",
  "at": "2026-10-30T19:00:03Z",
  "errorCode": null
}
```

| `event` | When |
|---|---|
| `message.sent` | WhatsApp's server accepted the message. |
| `message.failed` | Could not send (`errorCode` is set). |
| `message.expired` | TTL passed, never sent. |
| `message.unknown` | We do not know if it was sent. |

What you must do:

1. Verify the signature with the **webhook secret** (same 4 checks as section 5). Bad signature → `401`.
2. Return **HTTP 2xx within 10 seconds**. Do the rest of your work after you answer.
3. The same `eventId` can arrive again (our retry). Process it only once.
4. We retry on failure: 1 min, 5 min, 30 min, 2 hours, 6 hours (max 6 tries), then we stop. You can always poll `GET` status instead.

---

## 20. What you should **not** do

- Do **not** retry an OTP after status `sent`, `failed`, or `unknown`. That is how customers get two codes.
- Do **not** send the same marketing blast many times a day. There is a daily cap, and there is a ban risk (section 23).
- Do **not** send HTML, images, buttons, or template placeholders. Only the finished text.
- Do **not** put the API key in the URL or in logs.

When a customer **replies** to an OTP or an order message, that reply is a normal inbound message. It goes to your bot at `/ai-reply` (the first part of this document).

---

## 21. How to test outbound (no real WhatsApp needed)

When our system is ready we give you a **test key** (`wab_test_...`). Calls with the test key **never** send a real WhatsApp message. A virtual plugin answers, and the last 4 digits of the number decide the result:

```text
...0001    failed,  NOT_ON_WHATSAPP
...0002    first try WA_NOT_READY, second try sent
...0003    no result ever arrives (status stays, then unknown)
...0004    waits 30 seconds, then sent
any other  sent in 1–3 seconds
```

Example:

```text
POST /v1/message-to-customer
X-API-Key: wab_test_...
Idempotency-Key: otp-test-1
Content-Type: application/json

{ "number": "923000000001", "message": "Aap ka OTP 482913 hai.", "type": "otp", "ttlSeconds": 300 }
```

You should receive `failed` / `NOT_ON_WHATSAPP`. Use this to test your fallback (for example "send the OTP by SMS").

| # | Test | Expected |
|---|---|---|
| 1 | OTP with `Idempotency-Key` | `202` and a `jobId` |
| 2 | Same `Idempotency-Key` again | `200` and the **same** `jobId` |
| 3 | Number ending `0001` | `failed` / `NOT_ON_WHATSAPP` |
| 4 | Number ending `0003` | `unknown` — your code must **not** send a second OTP |
| 5 | Number ending `0004` | `sent` after about 30 seconds |
| 6 | Empty message / bad number | `400` |
| 7 | Marketing over the daily cap | `429 DAILY_CAP_EXCEEDED` |
| 8 | OTP without `Idempotency-Key` | `400 INVALID_REQUEST` |

The live key (`wab_live_...`) does **not** use these magic numbers. With the live key, `...0001` is treated as a real number.

---

## 22. Outbound rules and limits (quick table)

| Item | Value |
|---|---|
| Endpoint | `POST /v1/message-to-customer` |
| Status | `GET /v1/message-to-customer/{jobId}` |
| Body fields | `number`, `message`, `type`, `ttlSeconds` |
| `message` max length | 4000 characters, text only |
| `type` | `otp` / `transactional` (default) / `marketing` |
| Default TTL | otp 300, transactional 3600, marketing 86400 |
| TTL clamp | 30–86400 seconds |
| OTP auto-retry | never, once sending has started |
| Marketing daily cap | per vendor, default 200 (admin can change it) |
| Marketing gap | 8–20 seconds, random |
| OTP / transactional gap | 1–2 seconds |
| Webhook retries | 6 (1m, 5m, 30m, 2h, 6h) |
| Signature on webhook | same as section 5, with the **webhook secret** |
| Contract version | `1` |

---

## 23. Ban risk (please read)

This product sends messages through **WhatsApp Web**, not through the official WhatsApp Cloud API. WhatsApp can restrict or ban the number, especially if you send a lot of marketing, or send to people who did not opt in.

- Use `type: marketing` only for people who asked for it.
- Stay under the daily cap. The cap is there to protect the number.
- For large OTP or marketing volume, the official WhatsApp Cloud API is the safer long-term path.
- Real tappable buttons and the official OTP "Copy code" button exist only on the official Cloud API. This API sends **plain text**.

---

## 24. Outbound go-live checklist

- [ ] You send `Idempotency-Key` on every OTP (and keep the same key on retries).
- [ ] You handle `sent`, `failed`, `expired`, and `unknown`. You **never** auto-retry after `unknown` or `failed`.
- [ ] `NOT_ON_WHATSAPP` falls back to SMS (or you tell the user).
- [ ] `ttlSeconds` matches the real life of the message (especially OTP).
- [ ] Marketing stays under the daily cap. You handle `429 DAILY_CAP_EXCEEDED`.
- [ ] (If you use a webhook) signature is verified with the webhook secret, you return 2xx quickly, and you ignore a duplicate `eventId`.
- [ ] You tested with the **test key** and the magic numbers (section 21).
- [ ] One end-to-end test with a real number using the **live key**.

---

## 25. Outbound FAQ

**Do I need the customer to message me first?**
No. That is the point of this API. You can send first (OTP, order update, offer).

**Does `202` mean the customer has the message?**
No. It only means we accepted the job. Wait for `sent` (poll or webhook).

**The plugin was offline. Is my OTP lost?**
Not immediately. It waits in the queue until `ttlSeconds`. After that it becomes `expired` and is never sent.

**OTP went out twice. Why?**
Almost always because a retry used a **new** `Idempotency-Key`, or because you sent again after `unknown`. Use the same key on retries, and never auto-retry after `unknown` / `failed` / `sent`.

**I sent 100 marketing messages. When do they arrive?**
Slowly, about one every 10–15 seconds. 100 messages take about 20–25 minutes. An OTP that arrives in between is sent first.

**How many can I send from one number?**
Marketing: up to the daily cap (default 200). OTP and transactional: no daily cap, but very high volume increases the ban risk.

**The customer replied to the OTP. Where does that go?**
To your bot, `POST /ai-reply` (the inbound part of this document).

**The number is not on WhatsApp?**
Status `failed` with `errorCode: NOT_ON_WHATSAPP`. Do not send again on WhatsApp.

**Can I send buttons or an OTP "Copy code" button?**
Not on this API. Text only. Official buttons are a WhatsApp Cloud API feature.

**Can I send an image with the outbound API?**
No. Text only.

**Will this contract change?**
If it ever changes, `contractVersion` / `X-Contract-Version` will go up. This document describes version `1`.
