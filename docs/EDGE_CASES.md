# Edge cases: inbound, outbound, isolation (production se pehle)

Pehle `docs/TESTING_GUIDE.md` ke happy path chal chuke hon. Yahan wohi cheezen hain
jin se production toot'ti hai. Har row ke saamne pass / fail likho.

**Do test SIM, kam messages.** Personal number par load mat daalo.

Isolation ke liye do extra bots (purane Turabees bot ke baghair):

| Shop | Folder | Port | Jawab ka nishan |
|---|---|---|---|
| Rose Atelier | `test-bot-rose-atelier/` | 3001 | `[ROSE-ATELIER]` |
| Gold Jewel | `test-bot-gold-jewel/` | 3002 | `[GOLD-JEWEL]` |
| Turabees (purana) | `Turabees-Whatsapp-Bot/server/` | 3000 | AI / couture |

Gateway Docker mein hai, bot URLs: `http://host.docker.internal:3001` aur `:3002`.

---

## 0. Do bots + do vendors

Teen terminals:

```bash
cd "/home/jawad/Turabees Whatsapp Bot/test-bot-rose-atelier"
npm install
BOT_SECRET=whsec_ROSE npm start
```

```bash
cd "/home/jawad/Turabees Whatsapp Bot/test-bot-gold-jewel"
npm install
BOT_SECRET=whsec_GOLD npm start
```

```bash
cd "/home/jawad/Turabees Whatsapp Bot/Turabees_Whatsapp_Bot_V2/whatsup-plugin"
make up
```

Admin se **do vendors**:

| | Rose | Gold |
|---|---|---|
| Business name | Rose Atelier | Gold Jewel |
| WhatsApp number | Number A (Chrome profile 1) | Number B (Chrome profile 2), **alag** |
| Bot URL | `http://host.docker.internal:3001` | `http://host.docker.internal:3002` |
| Inbound mode | `forward` | `forward` |
| LIVE / TEST keys | copy, ek baar | copy, ek baar |
| Bot secret | `BOT_SECRET` rose wale terminal mein | gold wale terminal mein |

Har vendor page par **Test bot connection**. Dono pass.

Bina doosre WhatsApp ke isolation **TEST key + Swagger** se bhi ho jati hai (neeche 1.1).
Do Chrome profiles tab chahiye jab asli WhatsApp par isolation dekhni ho.

---

## 1. Isolation (ek ka msg doosre ko na jaye)

### 1.1 Bina WhatsApp (Swagger, TEST keys) — pehle ye

1. `POST /v1/sandbox/simulate-incoming`, Rose ki **TEST** key, body `{"text":"lehenga price"}`.
2. Jawab mein `[ROSE-ATELIER]` hona chahiye. Gold ke terminal mein **koi line nahi**.
3. Gold ki TEST key se `{"text":"gold set"}`.
4. Jawab mein `[GOLD-JEWEL]`. Rose ke terminal mein **koi nayi line nahi**.
5. Rose ki LIVE key is endpoint par `403 TEST_KEY_REQUIRED`.

### 1.2 Asli WhatsApp (do profiles)

Do Chrome profiles, do extensions (`dist/dev`), do LIVE keys, do WhatsApp numbers.

1. Phone se Number A ko `hello rose` bhejo. Jawab `[ROSE-ATELIER]`. Gold terminal silent. Gold ke WhatsApp par kuch nahi.
2. Phone se Number B ko `hello gold` bhejo. Jawab `[GOLD-JEWEL]`. Rose silent.
3. Outbound: Rose LIVE key se doosre phone ko `from rose`. Sirf Rose ke WhatsApp se jaye. Gold ke Jobs mein ye job na ho.
4. Gold LIVE key se `from gold`. Sirf Gold se jaye.

Fail = galat shop ka tag, ya silent shop ke terminal mein request.

---

## 2. Inbound edge cases

Har case ke baad admin **Request logs** dekho.

| # | Test | Kya karna hai | Pass |
|---|---|---|---|
| I1 | Burst | 3 messages 2 s ke andar | Ek request, ek jawab, teeno text `incomingMessages` mein |
| I2 | Group | Group mein likho | Jawab nahi, log nahi (ya group skip) |
| I3 | Auto-reply off | Popup toggle off, message | Jawab nahi. On ke baad naya message chale |
| I4 | Mode paused | Admin inbound `paused` | Jawab nahi |
| I5 | Mode canned | Rule `price` → fixed text | Sirf "price" par wo text, bot call nahi |
| I6 | Mode echo | `hello` | Wahi `hello` wapas, bot call nahi |
| I7 | Number mismatch | Extension kisi aur number ke WhatsApp par | Popup warning / reject, bot ko galat number nahi |
| I8 | Photo | Image + caption | Bot crash nahi, jawab aaye |
| I9 | Voice note | PTT | Crash nahi (Turabees bot transcribe kare to text) |
| I10 | PDF | Chhoti PDF | Crash nahi |
| I11 | Bari file | 15 MB se zyada | Crash nahi, khush andaaz fail |
| I12 | Urdu / emoji | `کیا حال ہے 😊` | Wahi text bot tak, jawab theek |
| I13 | Khali / sticker | Sirf sticker | Crash nahi |
| I14 | Bot down | `3001` band karo, message | Customer ko auto error nahi, Request log mein bot error |
| I15 | Bot slow | (optional) bot mein sleep 35s | Timeout, customer ko kuch nahi |
| I16 | Galat signature | Bot secret galat | 401, customer ko kuch nahi |
| I17 | Retry same requestId | Network flap (WiFi 5s) | Bot do dafa alag jawab na de (idempotent) |

---

## 3. Outbound edge cases

LIVE key + Connected WhatsApp. TEST key se pehle magic numbers (guide Part 3).

| # | Test | Kya karna hai | Pass |
|---|---|---|---|
| O1 | Happy path | LIVE, doosra phone, transactional | `sent`, ack >= 1, ek bubble |
| O2 | Idempotency | Wahi `Idempotency-Key` 5 dafa | Ek `jobId`, ek bubble |
| O3 | OTP required key | OTP bina header | `400` |
| O4 | Do OTP alag keys | Do jaldi OTP | Dono, 1–2 s gap, koi duplicate nahi |
| O5 | Marketing pacing | 3 marketing | Gap 8–20 s; beech OTP foran |
| O6 | Daily cap | Cap = 2, 3 marketing | Teesra `429 DAILY_CAP_EXCEEDED` |
| O7 | Invalid number | `"number":"abc"` | `400 INVALID_NUMBER` |
| O8 | Not on WhatsApp | Koi band number | `failed NOT_ON_WHATSAPP`, kuch nahi bheja |
| O9 | Unsaved contact | Naya number (apna doosra SIM) | `sent` **ya** Spike fail — jhoot `sent` nahi |
| O10 | Too long | 5000 chars | `413 MESSAGE_TOO_LONG` |
| O11 | Urdu / emoji | Urdu + emoji | Phone par sahi |
| O12 | TEST vs LIVE | TEST key same number | Jobs mein `test` badge, WhatsApp par nahi |
| O13 | WhatsApp connecting | Tab load | Job `queued` rahe, `sent` na ho jab tak connected |
| O14 | Tab band | Tab close, job, tab kholo | Intezar, phir ek dafa jaye |
| O15 | Do computers, ek key | Doosra Chrome, wahi LIVE | Doosra refuse, pehla chale |
| O16 | Key rotate | Rotate live, purani key Swagger | `401`; nayi key se naya job |
| O17 | Suspend | Suspend, queued jobs | `cancelled`; API `403`; plugin kick |
| O18 | No ack | (asli, mushkil) | Kabhi jhoot `sent` nahi; lease ke baad `unknown` |
| O19 | Cross-vendor job | Rose ki jobId Gold ki key se GET | `404` / nahi dikhe |
| O20 | Rate limit | 1 minute mein bohot POST | `429`, koi extra send nahi |

---

## 4. Admin / security

| # | Test | Pass |
|---|---|---|
| A1 | Support role edit/delete | `403` |
| A2 | Edit bot URL | Nayi URL par Test bot connection |
| A3 | Delete vendor, purani key | `401`; bots ko call nahi |
| A4 | Audit log | Edit/delete/suspend; **keys aur message text nahi** |
| A5 | Jobs list | OTP masked, number masked |
| A6 | 2FA ghalat code | Login nahi |
| A7 | Redis band (optional `make chaos`) | Naya outbound `503`, purani accepted job kho na jaye |

---

## 5. Connection

Guide Part 7 + `docs/MANUAL_CHECKLIST.md`: WiFi, sleep, Chrome restart, linked device hatao.
Har baar: intezar karti job **ek** dafa jaye, dobara nahi.

---

## Fail ho to kya bhejna hai

- Kaunsa row (I1, O3, …)
- Swagger status + body (keys nahi)
- Dono bot terminals ki last 5 lines
- Popup ka Gateway / WhatsApp text
- Admin Messages / Request logs ki 1 screenshot (keys crop)
