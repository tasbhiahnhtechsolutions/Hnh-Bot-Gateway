# Poora Testing Guide (step by step)

Is guide mein sab kuch ek saath hai: server chalana, vendor banana, extension lagana,
**inbound** (customer likhe, bot jawab de), **outbound** (hum customer ko message bhejein),
Swagger, aur admin panel. Har step ke aage likha hai **kya dikhna chahiye**. Pehle ek step
khatam karo, phir agla.

**Sab se zaroori:** asli WhatsApp par apna personal number zyada istemal mat karo. Sirf apne
**doosre phone** ko 2-3 messages bhejo. Zyada ya anjaan numbers ko bhejne se number ban ho sakta hai.

Ye guide dono tarah ke test cover karti hai:
- **Bina WhatsApp ke** (Part 2, 3): TEST key, asli message nahi jata. Pehle ye karo.
- **Asli WhatsApp ke saath** (Part 4 se 7): LIVE key aur extension.

---

## Part 0. Do browser tabs aur ek terminal

- Swagger: `http://localhost:8000/docs`
- Admin: `http://localhost:8000/admin`
- Terminal: har dafa pehle is folder mein jao:

```bash
cd "/home/jawad/Turabees Whatsapp Bot/Turabees_Whatsapp_Bot_V2/whatsup-plugin"
```

`make` ke saare commands isi folder se chalte hain.

---

## Part 1. Server chalao

```bash
make up
curl localhost:8000/ready
```

**Dikhna chahiye:** `{"status":"ok","checks":{"database":"ok","redis":"ok"}}`

Pehli dafa admin banane ke liye (agar nahi bana):

```bash
make seed
```

Ye `admin@example.com` aur ek password **ek baar** print karta hai. Password copy kar lo.
Seed 3 demo vendors bhi banata hai (`demo-canned`, `demo-echo`, `demo-mockbot`).

Admin mein login karo: `http://localhost:8000/admin`.

**Dikhna chahiye:** Dashboard page, upar menu: Dashboard, Vendors, Request logs, Audit log.

Server band karna ho to `make down`.

---

## Part 2. Vendor banao (admin panel se)

1. Menu mein **Vendors**, phir **Create vendor**.
2. Form bharo (`*` wale zaroori hain):

| Field | Kya likhna hai |
|---|---|
| Business name | `My Test` |
| WhatsApp number | Wo number jis WhatsApp se Chrome mein login karoge. Country code ke saath, bina `+`, jaise `923001234567` |
| Email | Koi bhi |
| Bot inbound URL | Poori URL, jaise `http://host.docker.internal:3000/ai-reply` (purana Turabees bot). Path gateway khud add nahi karta. Echo test ke liye bhi chalega, bot na chale tab bhi |
| Status webhook URL | Khali chhod do (optional, Part 6 dekho) |
| Default country code | `92` |
| Marketing daily cap | Khali (default 200) |
| Inbound mode | **echo** (pehle ye) |
| Outbound mode | Default |

3. **Create** dabao.

**Agla page keys dikhata hai, sirf ek baar.** Notepad mein copy kar lo:
- **LIVE key** `wab_live_...` (asli WhatsApp ke liye)
- **TEST key** `wab_test_...` (practice, asli message nahi jata)
- **Bot secret** `whsec_...`
- **Webhook secret** `whsec_...`

Keys kisi ko share mat karo, screenshot mein bhi nahi. Kho jayein to vendor page se rotate karo.

**Dikhna chahiye:** vendor list mein naya vendor, status `active`.

---

## Part 3. Bina WhatsApp ke test (Swagger, TEST key)

Isme asli message kahin nahi jata. Pehle ye karo taake pata chale gateway theek hai.

Swagger (`/docs`) mein har endpoint par **Try it out** dabao. Wahan `x-api-key` ka field hota hai,
usmein key paste karo. Aur `Idempotency-Key` ka alag field hai.

### 3.1 Key check
`GET /v1/plugin/whoami`, `x-api-key` = **LIVE** key.
**Dikhna chahiye:** `200`, vendor ka naam aur number. Galat key daalo to `401`.

### 3.2 Inbound (customer ka message pretend karna)
`POST /v1/sandbox/simulate-incoming`, `x-api-key` = **TEST** key:
```json
{"text": "hello"}
```
**Dikhna chahiye:** report, jisme bot ka jawab hai. Mode `echo` hai to `hello` wapas aata hai.
LIVE key yahan do to `403 TEST_KEY_REQUIRED` aata hai, ye sahi hai.

Admin mein **Request logs** kholo: ye run wahan dikhna chahiye.

### 3.3 Outbound (customer ko message)
`POST /v1/message-to-customer`, `x-api-key` = **TEST** key, `Idempotency-Key` = `t1`:
```json
{"number": "923001234567", "message": "hello", "type": "transactional"}
```
**Dikhna chahiye:** `202` aur ek `jobId`.
Phir `GET /v1/message-to-customer/{job_id}` mein wo `jobId` daalo.
1 se 3 second baad `status: sent`.

Magic numbers (sirf TEST key ke saath):

| Number ka aakhir | Nateeja |
|---|---|
| `...0001` | `failed`, `NOT_ON_WHATSAPP` |
| `...0002` | pehli koshish ready nahi, doosri mein `sent` |
| `...0003` | koi jawab nahi, ~2 minute baad `unknown` |
| `...0004` | 30 second baad `sent` |

Ye bhi dekho:
- Wahi `Idempotency-Key` dobara: wahi `jobId` wapas milta hai (naya message nahi banta).
- `"type": "otp"` bina `Idempotency-Key` ke: `400`.
- Marketing cap (Part 6 mein).

### 3.4 Admin mein check
- Vendor kholo, **Messages** par job dikhni chahiye.
- OTP wali job mein code masked dikhta hai (`OTP ****23`), numbers masked.

---

## Part 4. Extension Chrome mein lagao

1. Terminal:
   ```bash
   make ext-dev
   ```
2. Chrome mein `chrome://extensions` kholo.
3. Upar **Developer mode** on karo.
4. **Load unpacked**, phir ye folder chuno: `whatsup-plugin/extension/dist/dev`
5. Extension ka popup kholo (puzzle icon se). **API Key** field mein **LIVE** key paste karo, **Save**.
6. Naye tab mein `web.whatsapp.com` kholo. **Wahi number login karo** jo vendor banate waqt diya tha.
   Number alag hua to reject hoga (default policy `reject`).
7. WhatsApp Web ka tab khula rakho.

**Dikhna chahiye:**
- Popup mein business ka naam aur number.
- **Gateway line: Connected**.
- **WhatsApp: connected** (ya isi jaisa).
- Admin mein vendor ka **Plugin (devices)**, **Live view**: online dikhe (har 5 s mein refresh).

Agar popup "Another computer is using this key" kahe: kisi aur Chrome mein wahi key chal rahi hai.
"Open WhatsApp Web" dikhe: WhatsApp ka tab khola nahi.

---

## Part 5. Inbound asli test (customer likhe, jawab aaye)

Vendor ka inbound mode **echo** ho (Vendor page, **Modes**, **Save modes**).

1. Doosre phone se apne vendor wale number ko `hello` bhejo.
2. 7-10 second mein wahi `hello` wapas aana chahiye.
3. Admin **Request logs** mein ek nayi request aur uska jawab.

Ye cases ek ek karke karo:

| Test | Kya karna hai | Kya hona chahiye |
|---|---|---|
| Burst | 3 message jaldi jaldi bhejo | Ek hi request, ek hi jawab (teeno ko cover karta hua) |
| Group | Kisi group mein message | Jawab nahi aana chahiye |
| Auto-reply off | Popup mein auto-reply band karo, message bhejo | Jawab nahi. On karo to wapas |
| Canned | Mode `canned`, rule banao (jaise "price"), "price" bhejo | Rule wala fixed jawab |
| Paused | Mode `paused` | Koi jawab nahi |

### Purane Turabees bot ke saath (mode `forward`)

1. Bot chalao (alag terminal):
   ```bash
   cd "/home/jawad/Turabees Whatsapp Bot/Turabees-Whatsapp-Bot/server"
   npm run start
   ```
   (port 3000)
2. Vendor page par **Bot inbound URL** `http://host.docker.internal:3000/ai-reply` ho.
   `host.docker.internal` ka rasta `docker-compose.yml` mein add ho chuka hai. `make up` dobara
   chalao agar abhi tak nahi chalaya.
3. Vendor page, **Bot connection**, **Test bot connection**. **Dikhna chahiye:** pass.
4. **Modes** mein Inbound mode `forward`, "I understand" ka checkbox lagao, **Save modes**.
5. Doosre phone se message bhejo. Bot ka AI jawab WhatsApp par aana chahiye.
6. Ye contract wale cases try karo:
   - Text message.
   - Photo bhejo (image).
   - Voice note (Groq key purane bot ki config mein honi chahiye).
   - PDF / document.
   - Bohot bari file (15 MB se zyada): bot crash nahi hona chahiye, saaf jawab aana chahiye.
   - Ek se zyada message ek saath.
7. Admin **Request logs** mein har request: mode `forward`, bot ka status, latency.

Purane bot mein 2 second ka rate limit hai (ek chat par 2 s ke andar doosri request par `429`).
Test ke beech thora ruko.
Purane bot mein signature check nahi hai (contract section 5). Local test ke liye theek hai,
production se pehle lagana chahiye.

---

## Part 6. Outbound asli test (hum customer ko message bhejein)

Extension Connected ho (Part 4). **LIVE** key use karo. Number apne doosre phone ka (country code ke saath, bina `+`).

Swagger se ya terminal se:

```bash
curl -X POST localhost:8000/v1/message-to-customer \
  -H "X-API-Key: wab_live_TUMHARI_KEY" \
  -H "Idempotency-Key: live1" \
  -H "Content-Type: application/json" \
  -d '{"number":"923XXXXXXXXX","message":"hello test","type":"transactional"}'
```

**Dikhna chahiye:**
- Response `202` aur `jobId`.
- Doosre phone par "hello test" aaye.
- `GET /v1/message-to-customer/{jobId}` mein `status: sent`.
- Admin, vendor, **Messages** mein job `sent`.

Har dafa `Idempotency-Key` badlo (`live2`, `live3`...), warna wahi purani job wapas milti hai.

| Test | Kya karna hai | Kya hona chahiye |
|---|---|---|
| Save na kiya hua number | Aisa number jo phone mein save nahi | Message jaye (**unverified**, Spike dekho, fail ho to batao) |
| Number WhatsApp par nahi | Koi ghalat number | `failed`, `NOT_ON_WHATSAPP`, kuch nahi bheja |
| OTP | `"type":"otp","message":"Your code is 123456"` + `Idempotency-Key` | Jaldi jaye; admin mein `OTP ****56` masked |
| Dobara wahi key | Wahi `Idempotency-Key` 5 dafa | Sirf ek message, baqi 4 ko wahi `jobId` |
| Do OTP jaldi | Alag keys se do OTP | Dono jayen, ek ke baad ek (1-2 s gap) |
| Marketing | 3 messages `"type":"marketing"` | Gap 8-20 s, beech mein OTP bhejo to wo foran jaye |
| Lamba text | 4000+ characters | `413 MESSAGE_TOO_LONG` |
| Emoji / Urdu | Urdu aur emoji wala text | Bilkul sahi dikhe |

### Daily cap
1. Admin, vendor, **Marketing daily cap** mein `2`, **Save cap**.
2. 3 marketing messages bhejo (alag `Idempotency-Key`).
3. Teesre par `429 DAILY_CAP_EXCEEDED`. Cap wapas 200 kar do.

### Webhook (status ki khabar), optional
Vendor create karte waqt **Status webhook URL** daalna padta hai (kisi aise URL par jo aap dekh sako,
jaise webhook.site). Phir har `sent`/`failed`/`expired`/`unknown` par gateway wahan POST karta hai,
signature header ke saath. Admin, vendor, **Webhooks** mein har koshish, aur **replay** ka button.
Agar URL nahi diya to webhook nahi jata, ye normal hai.

---

## Part 7. Tootne wale cases (asli WhatsApp par)

Ek ek karke, har ek ke baad popup aur Messages dekho. Poori list `docs/MANUAL_CHECKLIST.md` mein hai.

| Test | Kya karna hai | Kya hona chahiye |
|---|---|---|
| WhatsApp tab band | Tab band karo, message bhejo | Popup "Open WhatsApp Web", job intezar kare. Tab kholo to jaye |
| WiFi band/on | 5 min band, phir on | "Reconnecting...", phir "Connected". Intezar karti job **ek** baar jaye |
| Do computer | Doosre Chrome mein wahi key | Doosra "Another computer is using this key", pehla chalta rahe |
| Linked device hatao | Phone se plugin ka device hatao | "Logged out: scan the QR code", kuch nahi bheja |
| Chrome restart | Chrome band karke kholo | Line wapas khule |

---

## Part 8. Admin panel ke actions

| Test | Kya karna hai | Kya hona chahiye |
|---|---|---|
| Devices | Vendor, **Live view** | Online/offline aur WhatsApp state, 5 s mein update |
| Jobs | Vendor, **Messages** | Har job, masked number/OTP |
| Request logs | Menu, **Request logs** | Inbound requests, filter vendor ke hisaab se |
| Mode badlo | Echo se canned | Naye messages naye mode se, Audit log mein entry |
| Suspend | Vendor, **Status**, checkbox, **Suspend** | Plugin foran band, queued jobs `cancelled`, API `403` |
| Activate | **Activate** | Wapas chalne lage |
| Key revoke | **API keys**, **Revoke** | Wo key `401`, popup "Refused" |
| Key rotate | **Rotate live key** | Nayi key sirf ek baar dikhti hai |
| 2FA | `/admin/account` se enroll | Authenticator app ka code, ghalat code par login na ho |
| Vendor edit | Vendor page, **Edit** (superadmin), naam/email/bot URL/webhook URL/number badlo, **Save** | "Vendor saved."; Audit log mein sirf field ke naam (jaise `email`), value nahi. Number badalne par plugin disconnect hota hai |
| Vendor delete | Vendor page ke neeche **Delete vendor**, business ka exact naam type karo | Vendor, uski keys, jobs, logs sab hat jate hain; us ki key `401`. Sirf rokna ho to Suspend use karo |
| Support role | `make gateway ARGS="create-admin --email s@x.com --role support --generate"` | Sirf dekh sakta hai, edit par `403` |
| Audit | Menu, **Audit log** | Har badlaav, par keys/message text kabhi nahi |

---

## Part 9. Agar kuch na chale

| Masla | Sab se aam wajah |
|---|---|
| `make: No rule to make target` | Galat folder. Upar wala `cd` chalao |
| `curl .../ready` fail | Docker chal nahi raha ya `make up` poora nahi hua. `docker ps` dekho |
| Popup Connected nahi | Key galat, ya extension `make ext-dev` se nahi bani |
| Message nahi gaya, job `queued` | WhatsApp ka tab khula nahi, ya popup mein Connected nahi |
| Inbound par jawab nahi | Auto-reply off, mode `paused`, ya number mismatch (vendor ka number aur login number alag) |
| `Test bot connection` fail | Bot chal nahi raha, ya URL `localhost` hai (`host.docker.internal` hona chahiye) |
| `401` | Key galat ya revoke ho chuki |
| `403` | Vendor suspended, ya LIVE key sandbox par |
| `429` | Rate limit ya daily cap |

Logs dekhna: `make worker-logs`, ya `docker compose logs -f api`.

Koi test fail ho to ye bhejo: kaunsa Part aur step, kya dikha (screenshot ya error text),
popup kya kehta hai, aur admin ke Request logs / Messages mein kya likha hai.
Keys kabhi mat bhejna.

---

Isolation (do bots, do vendors, ek ka msg doosre ko na jaye) aur baqi edge cases:
`docs/EDGE_CASES.md`. Bots alag folders mein hain: `test-bot-rose-atelier` (port 3001) aur `test-bot-gold-jewel` (port 3002).

---

## Aakhir mein

`docs/MANUAL_CHECKLIST.md` ki table bharo (tareekh, kisne, pass/fail). Jo cheezen asli WhatsApp par
abhi sabit nahi hain: unsaved number, connection state, ack level, `@lid`, offline behaviour.
Unka nateeja `spike/SPIKE_REPORT.md` mein likho.
