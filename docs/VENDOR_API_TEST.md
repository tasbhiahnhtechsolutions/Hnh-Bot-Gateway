# Vendor API test (gateway endpoints + payloads)

Is file se aap **hamare gateway** ko test kar sakte ho. Pehle **TEST key** use karo.
Asli WhatsApp par kuch nahi jata. Contract ki poori detail: `ai-contract.md` (version **1**).

Jo values hum denge, unhein neeche paste karo. Keys chat / git mein mat dalna.

```text
GATEWAY   = https://YOUR-NGROK-OR-HOST          # no trailing slash
TEST_KEY  = wab_test_...
LIVE_KEY  = wab_live_...                        # later, for real WhatsApp only
BOT_SECRET = whsec_...                          # for YOUR /ai-reply signature check
```

Har request par header (TEST ya LIVE, jo step ho):

```text
X-API-Key: <TEST_KEY ya LIVE_KEY>
```

| | TEST key `wab_test_` | LIVE key `wab_live_` |
|---|---|---|
| Sandbox inbound | Haan | Nahi (`403`) |
| Outbound | Nakli plugin, WhatsApp nahi | Asli WhatsApp (plugin chahiye) |
| Inbound customer message | Nahi (sandbox use karo) | Haan, plugin + WhatsApp Web |
| Risk | Koi nahi | Number ban ho sakta hai — sirf apna test phone, 2–3 msgs |

Swagger (browser): `{GATEWAY}/docs`

---

## 1. Key check — `GET /v1/plugin/whoami`

```bash
curl -sS "$GATEWAY/v1/plugin/whoami" \
  -H "X-API-Key: $TEST_KEY"
```

**Pass (`200`):**

```json
{
  "vendorId": "uuid",
  "businessName": "Your shop",
  "whatsappNumber": "92300...",
  "keyKind": "test",
  "inboundMode": "forward",
  "contractVersion": 1
}
```

| Galat | Code |
|---|---|
| Key missing / typo | `401` `INVALID_KEY` |
| Vendor suspended | `403` `VENDOR_SUSPENDED` |

LIVE key se wahi call; `keyKind` `"live"` hona chahiye:

```bash
curl -sS "$GATEWAY/v1/plugin/whoami" \
  -H "X-API-Key: $LIVE_KEY"
```

---

## 2. Inbound sandbox — `POST /v1/sandbox/simulate-incoming`

Pretend customer message. Gateway **aapke bot** ko admin mein save ki **poori inbound URL** pe `POST` karega (path khud add nahi hota).
**Sirf TEST key.** LIVE key = `403 TEST_KEY_REQUIRED`.

WhatsApp par jawab nahi jata. Report mein bot ka jawab dikhega.

```bash
curl -sS -X POST "$GATEWAY/v1/sandbox/simulate-incoming" \
  -H "X-API-Key: $TEST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"hello, is the red lehenga available?"}'
```

**Body (minimum):**

```json
{ "text": "hello, is the red lehenga available?" }
```

Optional media (contract section 4). `text` aur/ya `media` mein se ek zaroori:

```json
{
  "text": "is this available?",
  "media": {
    "hasMedia": true,
    "mediaType": "image",
    "mimeType": "image/jpeg",
    "fileName": "sample.jpg",
    "fileSize": 1234,
    "caption": "is this available?",
    "base64Data": null
  }
}
```

**Pass:** HTTP `200`, `"ok": true`, `"replies"` aapke bot ki list, `"bot.responseStatus": 200`.

Agar bot down / galat signature / galat JSON: `"ok": false`, `errorCode` / `bot` object mein wajah. Customer ko auto error nahi jata.

---

## 3. Outbound (TEST key, WhatsApp nahi) — `POST /v1/message-to-customer`

```bash
curl -sS -X POST "$GATEWAY/v1/message-to-customer" \
  -H "X-API-Key: $TEST_KEY" \
  -H "Idempotency-Key: t1" \
  -H "Content-Type: application/json" \
  -d '{"number":"923001234567","message":"hello from test","type":"transactional"}'
```

**Body:**

```json
{
  "number": "923001234567",
  "message": "hello from test",
  "type": "transactional"
}
```

| Field | Rule |
|---|---|
| `number` | Sirf digits, country code, **bina** `+` (10–15). `"string"` mat bhejna |
| `message` | Plain text, max 4000 characters |
| `type` | `otp` \| `transactional` \| `marketing` (default transactional) |
| `ttlSeconds` | Optional. 30–86400. `0` mat bhejna; field hata do |
| `Idempotency-Key` | Header, 1–128 printable ASCII. **otp par zaroori** |

**Pass:** `202`

```json
{
  "success": true,
  "jobId": "job_...",
  "status": "queued",
  "channelOnline": true
}
```

Phir status:

```bash
curl -sS "$GATEWAY/v1/message-to-customer/job_PASTE_ID" \
  -H "X-API-Key: $TEST_KEY"
```

TEST key par 1–3 second mein aksar `"status": "sent"` (virtual plugin; asli WhatsApp nahi).

Wahi `Idempotency-Key` dobara = wahi `jobId`, naya message nahi.

### OTP

```json
{ "number": "923001234567", "message": "Your code is 123456", "type": "otp" }
```

Header `Idempotency-Key` lazmi, warna `400`.

### TEST-only magic numbers (number ka aakhir)

| Aakhir | Nateeja |
|---|---|
| `...0001` | `failed` `NOT_ON_WHATSAPP` |
| `...0002` | pehli dafa ready nahi, doosri `sent` |
| `...0003` | ~2 min baad `unknown` |
| `...0004` | 30 s baad `sent` |

Magic numbers LIVE key par **nahi** chalte. Wahan asli WhatsApp jata hai.

---

## 3B. LIVE key testing (asli WhatsApp)

Pehle sections 1–3 TEST key se pass hon. LIVE tab, jab plugin connected ho.

**Zaroori pehle (humari side / aapke number wala Chrome):**

1. Chrome extension lagi ho, popup mein **LIVE** key (TEST nahi).
2. `web.whatsapp.com` khula ho, **wahi number** jo vendor ka WhatsApp number hai.
3. Popup: Gateway **Connected**, WhatsApp **Connected**.
4. Admin: inbound mode **forward**, bot URL public (ngrok/HTTPS), Test bot connection pass.
5. Sirf **apna doosra phone**. Anjaan numbers / zyada messages nahi.

Agar popup Connected nahi, LIVE outbound `queued` par atak sakti hai. Sandbox LIVE se nahi chalta.

### L1 — whoami (LIVE)

```bash
curl -sS "$GATEWAY/v1/plugin/whoami" \
  -H "X-API-Key: $LIVE_KEY"
```

**Pass:** `200`, `"keyKind": "live"`. Galat key = `401`.

### L2 — outbound transactional (LIVE)

`CUSTOMER` = doosre phone ke digits, bina `+` (jaise `923001234567`).
Har naye send par naya `Idempotency-Key` (`live1`, `live2`, …).

```bash
curl -sS -X POST "$GATEWAY/v1/message-to-customer" \
  -H "X-API-Key: $LIVE_KEY" \
  -H "Idempotency-Key: live1" \
  -H "Content-Type: application/json" \
  -d '{"number":"923001234567","message":"live hello test","type":"transactional"}'
```

**Pass:** `202` + `jobId`. 10–30 s mein:

```bash
curl -sS "$GATEWAY/v1/message-to-customer/job_PASTE_ID" \
  -H "X-API-Key: $LIVE_KEY"
```

`"status": "sent"`, phone par `live hello test`.  
Atka `queued` / `sending` = plugin ya WhatsApp Connected nahi.

Wahi `Idempotency-Key: live1` dobara = wahi `jobId`, **doosra** bubble nahi.

### L3 — outbound OTP (LIVE)

```bash
curl -sS -X POST "$GATEWAY/v1/message-to-customer" \
  -H "X-API-Key: $LIVE_KEY" \
  -H "Idempotency-Key: otp-live-1" \
  -H "Content-Type: application/json" \
  -d '{"number":"923001234567","message":"Your code is 482913","type":"otp"}'
```

Bina `Idempotency-Key` = `400`.  
**Pass:** jaldi `sent`, ek hi bubble. Wahi key 5 dafa = ek message.

### L4 — outbound fail cases (LIVE)

Alag `Idempotency-Key` har dafa.

```json
{"number": "string", "message": "x", "type": "transactional"}
```
→ `400 INVALID_NUMBER`

```json
{"number": "923009999999", "message": "ping", "type": "transactional"}
```
(number WhatsApp par na ho) → job `failed`, `NOT_ON_WHATSAPP`, kuch nahi bheja.

Lamba text (~5000 chars) → `413 MESSAGE_TOO_LONG`.

### L5 — inbound LIVE (customer likhe)

Sandbox yahan **mat** chalao (LIVE = `403`).

1. Doosre phone se **vendor ke WhatsApp number** ko `hello live inbound` bhejo.
2. 7–15 s mein aapka bot jawab WhatsApp par aaye (wahi `{ "replies": [...] }`).
3. 3 messages jaldi = **ek** bot call, ek jawab.
4. Group message = koi jawab nahi.

Aapka `/ai-reply` log: `X-Gateway-Signature`, `incomingMessages`, aap `200` + `replies`.

### L6 — TEST vs LIVE ek saath

| Call | Key | Asli WhatsApp |
|---|---|---|
| Sandbox `"hello"` | TEST | Nahi |
| `message-to-customer` | TEST | Nahi (`sent` virtual) |
| `message-to-customer` same number | LIVE | Haan, plugin se |
| Customer WhatsApp par likhe | (plugin LIVE) | Haan, bot jawab |

TEST job admin Jobs list mein `test` badge. LIVE job alag line. Ek vendor ki LIVE job doosri key se GET = nahi milni.

---

## 4. Aapka bot — inbound URL (aap host karte ho)

Hum **usi full URL** pe `POST` karte hain jo admin panel mein save hai. `/ai-reply` path zaroori nahi; agar aapke bot pe hai to URL ke andar likho.

Admin: **Bot inbound URL** = aapka public HTTPS (ngrok ho to poori path ke saath), jaise `https://xxxx.ngrok-free.dev/ai-reply`.

### Jo headers aayengi

```text
Content-Type:          application/json
X-Gateway-Timestamp:   1730300000
X-Gateway-Signature:   v1=<hex>
X-Request-Id:          8f2c1a7e-...
X-Vendor-Id:           ...
X-Contract-Version:    1
```

Signature: `HMAC_SHA256(BOT_SECRET, timestamp + "." + raw_body)`. Pehle **raw bytes** verify, phir JSON parse. Galat = **401**, kuch mat karo. Detail: `ai-contract.md` section 5.

### Jo body aayegi (example)

```json
{
  "contractVersion": 1,
  "requestId": "8f2c1a7e-1111-2222-3333-444444444444",
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

Jaldi 2–3 messages = **ek** request, `incomingMessages` list. Ek jawab do.

### Jo aapko return karna hai

HTTP `200`:

```json
{ "replies": ["Yes, the red lehenga is available.", "Prices start from 85,000."] }
```

| Rule | |
|---|---|
| `replies` | **array of strings** (string ya `"reply"` galat) |
| Khamoshi | `{ "replies": [] }` |
| Max | 5 bubbles, 4000 chars each |
| Time | 25 s try; 30 s par timeout |
| Extra fields | ignore (`debug` chalega) |
| Images/audio wapas | abhi nahi, sirf text |

Apne bot ko local test (secret verify skip karke nahi; production mein verify lazmi):

```bash
curl -sS -X POST "http://localhost:YOUR_BOT_PORT/ai-reply" \
  -H "Content-Type: application/json" \
  -d '{"contractVersion":1,"requestId":"local-1","chatId":"923001234567@c.us","chatTitle":"Test","senderName":"Test","senderNumber":"+923001234567","isSaved":false,"incomingMessages":[{"text":"hello","timestamp":"12:00:00","media":null}],"chatHistory":[]}'
```

Gateway ke through test = section 2 (sandbox). Wahan signature hum lagate hain.

---

## 5. Order (aapke liye)

**A. TEST (koi WhatsApp nahi)**

1. `whoami` TEST key se `200`, `keyKind: test`
2. Apna `POST /ai-reply` local, `{ "replies": ["..."] }`
3. Humein bot ki **full inbound URL** do; admin **forward** + Test bot connection
4. Sandbox `simulate-incoming` — report mein aapka jawab
5. `message-to-customer` TEST key — `202` phir `sent` (virtual)

**B. LIVE (plugin + WhatsApp Connected)**

6. `whoami` LIVE key se `200`, `keyKind: live`
7. L2 transactional — phone par message, status `sent`
8. L3 OTP + dobara wahi Idempotency-Key
9. L5 — doosre phone se inbound, bot ka jawab WhatsApp par
10. L4 fail cases (invalid number, NOT_ON_WHATSAPP)

LIVE tab shuru karo jab A poora pass ho.

---

## 6. Common errors

| Code | Matlab |
|---|---|
| `401 INVALID_KEY` | Key galat / copy toot gayi |
| `403 TEST_KEY_REQUIRED` | Sandbox par LIVE key |
| `403 VENDOR_SUSPENDED` | Account band |
| `400 INVALID_NUMBER` | Number digits nahi (Swagger ka `"string"`) |
| `400 INVALID_REQUEST` | Body khali / galat JSON / OTP bina Idempotency-Key |
| `413 MESSAGE_TOO_LONG` | Message 4000 se lamba |
| `429` | Rate limit ya marketing daily cap |
| Bot `401` sandbox report mein | `BOT_SECRET` match nahi / raw body se verify nahi kiya |
| LIVE outbound `queued` tries 0 | Plugin / WhatsApp Connected nahi |
| LIVE sandbox | `403 TEST_KEY_REQUIRED` — yeh sahi hai; inbound WhatsApp se karo |
