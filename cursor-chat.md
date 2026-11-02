# Pora project explanation
_Exported on 11/2/2026 at 17:37:52 GMT+5 from Cursor (3.27.22)_

---

**User**

@Turabees-Whatsapp-Bot 

pora project ko asa senior architect ka toor pa samjo aur mujhe explain karo

---

**Cursor**

I'll explore the Turabees-Whatsapp-Bot project end-to-end and explain it the way a senior architect would.

**Turabees-Whatsapp-Bot** ek local-first system hai: Chrome extension WhatsApp Web ko hook karti hai, Node/Express AI + logging + dashboard chalata hai. Official WhatsApp API nahi — browser session ke through unofficial automation.

---

## System kya hai?

**Turabees** (wedding couture) ke liye customer-service bot:

| Capability | Behavior |
|---|---|
| Message logging | Groups + DMs terminal pe colored logs |
| Auto-reply | Sirf **1:1 chats** — persona **“Sara”**, knowledge base se answers |
| Media | Images → vision; voice → Groq Whisper; PDFs → local extract |
| Dashboard | `localhost:3000` pe contacts + chat history |

Cloud DB, multi-user auth, official Business API — **scope mein nahi**.

---

## Architecture (4 layers)

```
WhatsApp Web (logged-in Chrome)
        │
   injected.js  ← MAIN world: WA internal modules hook
        │ postMessage
   content.js   ← bridge (isolated world)
        │ chrome.runtime
   background.js ← HTTP client + command poll (1.5s)
        │ localhost:3000
   server.js (Express)
        ├── Nebius (OpenAI-compatible LLM)
        ├── Groq Whisper (optional voice)
        └── turabees_knowledge_base.md
```

**Design idea:** browser = WhatsApp adapter; server = brain + UI + orchestration. Extension fragile WhatsApp internals handle karti hai; business/AI server pe rahti hai.

---

## Core flows

**1. Message capture**  
`WAWebMsgCollection` pe `add` hook → filters (outgoing ignore, newsletter/status skip, dedupe) → groups/logging turant; 1:1 + auto-reply on → **7s burst buffer** (rapid typing ko ek request mein merge).

**2. AI reply**  
Burst → `POST /ai-reply` → history merge → media prep → system prompt (Sara + full KB) → LLM → JSON `{ replies: [...] }` → WhatsApp pe multiple bubbles (internal send → DOM fallback).

**3. Dashboard history (async command queue)**  
Browser `GET /api/chat-history` → server queue mein command → extension poll → `injected.js` history nikaalta hai → `command-result` → HTTP response complete. Extension aur dashboard loosely coupled rehte hain.

---

## Stack & structure

| Piece | Tech |
|---|---|
| Extension | Chrome MV3, no build |
| Server | Express, cors, pdf-parse |
| LLM | Nebius Token Factory (`gemma-3-27b-it` default) |
| Storage | **In-memory only** — restart pe wipe |

Important paths:

- `extension/injected.js` — WhatsApp engine (~core complexity)
- `extension/background.js` — HTTP + poll
- `server/server.js` — monolith: AI, routes, memory, queue
- `server/data/turabees/turabees_knowledge_base.md` — brand/policy context
- `server/public/index.html` — dashboard
- `server/config.json` — local secrets (gitignored)

---

## Architectural judgment

**Strengths**
- Clear pipeline: inject → bridge → background → server
- Burst debounce real chat behavior match karta hai
- Resilient send path (3-level fallback)
- Docker + healthcheck server ke liye ready

**Risks / debt (senior view)**
1. **No API auth** — port open ho to LAN se `/ai-reply` / contacts abuse possible  
2. **Unofficial WA hooks** — Web update pe break; ToS/account risk  
3. **README drift** — docs ab bhi Gemini/`fabrics.json` batate hain; code Nebius + markdown KB use karta hai  
4. **Monolith** — ~1400 lines `server.js` + `injected.js`  
5. **No persistence** — memory/contacts restart pe gayab  
6. Config `debounceSeconds` banner-only; burst **hardcoded 7s** in extension

---

## Nayi feature kahan plug hoti hai?

| Change | Touchpoint |
|---|---|
| Message rule / mute | `injected.js` → `handleIncomingMsg` |
| Reply tone / policy | KB md + `generateHumanReply` system prompt |
| Naya media type | media extract + `/ai-reply` mapper |
| Dashboard UI | `public/index.html` + naya Express route |
| Background job from UI | command-queue pattern (jaise history fetch) |

---

**One-liner:** yeh unofficial WhatsApp Web sidecar hai — extension adapter, Express AI gateway, markdown KB se Turabees CS persona; production-grade multi-tenant SaaS nahi, local ops tool hai with sharp edges around auth, persistence, aur WA fragility.

Agar chaho to next mein kisi ek depth pe ja sakte hain: message pipeline, AI prompt design, ya dashboard command queue.

---

**User**

yani extension ka folder ma jo code ha wo wo google chrome extension ka ha uski complete ui wagera baki server ma api endpoint ha jo ka extension call karta ha ?

---

**Cursor**

Haan — understanding almost sahi hai. Ek chhoti clarification:

## Extension folder = Chrome extension

`Turabees-Whatsapp-Bot/extension/` poora **Google Chrome (Manifest V3) extension** hai:

| File | Role |
|---|---|
| `manifest.json` | Extension config / permissions |
| `popup.html` + `popup.js` | Extension ka **chhota UI** (on/off, auto-reply toggle, health) |
| `content.js` | WhatsApp Web page pe bridge |
| `injected.js` | Actual WhatsApp hooks (messages, send, history) |
| `background.js` | Server ko HTTP calls (yahi APIs hit karta hai) |

Yeh Chrome pe “Load unpacked” se install hota hai, aur sirf `web.whatsapp.com` ke saath kaam karta hai.

## Server = APIs (+ dashboard)

`server/` pe Express chal raha hai. Extension (via `background.js`) mainly yeh call karti hai:

- `POST /log-message`
- `POST /ai-reply`
- `POST /api/sync-contacts`
- `GET /api/poll-commands`
- `POST /api/command-result`

## UI kahan hai? (important)

- **Extension UI** = sirf popup (chhota control panel)
- **Bada WhatsApp-style dashboard** = extension mein nahi — `server/public/index.html` pe hai, browser se `http://localhost:3000` open karke

Matlab:

```
Chrome extension  →  localhost:3000 APIs  →  AI / logs / contacts
Browser dashboard →  same server APIs    →  contacts + chat history
```

**Short:** haan — `extension/` Chrome extension hai, `server/` APIs (aur dashboard) hai jo extension call karti hai. “Complete UI” agar dashboard se matlab hai, woh server side pe hai; extension ka UI popup tak limited hai.

---

**User**

mujhe ya batao ka abhi server kahi message db wagera ma store bi karta ha jo ka wo 3000 port pa dashboard pa dikaye ?

---

**Cursor**

Dashboard pe messages kahan se aate hain — DB se ya live WhatsApp se — code se confirm karta hoon.

**Nahi** — server messages ko kisi DB (Mongo/SQLite/Postgres/file) mein store nahi karta.

Dashboard pe jo dikhta hai woh live / temporary cache se aata hai:

### Contacts (sidebar)
- Extension WhatsApp Web se contacts sync karti hai → `POST /api/sync-contacts`
- Server unko sirf RAM mein rakhta hai: `contactsList = []`
- Dashboard `GET /api/contacts` se yehi list leti hai
- Server restart → contacts gayab, jab tak extension dubara sync na kare

### Chat messages (jab contact open karo)
- Dashboard `GET /api/chat-history` call karti hai
- Server **DB se nahi** nikalta — command queue mein `FETCH_CHAT_HISTORY` daalta hai
- Extension WhatsApp Web se messages fetch karke wapas bhejti hai
- Woh response seedha dashboard ko milta hai; permanently save nahi hota

### Jo RAM mein hai (`chatMemory`)
- Sirf **AI reply context** ke liye (last ~150 msgs)
- Dashboard history isse nahi dikhati
- Restart pe yeh bhi wipe

**Short:** port 3000 pe jo chat dikhti hai woh WhatsApp Web se on-demand aati hai (extension bridge se). Koi message database nahi hai.

---

**User**

POST /log-message
POST /ai-reply
POST /api/sync-contacts
GET /api/poll-commands
POST /api/command-result



in endpoint ko easy way ma define karo kis ka kiya kam ha 

---

**Cursor**

Simple language mein:

### `POST /log-message`
Extension naya WhatsApp message bhejti hai → server terminal pe colored log print karta hai.  
**Kaam:** sirf dikhana/log karna. Reply nahi banata.

---

### `POST /ai-reply`
1:1 chat ka message burst aata hai → server AI (Sara) se jawab generate karta hai → `{ replies: [...] }` wapas bhejta hai.  
**Kaam:** auto-reply ka brain. Extension phir WhatsApp pe message bhejti hai.

---

### `POST /api/sync-contacts`
Extension WhatsApp ke 1:1 contacts list server ko push karti hai.  
**Kaam:** dashboard sidebar ke liye contacts update karna (RAM mein).

---

### `GET /api/poll-commands`
Extension har ~1.5 sec poochti hai: “koi kaam pending hai?”  
Agar dashboard ne history maangi ho, server yahan command deti hai (jaise `FETCH_CHAT_HISTORY`).  
**Kaam:** server → extension ko orders dena.

---

### `POST /api/command-result`
Extension command ka result wapas bhejti hai (jaise chat messages).  
Server waiting dashboard request ko complete karti hai.  
**Kaam:** extension → server ko jawab dena.

---

**Ek line flow:**

```
Messages log     → /log-message
AI jawab chahiye → /ai-reply
Contacts update  → /api/sync-contacts
Server order?    → /api/poll-commands
Order ka result  → /api/command-result
```

---

**User**

yani agr ma as a senior architect ka toor per socho tu jo sb sa main endpoint ha wo /ai-reply q ka extension is endpoint ko call karta ha aur is sa jo reply ata ha wo extension ko milta ha aur extenion whatsup ui ko use kar ka reply back karta ha baki sb endpoint dashboard wagera ka liya ha agr mujhe dashboard chaye hi nhi ha sirf mera simple kam ha whatsup pa ana wala message ka reply karna tu sirf ak endpoint sa bi kam hojae ga na ?

---

**Cursor**

Haan — senior architect wali soch bilkul sahi direction mein hai.

### Auto-reply ke liye asal endpoint
**`POST /ai-reply`** hi core hai.

Flow:
1. WhatsApp pe message aata hai  
2. Extension capture + 7s burst  
3. `POST /ai-reply`  
4. Server AI reply deta hai  
5. Extension WhatsApp UI/internal APIs se reply bhejti hai  

Dashboard is flow ka part nahi.

### Baaki endpoints
| Endpoint | Zaroorat (sirf auto-reply)? |
|---|---|
| `/ai-reply` | **Haan — required** |
| `/log-message` | Nahi (sirf terminal logs / groups) |
| `/api/sync-contacts` | Nahi (dashboard) |
| `/api/poll-commands` | Nahi (dashboard) |
| `/api/command-result` | Nahi (dashboard) |

### Minimal system
Agar goal sirf “WhatsApp pe aaye message ka reply” hai, theoretically:

- Extension (message catch + send)
- Server pe **`/ai-reply`** (+ LLM config + knowledge base)

Itna kaafi hai. Dashboard wale 3 endpoints hata sakte ho.

### Chhoti caveats
1. **`/log-message`** optional hai — debugging/logs ke liye useful, reply ke liye zaroori nahi.  
2. Popup ka **`/health`** bhi optional — sirf “server alive?” check.  
3. Chat history AI ko already extension `/ai-reply` body mein bhejti hai; dashboard history alag path hai.

**Bottom line:** haan — dashboard nahi chahiye to architecture shrink ho ke practically **ek main endpoint (`/ai-reply`)** pe aa sakti hai. Baaki scaffolding logs/UI ke liye hai, core reply loop ke liye nahi.

---

**User**

1.Extension WhatsApp UI/internal APIs se reply bhejti hai



isko thora thk sa explain karo ka abhi extension whatsup ui ko use kar ka reply deta ha means dom pa click kar ka ya wo whatsup ki restapi use karta ha ?

---

**Cursor**

Reply bhejne ka exact path code se dekh raha hoon — DOM click hai ya WhatsApp internal APIs.

**Nahi** — yeh official WhatsApp REST API use nahi karti.

Reply bhejne ke **3 levels** hain, priority order mein:

---

### 1) Primary: WhatsApp Web ke **internal JS modules** (preferred)

WhatsApp Web browser mein apna private code load karta hai (`window.require('WAWebSendMsgChatAction')` wagaira).

Extension usi ko call karti hai:

- `WAWebSendMsgChatAction.addAndSendMsgToChat(...)`
- warna `chat.sendMessage(text)`

Yeh DOM click nahi — WhatsApp Web ke andar ka send function direct call. Same path roughly jo app khud use karti hai, lekin **public/documented REST API nahi**.

Isliye background chat pe bhi reply ja sakta hai (chat open kiye bina), jab internal method kaam kare.

---

### 2) Fallback: **DOM / UI simulation**

Agar internal send fail ho jaye, tab:

1. Chat open karo (`WAWebCmd`)
2. Compose box dhoondo (`contenteditable`)
3. Text `insertText` se likho
4. **Send button click** ya **Enter** press

Yeh wahi “UI ko use karke reply” wala path hai — human jaisa type + click.

---

### 3) Official WhatsApp API?

**Billkul nahi.**  
Na Business Cloud API, na koi Meta REST endpoint. Sirf logged-in **web.whatsapp.com** session + uske private frontend modules / DOM.

---

**Short:**  
Default = WhatsApp Web **internal APIs** (browser JS, unofficial).  
Backup = **DOM click/type**.  
Official REST API = use nahi hoti.

---

**User**

Primary: WhatsApp Web ke internal JS modules  is ka bara ma thora batao mujhe clear nhi hoa ha ya 

---

**Cursor**

Simple analogy se samjho:

---

## WhatsApp Web actually kya hai?

Jab tum `web.whatsapp.com` kholte ho, browser mein sirf UI nahi aata — **poora WhatsApp client JavaScript** load hota hai.

Us JS ke andar modules hote hain, jaise:

- messages store karna
- chat list
- **message send karna**
- media download

Yeh Meta ka **private/internal code** hai — public documentation nahi, na koi official “call this REST API from your extension” guide.

---

## “Internal JS modules” ka matlab

WhatsApp Web apne code ko modules mein todta hai. Unke names kuch aise hain:

- `WAWebMsgCollection` → naye messages sunna
- `WAWebSendMsgChatAction` → message bhejna
- `WAWebChatCollection` → chats dhundna

Extension page ke andar inject hokar `window.require('WAWebSendMsgChatAction')` se **usi function ko call** karti hai jo WhatsApp Web khud Send pe use karta hai.

Matlab:

```
Tum Send dabate ho     → WhatsApp ka internal send function chalta hai
Extension auto-reply    → Wahi internal send function seedha call
```

Beech mein DOM pe click zaroori nahi (primary path mein).

---

## REST API se farq

| | Official REST API | Internal JS modules |
|---|---|---|
| Kahan | Meta servers (`graph.facebook.com` etc.) | Tumhare browser ke andar, WhatsApp Web page pe |
| Kaise | HTTP request + Business account + tokens | Page ke JS functions call |
| Allowed? | Official, documented | Unofficial / reverse-engineered |
| Is project mein | Use nahi | Primary send path yahi hai |

Yahan koi alag server pe `POST /send-message` nahi ja raha. Extension **usi tab** mein baith ke WhatsApp ke apne JS ko bol rahi hai: “yeh text is chat pe bhej do.”

---

## Visual

```
┌─────────────────────────────────────────┐
│  Chrome tab: web.whatsapp.com           │
│                                         │
│  WhatsApp Web JS (internal)             │
│    ├── MsgCollection  (messages aaye)   │
│    ├── SendMsgChatAction (message bhejo)│← extension yahan call karti hai
│    └── ChatCollection                   │
│                                         │
│  injected.js (tumhari extension)        │
│    require('WAWebSendMsgChatAction')    │
│    addAndSendMsgToChat(chat, msgData)   │
└─────────────────────────────────────────┘
              │
              ▼
     WhatsApp network (jaise normal Web app)
```

---

## Kyun fragile hai?

Kyunki yeh **public contract** nahi. WhatsApp update kare aur module name / function change ho jaye, extension toot sakti hai. Isliye project mein DOM fallback bhi hai.

---

**Ek line:**  
Internal modules = WhatsApp Web ke hidden browser functions; extension unko hijack/call karke message bhejti hai — alag official API nahi.

---

**User**

yani approach achi q ka isma hum kuch illegal nhi kar raha aur kal ui ka change sa hamra code nhi kharab honga ?

---

**Cursor**

Donon assumptions thori **galat direction** mein hain. Clear karte hain:

---

### 1) “Illegal nahi kar rahe?”

Yeh **official WhatsApp Business API** nahi hai.

- Criminal “hacking” jaisa typical matlab usually nahi
- Lekin WhatsApp ke **Terms of Service** ke against unofficial automation / internal hooks hain
- Risk mainly: **account ban / restriction**, feature break, compliance issues (business use pe)

Safe/legal-feeling official raasta alag hai: **Meta WhatsApp Cloud API** (Business account, approved, documented).

Isliye architect view: yeh approach **clever + local-dev friendly** hai, “fully safe/compliant” nahi.

---

### 2) “UI change se code nahi kharab hoga?”

**Partial haan, partial nahi.**

| Path | UI (buttons/CSS) change | WhatsApp JS update |
|---|---|---|
| Internal modules (primary) | Zyada survivable — DOM pe depend kam | **Toot sakta hai** (module names/functions change) |
| DOM fallback | **Toot sakta hai** (selectors/buttons) | Bhi affect ho sakta hai |

Matlab:
- Primary path isliye choose hui ke har chhote UI tweak pe na toote
- Lekin WhatsApp Web ka **andaruni update** aaye to `WAWebSendMsgChatAction` wagaira break ho sakte hain — yeh common hai
- Isliye project mein DOM fallback bhi hai — jo UI change pe khud fragile hai

**Guarantee nahi** ke kal code safe rahega.

---

### Senior architect summary

| Claim | Reality |
|---|---|
| Approach smart hai local auto-reply ke liye | Haan |
| Illegal/zero-risk | Nahi — ToS + ban risk |
| UI change se kabhi nahi toote ga | Nahi — DOM fallback toot sakta; internal path bhi WA updates pe toot sakta |

**Bottom line:** yeh approach “DOM-only se better” hai, “future-proof + fully allowed” nahi. Long-term production ke liye official Cloud API zyada stable/compliant choice maani jati hai.

---

**User**

acha jo hamra ai message reply ki api ha uska inoput and output json do haar tarika ka input ka liya jasa text,imga,audio ka liya

---

**Cursor**

`/ai-reply` ka exact request/response shape code se nikaal raha hoon — text, image, audio ke examples ke saath.

Endpoint: **`POST /ai-reply`**

Output har case mein almost same hai. Farq mainly **input** ke `incomingMessages[].media` mein hota hai.

---

## Common wrapper (har request mein)

```json
{
  "chatId": "923001234567@c.us",
  "chatTitle": "Ali Khan",
  "senderName": "Ali Khan",
  "senderNumber": "+923001234567",
  "isSaved": true,
  "incomingMessages": [],
  "chatHistory": [
    {
      "fromMe": false,
      "text": "Hi",
      "timestamp": "19:10:05"
    },
    {
      "fromMe": true,
      "text": "Hello, how can I help you?",
      "timestamp": "19:10:20"
    }
  ]
}
```

`chatHistory` optional hai lekin AI context ke liye useful. Extension last ~60 msgs bhejti hai.

---

## 1) Text only

**Input**

```json
{
  "chatId": "923001234567@c.us",
  "chatTitle": "Ali Khan",
  "senderName": "Ali Khan",
  "senderNumber": "+923001234567",
  "isSaved": true,
  "incomingMessages": [
    {
      "text": "Do you have bridal lehengas in red?",
      "timestamp": "19:30:12",
      "media": null
    }
  ],
  "chatHistory": []
}
```

Burst (7s mein multiple texts):

```json
"incomingMessages": [
  { "text": "Hi", "timestamp": "19:30:01", "media": null },
  { "text": "Price of velvet suit?", "timestamp": "19:30:04", "media": null }
]
```

---

## 2) Image (+ optional caption)

**Input**

```json
{
  "chatId": "923001234567@c.us",
  "chatTitle": "Ali Khan",
  "senderName": "Ali Khan",
  "senderNumber": "+923001234567",
  "isSaved": true,
  "incomingMessages": [
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
        "base64Data": "/9j/4AAQSkZJRgABAQAAAQABAAD...",
        "thumbnailBase64": "/9j/4AAQ...",
        "previewUrl": "data:image/jpeg;base64,/9j/4AAQ..."
      }
    }
  ],
  "chatHistory": []
}
```

Server images ko vision LLM ko `image_url` data URL ke tor pe bhejta hai.

---

## 3) Audio / voice note (`ptt`)

**Input**

```json
{
  "chatId": "923001234567@c.us",
  "chatTitle": "Ali Khan",
  "senderName": "Ali Khan",
  "senderNumber": "+923001234567",
  "isSaved": true,
  "incomingMessages": [
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
        "base64Data": "T2dnUwACAAAAAAAA...",
        "thumbnailBase64": null,
        "previewUrl": null
      }
    }
  ],
  "chatHistory": []
}
```

`mediaType` `"audio"` bhi ho sakta hai.  
Agar `groqApiKey` set ho, server pehle transcribe karta hai aur andar `media.transcribedText` set karta hai (client ko yeh field bhejni zaroori nahi).

---

## 4) Document / PDF (bonus)

```json
"incomingMessages": [
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
]
```

Server PDF se text extract karke AI ko text context deta hai.

---

## Success output (har type ke liye same)

```json
{
  "success": true,
  "replies": [
    "Yes, we have red bridal lehengas available.",
    "Would you like me to share the catalogue link?"
  ],
  "usedKeyIndex": 1,
  "model": "google/gemma-3-27b-it"
}
```

- `replies` = string array → extension har item alag WhatsApp bubble bhejti hai  
- AI fail / empty → technical fallback string `replies` mein aa jati hai  

---

## Error / rate limit output

```json
{
  "error": "Rate limit: Please wait a moment before sending another request.",
  "replies": []
}
```
(HTTP **429** — same chat pe ~2s ke andar dobara call)

---

## Short cheatsheet

| Type | `media.mediaType` | Zaroori field |
|---|---|---|
| Text | `media: null` | `text` |
| Image | `image` | `base64Data` + `mimeType` |
| Voice | `ptt` / `audio` | `base64Data` + `mimeType` |
| PDF | `document` | `base64Data` + `application/pdf` |

**Output hamesha:** `{ success, replies[], usedKeyIndex, model }`

---

**User**

acha ak baat batao ab asal ma mujhe karna ya ka is plugin ko mujhe apna different whatsup no pa test karna ha yani in simple words plugin ki functionality same raha gi api mujhe different bot ki integrate karni ha let suppose mera no#01 ha uski apis bani means wo mera chatbot bana ha aur mera no#02 ha uski api chatbot bana ha mujhe asa backend interface banana honga jo ka haar chatbot ko integrate kara tu mujhe apni team#01 and team#02 na jo chatbot banaya unko api structure batana honga means mujhe unko clearity dani hongi ka hum tmahai post ki api call pa kasa data daga text,audio,image ma aur huma tm sa asa response chaye honga tu is sa ya honga huma extension ma ui ma ak option daga jaaha sa hum ai reply wali api ka baseurl set kar saka ga us sa honga ya ka hum extension sa multiple chatbot ko call kar saka ga aur hamara kam tu sirf api call karna aur jo reply aye unka response ma usko simple show karna honga huma is chez ki tension lana ki zarwat hi nhi ha haar url ka pecha jo chatbot ha wo unki team manage kara gi tmhe meri baat aye samj tm thk sa current architecture ko dakh kar plan banao ka huma code ma kiya change karna honga aur ya kasa possible honga detail ma sb batana hum api ka input and output structure kasa bataye ga team ko sb detail ma batana ha tmhe thk sa yr easy roman urdu ma plan banao jaha need ha exmaple ki zaror dana aur jaha mermaid diagram ki need ha wo bi dalna tmhe plan banata wa haar wrostcase ko sochna ha yr taka implement ka baad kuch break na ho aur sb thk sa chala 

---

**Cursor**

Plan banane se pehle extension ke woh hisse padh leta hoon jahan URL hardcoded hai (`background.js`, popup, manifest) aur dekhta hoon server logic ka kitna hissa extension mein shift hoga.

Research mostly ho gayi. Plan ka design do cheezon pe depend karta hai, isliye pehle yeh poochta hoon.

**Plan ke liye 2 important decisions**

> **Bot URL kis level pe set hoga? (WhatsApp Web ek Chrome profile mein ek hi number se login hota hai)**
> - Per number/profile: Number#01 wale Chrome profile ki extension mein URL1, Number#02 wale profile mein URL2 (simple, recommended)
> - Ek hi number pe, alag alag chats/contacts ko alag bot URL (routing rules chahiye)
> - Dono: default URL per number + optional per-chat override
>
> Answer: yr simple ak chrome tab pa whatsup khol ka waha pa hum extension ma change url karaga aur wo hamara backend sa simple connect honga

> **Team ki bot API ke liye security kaisi chahiye?**
> - Optional API key / Bearer token header (extension mein URL ke saath key bhi set ho) - Recommended
> - Koi auth nahi, sirf URL
>
> Answer: simple x-api key

---

**User**

yani is plan sa fiada ya honga ka huma sirf api call karni ha ab agr hum Case B ma image bhaja ga tu jo baseurl honga uska kam ha base64 image ko vision model ko pass karna aur audio ma same groq ya kisi aur aur ko hamara kam tu sirf sender and reciever ka honga na ?

---

**Cursor**

Haan, bilkul sahi samjhe. Is plan ke baad extension ka kaam sirf **courier** ka reh jata hai.

## Kaun kya karega

**Hamari extension (sender/receiver):**
- WhatsApp se message pakadna (text, image, voice, PDF)
- 7s burst mein jama karna
- Media ko base64 mein convert karke `POST {baseUrl}/ai-reply` pe bhejna
- Jawab mein `replies[]` aaye to WhatsApp pe bhej dena

**Bot team (BaseURL ke peeche):**
- Image: `base64Data` ko vision model (GPT-4o, Gemini, Gemma vision, etc.) ko dena
- Voice: `base64Data` ko Groq Whisper ya kisi aur STT se text banana
- PDF: text extract karna
- AI se jawab banana, knowledge base/persona/business logic lagana
- `{ "replies": ["..."] }` wapas dena

## Faida

- Har team apna model, apni keys, apna cost, apna prompt khud rakhegi.
- Hamari taraf se model badalne par extension change nahi hogi.
- Naya bot add karna sirf URL + API key set karna hai.

## Do cheezein dhyan mein rakhna

1. **Hum media format nahi badalte.** Voice mein `audio/ogg` (opus) aata hai. Groq Whisper ye accept karta hai, lekin kuch dusre STT services ke liye bot team ko convert karna pad sakta hai (e.g. ffmpeg se mp3/wav). Ye unki zimmedari hai, contract doc mein likhna chahiye.
2. **Hum sirf bhejte hain, bot ki quality ke zimmedar nahi.** Agar bot ne image samajh kar galat jawab diya ya timeout kar diya, to extension bas silent rahegi (customer ko kuch nahi jayega). Isliye "Last error" popup mein dikhana plan mein rakha hai, taa ke aapko pata chale masla bot side par hai.

Chhota sa correction: "sirf sender receiver" ke saath extension ko **WhatsApp ke andar ka kaam** bhi karna hai (message hook karna, media download, bubble bhejna). Yeh hissa hi sabse fragile hai, aur ye aap ki taraf rahega. Bot ka AI wala hissa poora teams ka.

---

**User**


yr ya plan bilkol thk ha aur phase#01 ma hamara kam itna hi ha ka huma base url configure ka option dana ha aur ak chez huma dashboard wagera abhi jo bana ha wo nhi chaye ha huma sirf aur sirf abhi ai reply api call karni ha aur jo reply aye ga usko simple way ma whatsup pa bhajna ha yr lakin ak sb sa imp chez jo huma iska phase 2 ma karni ha ka hamara pass admin pannel honga jaha sa hum kisi bi vendor ko onboard karaga aur onboard pa simple business name aur phone no aur email sa register karaga register karta hi x-api key aye ab ya key imp ha bhot zada q ka plugin pa base url ka sath x-api key dala gi jis sa pata chalaga ya kn sa user ha ya bhot imp ha q ka jo next functionality ma batana wala ho wo is sa hi related ha abhi tk kiya hota ha ka user huma whatsup pa message karta ha aur hum isko reply lakin kafi baar asa bi honga ka hum koi template kisi number pa bhajna ha jasa forget otp,marketing message wagera tu is case ma user na huma message nhi kiya ha balka huma us user ko direct whatsup message kar raha ha tu iska liya extension ma huma kuch sochna honga mera mind ma jo highlevel idea ha jb hum vendor ko onboard kar raha ha tu us wakt hum x-api key banata ha hum vendor ko apni ak api daga ga let suppose uska name ha endpoit ka /message-to-customer ya api payload ma huma {"number":"03486102017","message":"here is you otp code"} aur header ma x-api-key dagi hum is x-api-key sa identify karlaga ya kn sa vendor ha aur huma database ma us vendor ka against ma ak job table bana ka us ma ya job insert kara aur yaha yaha baar pooling nhi karaga hum postgress trigger ka use karaga taka jasa hi table ma data aye hum is pending jobs ko nikala aur check kara ya job kis vendor ka agaisnt ma ha aur hum us vendor ka plugin pa update bhaja aur plugin whatsup pa us no ko find kar ka wo message hamara no pa bhaj da lakin jo wrostcase tmhe sochna ha let suppose ak chrome pa 5 profile ha aur haar profile pa hamara plugin install hoa wa ha lakin main task ya ha jo message jis plugin ka liya ha wo usko hi jaye ga ab yaha huma phase#01 ma jo chez change karni ha waha hum extension ma url nhi laga x-api-key laga is api key sa huma pata chal jaye ga ka kn sa vendor ha aur onboard pa hum jo base url laga us baseurl ko hi call karaga api ka liya ab tmhe detail ma meri sb instruction ko samjna ha haar wrostcase ko sochna ha thk sa then pora plan bana ka dana ha mana requirement kafi agah pecha bataye ha lakim tmhe as a senior software architect ka toor pa haar chez ka wrostcase ko soch ka plan banana ha plan ma sb cheza bhot detail ma define lakin bhot easy roman urdu ma hona chaye ya jaha example ki need ha waha example dana aur jaha mermiad diagram ki need ha waha diagram zaror dana ha taka mujhe sb clear ho main purpose agr tmhe samj agaya tu tm planning thk sa kar sakta ho plan bana ka review bi karlana tm agr ma simple bolo chatbot ka base url ak hi honga hamara server ka lakin x-api key sa vendor differtiate honga  ab tm plan banao thk sa soch ka 

---

**Cursor**

Samajh gaya. Yeh requirement pehle plan se kaafi bari hai (gateway, vendor identity, outbound jobs, Postgres trigger, push to plugin), isliye pehle plan ko is nayi direction ke mutabiq dobara design kar raha hoon.

---

**User**

yr sb kuch perfect ha  baas is ma ak chez ka mujhe gate way  backend fastpai pa banana ha aur sb best practices ko follow karo kiya socket.io use karna zada better honga usma bhot sari cheza outof the box mil jati ha aur sb sa imp dispatch ka fastapi project ma celery zada acha means ma abhi for now backend ka hi project ho jo gateway ka toor pa bi kam karaga websocket pa toor pa bi kam karaga aur aur quee celery ma aur testcase add karo jis na worstcase ma possible ho haar tarika sa tmhe testcase ko check karna ha let browser pa internet off ho then connect ho bhot zada daar baas whatup session tu disconnect tu nhi hogaya in sb ko dakho overall plan bhot acha ha baas backend python fastapi pa ho aur plugin ki ui ma jo change karna ha wo karo tm plan update karo is hisab sa

---

**Cursor**

Plan file ka format dekh leta hoon, phir usi ko FastAPI + Socket.IO + Celery + test strategy ke hisab se update karta hoon.

Plan update ho gaya. Yeh file hai: `/home/shakeeb/.cursor/plans/vendor_gateway_two_phase_bad75e66.plan.md`

## Kya badla

**Backend ab FastAPI ka ek hi project hai.** Gateway (HTTP API), Socket.IO hub aur Celery dispatch, teeno isi mein hain.

**Socket.IO: haan, yeh behtar hai.** Wajah:
- Rooms mil jate hain. Har vendor ka room `vendor:{id}` hoga, to job galat plugin ko jana mushkil ho jata hai.
- Ack callbacks milte hain. Plugin ko pata chal jata hai ke result DB mein save hua ya nahi.
- Reconnect aur heartbeat built-in hain.
- Auth payload mein key ja sakti hai, URL mein nahi. Isse pehle wala ticket step khatam ho gaya.

Do cheezein dhyan mein rakhni hain:
- MV3 service worker mein sirf `websocket` transport chalega. Long-polling fallback nahi.
- `socket.io-client` ko extension ke andar bundle karna hoga.

**Celery se dispatch** (Postgres trigger ki jagah):
- DB hi sach ka source hai. Celery task sirf "jaag jao aur dekho" ka signal hai.
- Ek vendor ke ek waqt mein ek hi job ko DB ka partial unique index guarantee karta hai. Isse race conditions application code pe depend nahi karte.
- Pacing ke liye task mein `sleep` nahi. Result aane par agla task `countdown` ke saath schedule hota hai.
- Celery worker alag process hai, wo seedha socket ko emit nahi kar sakta. Is liye Socket.IO ka Redis manager (`write_only`) use hoga.
- Celery Beat har 30s ek safety net chalata hai. Ye isliye ke "DB commit ho gaya lekin Redis enqueue fail" wala case dab na jaye. Yeh normal polling nahi hai, sirf reconciliation hai.

**Plugin UI:**
- API key input.
- "Connected as <business>" status.
- Realtime aur WhatsApp state.
- Icon pe badge.
- Last error line.

## Test strategy (plan ka naya bara hissa)

Har worst case ke liye ek automatic test hai. Teen invariants har test mein check hote hain: koi job do baar nahi jata, job sirf apne vendor ko jata hai, aur koi job hamesha atka nahi rehta.

- **Internet off/on:** Toxiproxy se 10 scenarios. Job emit ke baad cut, `started` ke baad cut, half-open connection, flapping, high latency, TCP reset, 10 min outage.
- **Browser aur extension:** Playwright mein real Chromium, extension aur ek fake WhatsApp page. Isme service worker kill, tab reload aur WA state change test hote hain.
- **Celery:** broker down, worker `SIGKILL`, Redis/Postgres restart.
- **Hypothesis:** random event sequences pe state-machine test.
- **Load aur soak** tests.
- **Manual checklist** ek real staging number pe.

## "WhatsApp session disconnect toh nahi hoga?"

- Internet off hone se WhatsApp Web logout nahi hota. Wo "Connecting…" dikhata hai aur khud reconnect ho jata hai.
- Asli logout tab hota hai jab phone se linked device hata diya jaye, ban lage, ya browser data clear ho.
- Khatra ye hai ke plugin ko pata na ho ke WhatsApp connected nahi hai, aur wo job "bhej diya" samajh le. Isliye plugin `waState` report karega (`connected`, `connecting`, `offline`, `logged_out`, `not_loaded`).
- Job sirf `connected` par process hoga.
- `sent` ka matlab WhatsApp ka ack ≥ 1 hoga. WhatsApp offline hone par message locally queue kar leta hai aur baad mein bhej deta hai, jo OTP ke liye late delivery ka khatra hai.
- Ack 15s mein na aaye to job `unknown` hoga aur OTP kabhi auto-retry nahi hoga.

## Do cheezein jo abhi verify nahi hui

- WhatsApp ke internal module names pakke nahi hain. Naye chat banane, number exists check, connection state aur ack level ke liye Phase 2 mein ek spike rakha hai. Is spike ke baghair in cheezon pe code nahi likha jayega.
- `context.setOffline` service worker par lagoo hota hai ya nahi, ye maine verify nahi kiya. Isliye Toxiproxy primary hai aur CDP offline emulation uske saath hai.

Plan ke aakhri section mein 7 khuli baatein hain: ek key = ek number, `X-WA-Number` mismatch par reject ya warning, `Idempotency-Key` zaroori ya optional, daily cap, webhook, body purge time, aur sirf text ya templates. Inme se jo aap ko abhi tay karni hon wo bata dein, baqi ke default plan mein likhe hue hain.

Jab aap ready hon to bata dein, Phase 1 se implementation shuru karte hain.

---

**User**

yr plan acha lakin thora easy wording ma thk sa sb explain karo abhi cheza bhot upar upar sa bataye hoe ha 

---

**Cursor**

Phase-wise, ek example ke saath, aam alfaaz mein samjhata hoon. Pehle tasveer, phir har hissa.

---

# 1. Poori baat ek kahani mein

Socho humara backend ek **post office** hai.

- **Vendor** (jaise Ali Boutique) hamara customer hai. Uska apna chatbot hai.
- **Plugin** (Chrome extension) ek **courier** hai. Wo vendor ke WhatsApp ke paas baitha hai.
- **Backend** beech mein hai. Wo pehchanta hai ke courier kis vendor ka hai aur kis ko kya dena hai.

Do tarah ka kaam hota hai:
1. **Customer ne vendor ko message kiya** (Phase 1): courier message backend ko deta hai, backend vendor ke bot se jawab mangta hai, courier jawab WhatsApp pe bhej deta hai.
2. **Vendor ne customer ko khud message bhejna hai**, jaise OTP (Phase 2): vendor backend ko bolta hai, backend sahi courier ko bolta hai, courier WhatsApp pe bhej deta hai.

---

# 2. Teen kirdar: kaun kya jaanta hai

- **Plugin** ko sirf ek cheez pata hai: **API key**. Usay vendor ka bot URL pata nahi.
- **Backend** key dekh kar jaanta hai ke ye Ali Boutique hai, aur Ali ka bot URL kya hai.
- **Vendor ka bot** sirf AI wala kaam karta hai. Text, image, voice samajh kar jawab banata hai.

Isse faida ye hai ke vendor ka bot URL badle to plugin mein kuch nahi badalna. Naya vendor aaye to sirf backend mein ek entry.

---

# 3. API key kya hai

Ye **hotel ke room ki chaabi** jaisi hai. Chaabi dekh kar hotel jaan jata hai ke ye room 305 wala hai.

**Onboarding ka example:**

1. Admin form bharta hai: Business name = Ali Boutique, phone = 923001234567, email = ali@x.com, bot URL = https://bot.ali.com
2. Backend ek lambi random key banata hai: `wab_live_k3f9a1xz_9fA83...`
3. Ye key **sirf ek dafa screen pe dikhti hai**. Admin copy kar ke vendor ko deta hai.
4. Database mein key **seedhi save nahi hoti**. Sirf uska "fingerprint" (hash) save hota hai. Agar koi DB chura le to bhi asli key nahi milti.
5. Vendor ye key apne plugin mein paste karta hai.

**Key kab kaam aati hai:**
- Phase 1 mein plugin ki har request pe: "main kaun hoon".
- Phase 2 mein vendor jab OTP API call kare: "ye vendor kaun hai".

**Agar key leak ho jaye?** Admin panel mein "Revoke" dabate hi us key ka socket kat jata hai aur naye requests 401 dete hain.

**Ek key = ek WhatsApp number.** Agar Ali ke 2 numbers hain to usay 2 keys milengi. Isse "kaunsa plugin sahi hai" wala masla bohot asaan ho jata hai.

---

# 4. Phase 1: customer message karta hai (step by step)

**Example:** Customer Sara ne Ali Boutique ke number pe likha: "Red lehenga available hai?" aur ek photo bheji.

**Step 1: Plugin message pakadta hai.**
WhatsApp Web ke andar plugin chhupa baitha hai. Naya message aate hi usay pata chal jata hai (ye `injected.js` ka kaam hai, jo already bana hua hai).

**Step 2: 7 second ruk kar jama karta hai.**
Customer aksar 3 chhote message ek ke baad ek bhejta hai. Plugin 7 second intezar karta hai taa ke teeno ek saath bot ko jayein, alag alag nahi.

**Step 3: Plugin backend ko bolta hai.**
```
POST https://backend.ourcompany.com/v1/ai-reply
X-API-Key: wab_live_k3f9a1xz_...
X-WA-Number: 923001234567
body: { text, photo (base64), chatHistory... }
```

**Step 4: Backend key check karta hai.**
Key ka fingerprint banata hai, DB mein dhoondta hai. Mila? Vendor active hai? Number match karta hai? (plugin jis WhatsApp number mein login hai wo vendor ke registered number se milna chahiye, warna galat profile mein galat key lag gayi.)

**Step 5: Backend vendor ke bot ko forward karta hai.**
`https://bot.ali.com/ai-reply` pe wahi data bhej deta hai, saath mein ek **signature** (HMAC). Signature ka matlab: "ye request waqai humare backend se aayi hai". Bot team signature check kare to koi aur unke bot ko direct call nahi kar sakta.

**Step 6: Bot jawab deta hai.**
```json
{ "replies": ["Ji, red lehenga available hai.", "Price 85,000 se shuru hota hai."] }
```
Bot ne image khud vision model ko di, voice ho to Groq jaisi service ko. Ye unka kaam hai, humara nahi.

**Step 7: Backend jawab check karke plugin ko deta hai.**
Maximum 5 bubbles, har bubble 4000 characters se kam, sirf text. Ye isliye taa ke bot galat cheez bheje to bhi customer ko ajeeb kuch na jaye.

**Step 8: Plugin WhatsApp pe bhej deta hai.**
Pehle WhatsApp ke internal function se, na chale to screen pe type kar ke send button dabata hai.

## Phase 1 mein cheezein toot jayein to kya hota hai

- Bot band ya slow ho: customer ko **kuch nahi jata**. Hum jaan boojh kar "technical issue" jaisa message nahi bhejte, kyun ke wo customer ko ajeeb lagta hai. Popup mein "Last error" dikhta hai.
- Key galat ho: plugin ruk jata hai, baar baar request nahi karta.
- Ek vendor ka bot bohot slow ho: baaki vendors ko asar nahi padta, har vendor ki apni limit hai.
- Vendor ne bot URL mein `localhost` ya andar ka IP daal diya: backend mana kar deta hai (ye hacker ka tareeqa hota hai, SSRF kehte hain).

## Plugin mein kya hata rahe hain

Dashboard, contacts sync, log-message, aur wo `poll-commands` jo har 1.5 second pe server ko pooch raha tha. Agar ye na hatayein aur URL badal dein to vendor ke server ko spam jayega aur customers ki contact list unke paas chali jayegi. Isliye ye sab delete hoga.

---

# 5. Phase 2: vendor customer ko khud message bhejta hai

**Example:** Ali ki website pe customer ne "Forgot password" dabaya. Ali ko customer ke number 03486102017 pe OTP bhejna hai.

## 5.1 Vendor hamari API call karta hai

```
POST /v1/message-to-customer
X-API-Key: <Ali ki key>
Idempotency-Key: otp-order-9981
{ "number": "03486102017", "message": "OTP 482913 hai, 5 min mein expire", "type": "otp", "ttlSeconds": 300 }
```

Backend ye karta hai:
- Number theek karta hai: `03486102017` -> `923486102017` (shuru ka 0 hata kar 92 lagata hai).
- Foran jawab deta hai: "kaam le liya, job ID ye hai". Ye jawab **bhejne se pehle** nahi aata, sirf **register hone ke baad** aata hai.

**Idempotency-Key kya hai?** Maan lo vendor ka internet atka aur usne wohi request 3 dafa bheji. Is key ki wajah se hum samajh jate hain "ye wohi request hai" aur ek hi OTP jata hai, 3 nahi.

**TTL (300 second):** OTP ki zindagi 5 minute hai. Agar plugin us waqt band tha aur 2 ghante baad chala, to purana OTP **nahi bheja jayega**. Job `expired` ho jati hai.

## 5.2 Job pehle DB mein likhi jati hai

Ye ek **register** (diary) hai. Har job ek row hai: kis vendor ki, kis number ko, kya text, kya status.

Pehle diary mein likhna zaroori hai, kyun ke agar server beech mein crash ho jaye to kam az kam likha hua kaam yaad rahega. Diary hi sach hai.

## 5.3 Celery kya hai aur kyun

Celery ko **restaurant ka waiter** samjho. Order lene wala (API) khana nahi banata, wo sirf parchi kitchen ko deta hai. Kitchen (worker) parchi dekh kar kaam karta hai.

Hamare case mein:
- API job likh kar kehti hai: "Ali ke liye `dispatch_next` chalao".
- Celery worker aata hai, diary dekhta hai, Ali ki sabse zaroori pending job uthata hai, aur plugin ko bhejta hai.

**Ek vendor ke liye ek waqt mein ek hi job** chalti hai. Ye rule code mein "agar yaad rahe" wala nahi, balke **database khud force karta hai** (ek khaas index jo kehta hai: ek vendor ki do job ek saath 'bheji ja rahi' nahi ho sakti). Isse race condition ka masla khatam.

**Safety net:** Maan lo job diary mein likh di lekin Redis (Celery ka dak-khana) usi pal band tha, parchi kitchen tak nahi pahunchi. Is liye Celery Beat har 30 second ek chhoti jaanch karta hai: "koi job diary mein pending hai jis ka koi pata nahi?" Ye normal polling nahi, sirf bachao ka tareeqa hai.

## 5.4 Socket.IO kya hai aur kyun

Pehle sochte hain agar plugin har 2 second pooche: "koi kaam hai?" Ye bekaar hai. Hazaron plugins, har 2 second, server pe bojh.

Socket.IO ek **phone call jo khuli rehti hai**. Plugin ek baar call lagata hai aur line khuli rehti hai. Server ko jab job milti hai wo seedha usi line pe bol deta hai. Plugin ko poochna nahi padta.

**Rooms (WhatsApp groups ki tarah):**
- Ali ka plugin "room: vendor-Ali" mein hai.
- Zara ka plugin "room: vendor-Zara" mein hai.
- Ali ki job sirf Ali ke room mein jati hai. Zara ko kabhi nahi.

**5 Chrome profiles wala sawal** ka seedha jawab: har profile ki apni key hai, apna room hai. Job jis vendor ki hai wo sirf us room mein jati hai. Kaunsa profile ya tab hai, isse farq nahi padta.

**Ek key, ek hi connection.** Agar galti se do profiles mein ek hi key lag gayi to:
- Pehla agar zinda hai (heartbeat taza hai), dusre ko "SESSION_ALREADY_ACTIVE" kehkar mana kar dete hain.
- Pehla agar mar chuka hai (heartbeat band), dusre ko kabool kar lete hain.

**Connect karte waqt check:** plugin bataata hai "main WhatsApp number 923001234567 mein login hoon". Server compare karta hai Ali ke registered number se. Na milne par connection reject. Isse galat number se OTP bhejne ka khatra khatam.

## 5.5 Plugin WhatsApp pe kaise bhejta hai

Plugin job milte hi ye sab karta hai:
1. Check: ye job pehle to nahi bhej chuka? (apni local list mein dekhta hai)
2. Check: WhatsApp theek se connected hai? (agar nahi, to bhejta hi nahi, "abhi nahi" keh deta hai)
3. Server ko batata hai: "main ne is job pe kaam shuru kar diya" (`started`)
4. Number WhatsApp pe hai ya nahi, check karta hai
5. Chat dhoondta hai, nahi mile to banata hai (kyun ke customer ne pehle kabhi message nahi kiya)
6. Message bhejta hai
7. Intezar karta hai ke WhatsApp ka server confirm kare ke "mil gaya"
8. Server ko batata hai: `sent`

**Ye hissa sab se zyada risky hai.** Naye chat banane ka WhatsApp ka andar ka function mujhe pakka nahi pata. Isliye plan mein pehle ek chhota **research task (spike)** hai: asli WhatsApp Web pe check karna ke ye cheez kaise hoti hai. Uske baghair is hisse pe code nahi likhna chahiye.

## 5.6 Job ki zindagi (status)

Ek job ke ye status ho sakte hain, simple alfaaz mein:

- `queued`: diary mein likhi hai, intezar mein hai
- `dispatched`: plugin ko bhej di, abhi plugin ne "shuru" nahi kaha
- `sending`: plugin ne kaha "main bhej raha hoon"
- `sent`: WhatsApp ne qubool kar liya
- `failed`: nakam (jaise number WhatsApp pe nahi hai)
- `expired`: waqt guzar gaya (OTP ke 5 min khatam)
- `unknown`: pata nahi bheja ya nahi (neeche samjhata hoon)
- `cancelled`: vendor ne ya admin ne rok diya

---

# 6. Sab se nazuk masla: OTP do baar na jaye

**Kahani:** Plugin ne OTP WhatsApp pe bhej diya. Lekin result server ko batane se pehle internet chala gaya. Server ne dekha "koi jawab nahi aaya", socha fail hua, dobara bhej diya. Customer ko do OTP mile.

**Hamara bachao, 3 layers mein:**

1. **Plugin ki "outbox":** result pehle plugin ke local storage mein likhta hai. Server se "mil gaya" (ack) milne ke baad hi mitata hai. Internet aate hi bacha hua result dobara bhejta hai. Is se zyada tar kahani yahin khatam ho jati hai.

2. **Do stage:**
   - `started` bheja lekin result nahi aaya aur wait khatam: status `unknown`. **OTP ke liye kabhi auto-retry nahi.** Insaan ya vendor dekhe.
   - `started` tak bhi nahi aaya (plugin ko job mili hi nahi): `queued` mein wapas, dobara bhejna safe hai.

3. **Plugin ki yaad:** agar server galti se wohi job dobara de, plugin kehta hai "ye main bhej chuka hoon" aur dobara nahi bhejta.

---

# 7. "Browser ka internet off ho jaye to?" (timeline ke saath)

**Scene:** 7:00 pm. Plugin online hai. 7:01 pm internet gaya. 7:06 pm wapas aaya.

- **7:01** Socket ki line kat jati hai. Server 15 second mein jaan leta hai (ping jawab nahi aaya). Plugin ka status "Reconnecting" ho jata hai.
- **7:01 se 7:06** Plugin bar bar jodne ki koshish karta hai: 1s, 2s, 4s, 8s... aakhir 30s ke gap par (dheere dheere, taa ke sab plugins ek saath server pe na tut padein).
- **Is dauran 7:03 pm vendor ne OTP API call ki:** job diary mein likhi gayi, lekin plugin offline hai to koi bhejne wala nahi. Job `queued` rahti hai, uski 5 minute ki ghadi chal rahi hai.
- **7:06** Internet aaya. Plugin jud gaya. Server foran check karta hai "is vendor ki kya pending hai?". 7:03 wali job abhi 3 min purani hai (5 min ke andar), to bhej di jati hai.
- **Agar internet 7:10 tak nahi aata:** OTP `expired` ho jata hai, kabhi nahi jata.

**Ek aur dikkat:** Chrome kabhi kabhi extension ka "service worker" so jata hai, aur saath mein socket bhi mar jata hai. Iske liye plugin mein ek alarm lagta hai jo har 30 second pe check karta hai "socket zinda hai?", warna dobara jodta hai. Plus har 20 second ek halka heartbeat bhejna worker ko jagaye rakhta hai.

---

# 8. "WhatsApp ka session disconnect to nahi hoga?"

Pehle ye alag cheezein samajh lo:

**Hamara plugin ka server se connection** (Socket.IO) alag cheez hai, aur **WhatsApp Web ka apna login** alag.

- Agar internet chala gaya: WhatsApp Web **logout nahi hota**. Wo bas "Connecting..." dikhata hai aur internet aate hi khud wapas aa jata hai. Login browser mein save rehta hai.
- Agar phone se "Linked devices" mein is device ko remove kar diya, ya WhatsApp ne account restrict kar diya, ya browser ka data clear ho gaya: tab asli **logout** hota hai, QR code dobara scan karna padta hai.

**Khatra kya hai?** Plugin ko pata hi na ho ke WhatsApp connected nahi hai, aur wo "message bhej diya" samajh le. Ya WhatsApp offline hai, message local queue mein chala jaye aur 2 ghante baad wapas online hone par chala jaye (late OTP).

**Humara hal:**
- Plugin har 20 second bataata hai apni WhatsApp ki halat: `connected`, `connecting`, `offline`, `logged_out`, ya `not_loaded`.
- Backend sirf `connected` plugin ko job deta hai.
- "Sent" ka matlab ab **WhatsApp ke server ne receive kar liya** (ack 1) hoga, sirf ye nahi ke hamara function chal gaya. 15 second mein ack na aaye to `unknown`, retry nahi.
- Popup mein saaf likha aata hai: "WhatsApp logged out, QR check karo".

**Dhyan:** WhatsApp ki halat (connected/offline) detect karne wala internal signal mujhe pakka nahi pata, ye bhi spike mein verify hoga.

**Chrome ka Memory Saver:** Chrome kabhi kabhi purane tabs ko "so" deta hai taa ke memory bache. Agar WhatsApp wala tab so gaya to plugin ka script mar jata hai. Hal: tab ko pin karna aur Memory Saver ki exception daalna. Popup mein ye hint dikhayenge.

---

# 9. Ek ek message kyun, aur ban ka darr

WhatsApp unofficial automation ko pasand nahi karta. Agar ek number se achanak bohot saare messages jayein, to number ban ho sakta hai.

Is liye:
- Ek waqt mein ek hi job.
- OTP ke baad sirf 1 se 2 second ka gap (jaldi chahiye).
- Marketing ke beech 8 se 20 second random gap.
- OTP hamesha marketing se pehle.
- Roz ki limit (jaise 200 marketing messages). Naye number pe aur kam, dheere dheere barhao.

**Seedhi baat:** ye plugin wala tareeqa chhote vendors aur prototype ke liye theek hai. Bari scale pe OTP aur marketing ke liye official **WhatsApp Cloud API** zyada sahi aur safe raasta hai. Ye business ka faisla hai, aur vendors ko bhi batana chahiye.

---

# 10. Test cases simple alfaaz mein

Idea ye hai: har worst case ek **automatic test** ho, taa ke code badalne pe pata chal jaye ke kuch toota ya nahi. Har test mein 3 cheezein check hoti hain:
1. Koi job do baar WhatsApp pe nahi gayi.
2. Job sirf apne vendor ke plugin ko gayi.
3. Koi job hamesha ke liye atki nahi.

**Kis tarah ke tests:**

1. **Chhote tests:** key banana/check karna, number theek karna (`03486102017`, `+92 348 6102017`, `abc`, bohot chhota/bada), galat URL block hona.

2. **Backend ke tests with asli database:** bot 500 de, bot timeout kare, bot galat JSON de, bot khali `replies` de. Vendor suspended ho, key galat ho. Ek hi `Idempotency-Key` se 100 requests ek saath, to sirf ek job bane.

3. **Race tests:** 50 workers ek hi vendor ke liye ek saath job lene ki koshish karein, sirf ek kamyab ho.

4. **Celery ke tests:** Redis band kar ke job daalo, phir Redis wapas, to Beat job ko pakad le. Worker ko beech mein `kill` karo, job na kho, na do baar jaye.

5. **Socket.IO ke tests (nakli plugins):** 5 nakli plugins, 5 vendors, 500 random jobs, har plugin ko sirf apni jobs milen. Revoke key karte hi socket kate.

6. **Internet kaatne ke tests (Toxiproxy):** ye ek tool hai jo plugin aur server ke beech baith kar network ko jaan boojh kar kharab karta hai. Scenarios:
   - Job bhejne ke baad lekin `started` se pehle internet kata
   - `started` ke baad lekin result se pehle kata
   - Result bhej diya lekin server ka ack aane se pehle kata
   - Bina kisi alert ke packets chup chap gir jayein (half-open, sab se ajeeb wala)
   - Har 2 second pe 100 dafa on/off (flapping)
   - Bohot slow internet
   - 10 min tak server unreachable

7. **Plugin ke tests (Playwright):** ek **nakli WhatsApp page** banayenge jo asli WhatsApp jaisa dikhta hai (same internal functions ke naam wale stubs). Phir hum us nakli page ko order de sakte hain: "ab offline ho jao", "ab logout ho jao", "ack kabhi mat bhejo", "send fail kar do". Asli Chrome mein extension load karke dekhenge plugin sahi behave karta hai ya nahi. Service worker ko force kill kar ke bhi dekhenge.

8. **Hypothesis test (random events):** computer khud hazaron random sequences banata hai (connect, disconnect, job aayi, ack aaya, result do baar aaya, worker crash...) aur har step ke baad check karta hai ke upar wale 3 rules toot to nahi gaye. Ye aise bugs pakadta hai jo hum soch hi nahi sakte.

9. **Load test:** 50 vendors, 1000 jobs/min, kya delay aata hai.

10. **Soak test:** staging pe 24 ghante chalao, beech beech mein random internet cut aur worker restart. Aakhir mein script se audit: koi job atki to nahi, koi duplicate to nahi.

11. **Manual checklist** ek asli staging WhatsApp number pe: WiFi band, laptop sleep, phone se linked device hatana, tab reload.

---

# 11. Kaam ka order

**Phase 1 (pehle ye):**
1. Backend ka skeleton (FastAPI, DB, docker)
2. Vendor + key banane ka command line tool
3. `/whoami` aur `/ai-reply` endpoints
4. Plugin ka popup (API key) aur purani cheezein hatana
5. Ek nakli vendor bot aur team ke liye document
6. Phase 1 ke tests

**Phase 2 (jab Phase 1 stable ho):**
7. **WhatsApp spike** (naya chat, connection state, ack ka research)
8. Jobs ka table
9. `/message-to-customer`
10. Socket.IO hub
11. Celery dispatch
12. Plugin ka realtime hissa
13. Admin panel
14. Pura test suite aur soak

---

# 12. Cheezein jo mujhe abhi pakki nahi hain

- WhatsApp Web ke internal function ke naam (naye chat banane ke liye, number check ke liye, connection state ke liye, ack ke liye). Ye update pe badal sakte hain.
- Chrome mein "internet off" test service worker pe sahi lagta hai ya nahi.
- WhatsApp ka ban ka asli risk kitna hai.

Ye teeno cheezein spike aur testing mein verify hongi.

---

Aap bata dein kis hissa ko aur gehrai se chahiye? Jaise sirf Socket.IO aur Celery ka connection, ya Toxiproxy tests ka ek example, ya Phase 1 ka code kaisa dikhega. Agar ab bhi kuch dhundhla lage to us hisse ko main alag se tod kar samjha deta hoon.

---

**User**

1.yr tmna bhot acha sa explain kiya ma chahta ho tm plan ko update karo aur technical chezo ko asa hi sample kar ka sb batana tm


2.yr admin ki ui ka tmna nhi bataya kiya tm fastapi pa hi admin banao ga ?

3.plan ma last ma vendor contract ka bara ma bi detail ma likho ka unko kasa kasa input mila ga aur unko kasa isko handle karna honga aur usma jo template message hota ha uska bara ma bi likhna ka kasa banta ha jasa whatsup ma otp template ya akar message ma hi yes no  button ata ha wo kasa sb detail likha kasa wo signature ko verify kara sb 

4.yr localhost ka link bi ma daal sakta ho q ka abhi ma api banata hi vendor nhi aye ga tu tmhe khud iska test environment bana ka test karna honga tu plan ma wo chez miss lagi jasa abhi pora system bana gaya tu koi developer portal type ka toogle ho jis pa message karna pa mera server koi api call na kara baar haar message ka hardcode jawab da tu asi cheza missing lagi ha yr mujhe in sb ko detail ma sochna honga hum 

imp notes:
plan detail ma ho aur as a senior architect ka toor pa plan review karna aur diagram abhi bi achi baas thora easy kar ka acha sa explain karo

---

**Cursor**

Aap ke chaaron points plan mein daal raha hoon. Pehle ek design sawal pe sochna hai: real WhatsApp mein "buttons" aur "OTP template" ka kya hota hai aur humare plugin se kitna possible hai. Phir plan file poori tarah dobara likhta hoon.

Plan file mein section numbers ke references mein ek ghalti reh gayi (vendor contract ab section 17 hai). Usay theek karta hoon.

Plan update ho gaya: `/home/shakeeb/.cursor/plans/vendor_gateway_two_phase_bad75e66.plan.md`. Section 17 (vendor contract) sab se aakhir mein hai, jaisa aap ne kaha tha.

## Aap ke 4 points ka jawab

**1. Technical cheezein sample ke saath:** har section mein pehle "Simple baat" hai, phir code ya JSON ka namoona. Samples ye hain: API key generate/verify, FastAPI `/ai-reply`, Celery `dispatch_next`, Socket.IO events, plugin outbox, signature verify (Python aur Node), Toxiproxy test, aur Playwright test jo fake WhatsApp ko order deta hai.

**2. Admin:** haan, FastAPI ke andar hi, Jinja2 + HTMX se. Alag React app nahi. Plan mein ye cheezein hain:
- Pages ka diagram aur vendors list ka wireframe.
- Create vendor form, jahan key sirf ek dafa dikhti hai.
- Admin security: argon2 password, CSRF, login lockout, roles, audit log.

**3. Vendor contract (section 17):**
- Bot ko kya input milega: text, image, voice, PDF, sticker, aur bari file jisme `base64Data` null hota hai. Har ek ka JSON sample hai.
- Bot isay kaise handle kare: flowchart aur Python sample.
- Response format aur limits.
- Signature: `HMAC(secret, timestamp + "." + raw_body)`. Python aur Node code hai, replay se bachao hai, aam ghaltiyan likhi hain, aur secret rotation bhi hai.
- Outbound API, webhook, aur vendor go-live checklist.

**Templates aur Yes/No buttons ki asli baat (maine plan mein saaf likhi hai):**
- Asli WhatsApp ke tappable buttons aur OTP "Copy code" official Cloud API ki cheez hain. Wahan Meta pehle template approve karta hai.
- Humara plugin WhatsApp Web chalata hai, jahan real buttons bharosemand tareeqe se possible nahi. Ye mera andaza hai, aur Phase 2 ke spike mein asli WhatsApp pe verify hoga.
- Humara design: template humare backend ki cheez hai (text + variables). "Buttons" numbered text options hain ("Reply 1 for Yes, 2 for No"). Customer "1" likhe to backend usay `matchedOption: confirm` bana kar vendor ke bot ko deta hai, to bot ko text parse nahi karna padta.

**4. Test environment (jo plan mein missing tha):**
- **Vendor ka inbound toggle:** `forward` (asli bot), `canned` (hardcoded jawab, koi API call nahi), `echo`, `mock_bot`, `paused`. Aap ka "har message ka hardcode jawab" wala point yehi hai.
- **Localhost bot URL:** dev mein allowed hai. Prod mein flag on ho to app start hi nahi hoti.
- **Test keys (`wab_test_`):** inse asli WhatsApp message nahi jata. Nakli (virtual) plugin handle karta hai, aur magic numbers hain jaise `...0001` = NOT_ON_WHATSAPP.
- **Test Lab:** bina WhatsApp ke message simulate kar ke bot ki exact request, response aur validation errors dekh sakte hain.
- **Fake WhatsApp page, dev extension build, `make dev-up` aur `make seed`.**

## Architect review mein jo nayi kamiyan pakdi gayi

Section 13 mein 17 points hain. Sab se ahem:
- **Same-device reconnect:** pehle plan mein internet jhapakne par plugin khud ko 45 second tak reject karta rehta. Ab same `deviceId` se aane par foran takeover hota hai.
- **Signature mein replay attack:** pehle timestamp nahi tha. Ab sign ke saath hai.
- **Secrets:** bot/webhook secret hash nahi ho sakte, isliye encrypt hote hain.
- **Webhooks:** vendor ko job ka natija push hona chahiye, sirf GET kaafi nahi.
- **STOP / opt-out:** marketing ke liye, ban risk kam karne ke liye.
- **LID problem:** WhatsApp kuch chats ko phone number ki jagah `@lid` se dikhata hai, to reply context match karna mushkil ho sakta hai. Ye bhi spike mein dekhna hai.

## Abhi tak verify nahi hua

- WhatsApp ke internal function ke naam (naya chat, connection state, ack).
- Real buttons ka na hona.
- `context.setOffline` service worker par lagoo hota hai ya nahi.

Ye teeno Phase 2 ke spike aur testing mein verify honge.

Plan ke section 16 mein 9 sawal hain, jin ke defaults likhe hain. Mere liye sab se zaroori ye hain:
- Marketing messages sirf template se hon ya raw text bhi allowed ho?
- Admin 2FA abhi chahiye ya baad mein?
- Vendors ke liye alag Developer Portal Phase 3 mein theek hai?

Aap jab ready hon to batayein, Phase 1 se implementation shuru karte hain.

---

**User**

ya plan meri current directory ma save karo

---

**Cursor**

Plan ko workspace folder mein ek markdown file ke tor par save kar raha hoon.

Plan aap ki current directory mein save ho gaya:

`/home/shakeeb/Desktop/hnh/complete_backup/hnh/upcomming_projects/whatsup-plugin/VENDOR_GATEWAY_PLAN.md`

Plan mode mein hone ki wajah se maine file copy karne ke bajaye uska content naye markdown file mein likha hai. Chhote farq:
- Cursor wali todos ki frontmatter hata di gayi hai. Uski jagah file ke aakhir mein ek normal checklist hai (`### Todo checklist`).
- Code-file ke links ab plain text mein hain.
- Baaqi sab sections wahi hain (0 se 17, vendor contract sab se aakhir mein).

Cursor wala original plan bhi `~/.cursor/plans/vendor_gateway_two_phase_bad75e66.plan.md` mein maujood hai. Agar aap isko aage badalna chahein to jo file khuli rakhein, bata dein ke kaunsi ko "main" maanna hai, taa ke dono mein farq na aaye.

---

**User**

yr Toxiproxy use q honga isma thora guide karna ?

---

**Cursor**

# Toxiproxy kyun hai, aur kaise use hota hai

## Simple baat

Aap ke plan mein sab se nazuk cheez yeh hai:

> Internet off/on hota hai, connection beech mein kat'ta hai, OTP do baar na jaye.

Agar aap sirf WiFi band kar ke test karein:
- Har baar same nahi hota
- Exact moment control nahi hota (“job send ke baad, result se pehle cut”)
- CI/automatic tests mein WiFi band nahi kar sakte
- Half-open connection (packets chup chap gir jayein) WiFi se easily nahi banata

**Toxiproxy** beech mein baith kar network ko **jaan boojh kar kharab** karta hai — code se on/off, slow, cut, reset. Is liye plan mein hai.

---

## Visual: beech mein baitha hua “tota”

```text
Bina Toxiproxy:
  Plugin / Virtual Plugin  ----------->  Backend (localhost:8000)

Toxiproxy ke saath:
  Plugin / Virtual Plugin  --->  Toxiproxy(:8666)  --->  Backend(:8000)
                                      ↑
                               yahan se aap:
                               - connection cut
                               - delay
                               - bandwidth kam
                               - packets drop
                               - TCP reset
```

Tests Toxiproxy ke API se bolte hain: “ab 5 second cut karo”, “ab 2s latency laga do”.

---

## Plan mein kis liye zaroori hai

Aap ke 3 invariants:

1. OTP do baar na jaye  
2. Job sirf sahi vendor ko jaye  
3. Job atki na rahe  

In mein se (1) aur (3) **network cut ke timing** pe toot sakte hain. Example:

| Moment jab cut hota hai | Kya khatra hai | Toxiproxy se test |
|---|---|---|
| Job emit ke baad, `started` se pehle | Server sochta hai fail, dobara bhej de | Cut yahin |
| `started` ke baad, `result` se pehle | OTP bhej diya, result kho gaya → duplicate? | Cut yahin |
| Result ke baad, server ack se pehle | Outbox dobara bheje — server idempotent? | Cut yahin |
| Flapping (har 2s on/off) | Reconnect storm, duplicate, crash | Loop mein disable/enable |

WiFi se aap “`started` ke 50ms baad cut” nahi kar sakte. Toxiproxy se kar sakte ho.

---

## Toxiproxy kya cheez hai

- Open-source tool (Shopify ka), Go mein likha
- Ek **proxy** chalata hai jo traffic forward karta hai
- Us proxy pe **toxics** lagate ho: latency, bandwidth, timeout, reset_peer, etc.
- Control API (HTTP) se toxic add/remove — tests se call

Important: yeh **kya nahi** hai:
- Yeh WhatsApp / browser ka replacement nahi
- Yeh load test tool nahi (k6/Locust alag)
- Yeh sirf **network behavior** simulate karta hai

---

## Dev stack mein kaise baithta hai (aap ke plan ke mutabiq)

```text
docker compose:
  - backend:8000
  - toxiproxy:8474 (API) + 8666 (proxy listen)
  - redis, postgres, ...
```

Config idea:

```text
# Toxiproxy proxy:
#   name: backend
#   listen: 0.0.0.0:8666
#   upstream: backend:8000
```

Tests / virtual plugin backend ko **seedha `:8000` pe nahi**, balke **`:8666` (Toxiproxy)** pe hit karte hain.

Jab aap `toxiproxy.disable("backend")` karte ho → jaise internet gaya.  
`enable` → jaise wapas aaya.

---

## Test ka sample (plan jaisa, thora clear)

```python
async def test_cut_after_started_before_result(env):
    # 1. Plugin ready, lekin result abhi hold
    plugin = await env.virtual_plugin("A", hold_result=True)

    # 2. OTP job daalo
    job = await env.api.post_job("A", type="otp", number="03486102017")

    # 3. Plugin ne "started" bhej diya — yahan cut
    await plugin.wait_for("job_ack_started")
    await env.toxiproxy.disable("backend")   # internet off

    # 4. Plugin locally "sent" samajh kar result ready kare
    await plugin.release_result("sent")

    # 5. Internet wapas
    await asyncio.sleep(5)
    await env.toxiproxy.enable("backend")

    # 6. Invariants
    await eventually(lambda: env.job(job.id).status == "sent")
    assert plugin.send_count(job.id) == 1   # OTP do baar nahi gaya
```

Yeh wi-fi band karke nahi, **exact stage pe** cut karta hai.

---

## Common toxics (aap ke chaos scenarios)

| Toxic | Simple matlab | Kab use |
|---|---|---|
| `disable` proxy | Poora connection band | Internet off |
| `latency` | Har packet slow | Slow network |
| `bandwidth` | Download/upload limit | Bari media / slow link |
| `timeout` | Connection hang | Half-open jaisa feel |
| `reset_peer` | TCP RST | Beech mein abrupt cut |
| `slicer` | Packets tukre tukre | Weird network |

Plan ke 10 scenarios inhi se cover hote hain.

---

## Kab Toxiproxy, kab nahi

**Toxiproxy use karo:**
- Backend ↔ virtual plugin / test client network
- Celery / Redis path pe chaos (agar unke liye alag proxy)
- Automatic CI chaos tests

**Toxiproxy se nahi / alag tareeqa:**
- **Asli Chrome extension + real WhatsApp:** browser ka internet Toxiproxy se seedha control mushkil. Wahan Playwright + CDP network offline / extension config jo Toxiproxy URL use kare.
- **WhatsApp session logout:** yeh network nahi, WA state — fake WhatsApp page / spike se test
- **Bot slow/500:** Toxiproxy ki zaroorat nahi — `respx` / mock bot kaafi

Plan mein is liye **dono** hain: Toxiproxy (network) + fake WhatsApp (WA state) + respx (bot HTTP).

---

## Setup ka rough flow (jab implement hoga)

1. `docker-compose` mein Toxiproxy service  
2. Proxy banao: listen `8666` → upstream `api:8000`  
3. Tests `GATEWAY_URL=http://toxiproxy:8666` use karein  
4. Helper: `disable()`, `enable()`, `add_latency(ms)`, `reset_peer()`  
5. Har chaos test ke baad proxy clean state pe wapas  

---

## Ek line summary

**Toxiproxy = lab mein “internet kharab karne wala switch”**, taa ke OTP duplicate, outbox, lease expire, reconnect — yeh sab **automatic, repeatable** test ho, WiFi band karne pe depend na ho.

Agar chaho to next mein sirf yeh bata sakta hoon: Phase 1 mein Toxiproxy zaroori hai ya pehle Phase 2 (outbound Socket.IO) pe hi lagana better hai.

---

**User**

yani ya dev purpose ka liya ha baas taka huma proper test kar saka aur ya traffic jo server tk ja raha ha uska middle ma ha taka request ko zada delay packet drop wagera kar saka 

---

**Cursor**

Haan, bilkul sahi samjhe.

**Toxiproxy sirf dev / test / CI ke liye hai** — production mein customers ke beech nahi lagta.

Kaam yeh hai:
- Traffic **plugin/test client → Toxiproxy → aapka server**
- Beech mein aap control kar sakte ho: delay, cut, packet drop, slow bandwidth, TCP reset
- Is se “internet kharab” jaisi situations **repeatable** test hoti hain

Production mein plugins seedha aap ke real backend pe jate hain, Toxiproxy ke baghair.

---

**User**

1.-- ek vendor ki ek hi job ek waqt mein "bheji ja rahi" ho sakti hai


is point ko thora easy kar ka batana yr agr 1 vendor 100 job bhaja let suppose kuch marketing template ha aur kuch otp wagera tu tu 99 drop hojae ga ?

---

**Cursor**

Nahi — **99 drop nahi hongi.**

## Simple matlab

“Ek vendor ki ek hi job ek waqt mein **bheji ja rahi** ho sakti hai” ka matlab:

- Ek waqt mein **sirf 1** job WhatsApp pe bhejne ke process mein hogi (`dispatched` / `sending`)
- Baaki **99 diary (`queued`) mein intezar** karengi
- Jab pehli `sent` / `failed` / `unknown` ho jaye, **agli** queue se uthayegi

Drop = job delete / refuse. Yahan aisa nahi.

---

## Example: 100 jobs ek saath

Vendor ne 1 OTP + 99 marketing API pe bhej diye:

```text
Job 1  OTP         → abhi bhej rahi (sending)
Job 2  marketing   → queued (waiting)
Job 3  marketing   → queued
...
Job 100 marketing  → queued
```

Phir:

```text
Job 1 sent
  ↓
Job 2 bhejni shuru (OTP pehle priority se aati agar pending ho)
  ↓
Job 3 ...
```

Sab 100 eventually process hongi (TTL / daily cap / rate limit alag baat hai).

---

## Kyun yeh rule?

Agar 100 ek saath WhatsApp pe daud jayein:
- Ban ka risk
- Duplicate / race
- Plugin overload

Is liye: **queue mein kitni bhi rakh sakte ho**, **ek waqt mein bhejna ek**.

---

## Kab “reject” hota hai (drop jaisa feel)

Woh alag rules hain, is index se nahi:

| Case | Kya hota hai |
|---|---|
| Queue bohot bhari (e.g. 1000+ pending) | naya request `503 QUEUE_FULL` |
| Marketing daily cap khatam | `429` |
| OTP ka TTL guzar gaya | job `expired` (purana OTP nahi jata) |
| Customer ne STOP kiya | marketing `422` |

**Inflight unique index = serial send, queue discard nahi.**

---

**User**

1.key check vendor check mode check


is ka matlab batao ka agr mujhe apna local host pa plugin ko test karna ha tu ma kasa karonga yr zara thk sa batao ga mujhe tm iska mujhe WhatsApp pa reply hi nhi mila ga tu ma test kasa karonga ?

---

**Cursor**

# “key check / vendor check / mode check” ka matlab

Backend jab plugin se `/ai-reply` leti hai, teen sawal poochti hai:

| Check | Sawal | Fail hone par |
|---|---|---|
| **key check** | `X-API-Key` sahi hai? | 401 — plugin reply nahi bhejta |
| **vendor check** | Vendor active hai? Number match? | 403 — pause |
| **mode check** | Is vendor ka `inbound_mode` kya hai? | Mode ke hisab se aage |

**Mode** hi decide karta hai ke asli bot call hogi ya test wala raasta:

```text
forward   → asal vendor bot (localhost ya live URL)
canned    → hardcoded jawab, koi bot call nahi
echo      → jo likha wahi wapas
mock_bot  → hamara nakli bot
paused    → chup (replies: [])
```

---

# Localhost pe plugin kaise test karoge

Do alag cheezein hain. Confuse yahi se hota hai.

## A) WhatsApp pe **asli reply** chahiye (plugin E2E)

Yeh path:

```text
WhatsApp → Plugin → localhost:8000 (backend) → mode → jawab → Plugin → WhatsApp
```

**Setup (seedha):**

1. Backend local chalao (`make dev-up` / FastAPI)
2. Seed se demo vendor banao, **API key** copy karo  
3. Extension **dev build** load karo (`GATEWAY = http://localhost:8000`)
4. Popup mein woh API key paste + Save → “Connected as …”
5. Admin mein us vendor ka mode set karo:

### Pehli dafa (bina bot ke) — **sab se aasan**

`inbound_mode = canned` (ya `echo`)

- Customer (apna doosra number) vendor number pe “price” likhe  
- Backend **kisi bot ko call nahi karta**  
- Hardcoded / echo jawab plugin ko milta hai  
- Plugin WhatsApp pe bhej deta hai  

Is se confirm hota hai: plugin + key + localhost backend + WhatsApp send — sab theek.

### Phir apna bot localhost pe

`inbound_mode = forward`  
`bot_base_url = http://localhost:9000` (dev mein private URL allowed)

- Ab backend aap ke local bot ko call karega  
- Bot `{ "replies": ["..."] }` de → WhatsApp pe aayega  

Agar bot down / galat JSON → **WhatsApp pe kuch nahi** (by design). Popup mein “Last error” dekho.

---

## B) WhatsApp pe reply **na** aaye to bhi test kaise?

Plan mein is liye **Test Lab** hai — WhatsApp ke baghair:

| Tareeqa | Kya hota hai | WhatsApp reply? |
|---|---|---|
| Admin → **Simulate incoming** | Form se text/image bhejo, backend wahi `/ai-reply` pipeline chalaata hai | Nahi — screen pe result dikhta hai |
| `POST /v1/sandbox/simulate-incoming` (test key) | Vendor apna bot test kare | Nahi |
| `canned` / `echo` + asli plugin | Reply WhatsApp pe aati hai, bot ki zaroorat nahi | Haan |
| Playwright + **fake WhatsApp** | Automatic tests | Fake page pe |

Matlab: reply WhatsApp pe na aaye to bhi aap dekh sakte ho:
- bot ko kya request gayi  
- signature theek thi ya nahi  
- bot ne kya return kiya  
- validation fail kyun hui  

---

# Practical order (aap ke liye)

1. **Backend + key + plugin** → mode `echo` ya `canned` → doosre phone se message → WhatsApp pe jawab aana chahiye.  
2. Apna bot `localhost:9000` pe chalao → mode `forward` → phir WhatsApp pe AI jawab.  
3. Bot break karke dekho: WhatsApp silent + popup error (yeh bhi sahi behavior hai).  
4. Bina WhatsApp: Admin Test Lab se simulate.

---

# Short jawab aap ke sawal ka

> “Agar WhatsApp pe reply na mile to test kaise?”

- Pehle **`canned`/`echo`** se confirm karo ke plugin + gateway theek hain (reply zaroor aani chahiye).  
- Bot wala masla alag hai — Test Lab / logs se bot test karo.  
- Production design: bot fail pe customer ko automatic “error” message nahi jata; isliye debug ke liye popup + Test Lab zaroori hain.

Agar chaho to next message mein sirf “Day-1 localhost checklist” (commands + kis screen pe kya set) 5 steps mein likh doon.

---

**User**

X-Device-Id ma jis no ko message bhajna ha wo honga na ?

---

**Cursor**

Nahi. **`X-Device-Id` recipient number nahi hai.**

## Teen alag cheezein

| Header / field | Kya hai | Example |
|---|---|---|
| **`X-Device-Id`** | Is Chrome / plugin install ki random ID (UUID). Pehchan: kaunsa device reconnect ho raha hai | `5d2e-a91c-...` |
| **`X-WA-Number`** | Jis WhatsApp number pe **plugin login** hai (vendor ka number) | `923001234567` |
| **Jis ko message bhejna hai** | Outbound job / API body ka `number` / `to` | `03486102017` → customer |

---

## Example

Ali Boutique ka number `923001234567` pe plugin chal raha hai. Customer Sara ko OTP:

- `X-Device-Id` = plugin ka device uuid (har install pe ek dafa banta hai)
- `X-WA-Number` = `923001234567` (Ali ka WA)
- Job body: `"number": "03486102017"` (Sara — **yahan** jisko bhejna hai)

`X-Device-Id` is liye hai ke same key se do Chrome profiles na juden / reconnect pe takeover ho sake — number bhejne ke liye nahi.

---

**User**

X-Device-Id ban ka backend pa bi save hota ha na aur ya local storage sa user change tu nhi kar sakta ha ?

---

**Cursor**

Haan — idea yeh hai, lekin security ka hisab alag samajhna.

## `X-Device-Id` kahan rehta hai

**Plugin side**
- Install / pehli run pe random UUID banta hai
- `chrome.storage.local` mein save
- Har request / Socket connect pe header/auth mein jata hai

**Backend side (Phase 2 especially)**
- `devices` table: `device_id`, `vendor_id`, `wa_number`, `last_seen_at`…
- Redis presence: session claim / same-device takeover ke liye

Matlab backend ko pata rehta hai ke is vendor se kaunsa device online hai.

---

## User UI se change kar sakta hai?

**Normal popup se nahi** — koi “Device ID edit” field plan mein nahi.

Lekin **technically haan**: jo Chrome DevTools / storage jaanta ho, `chrome.storage.local` edit kar sakta hai. Yeh browser extension storage hai, server-side secret nahi.

Isliye:
- `X-Device-Id` = **pehchan / session helper** (ek key pe ek session, reconnect takeover)
- Asli security = **`X-API-Key`** (hash DB mein, revoke, etc.)

Agar koi device ID badal de:
- Backend naya / alag device samajh sakta hai
- Pehli session zinda ho to `SESSION_ALREADY_ACTIVE` ya takeover rules lag sakte hain
- Key choraye baghair yeh “hack” se vendor ban nahi jata

---

## Short

| Sawal | Jawab |
|---|---|
| Backend pe save? | Haan (devices + presence) |
| User UI se change? | Nahi |
| Storage edit se change possible? | Haan — is liye isay API key jaisa secret mat samjho |

**Rule of thumb:** key = kaun vendor; device id = kaunsa Chrome install connected hai.

---

**User**

X-Request-Id kiya ha aur ya plugin khud generate karaga is ka advantage kiya ha ?

---

**Cursor**

# `X-Request-Id` kya hai

Har `/ai-reply` (ya similar) request ka **unique ticket number** — jaise parcel tracking ID.

Example: `8f2c1a7e-4b21-4c9a-9f01-...` (UUID)

Plan ke mutabiq **plugin khud generate** karta hai aur header + body dono mein bhejta hai.

---

## Advantage (kyun zaroori)

### 1. Duplicate / retry se bachna (sab se bara faida)
Network atke, plugin sochta hai fail hua, **wahi request dobara** bhej de.

Agar `requestId` same ho:
- Backend / vendor bot pehle wala jawab wapas de sakte hain  
- AI dobara call nahi → **customer ko 2 baar same reply** nahi  

Bina iske har retry naya “naya message” lagta hai.

### 2. Logs mein dhundna asaan
Admin / Test Lab / bot logs: “is ID wali request kahan fail hui?”  
Latency, signature, bot 500 — sab ek ID se jod sakte ho.

### 3. Vendor bot ke liye idempotency
Contract mein: bot `requestId` cache kare; same ID aaye to **cached replies** de.

### 4. Backend ↔ bot tracing
Gateway bot ko bhi wohi `X-Request-Id` forward karta hai — ek chain, ek ID.

---

## Short example

```text
1st call:  X-Request-Id: abc-111  → bot AI chalaya → replies ["Price 85k"]
Retry:     X-Request-Id: abc-111  → bot cache → wahi ["Price 85k"]  (AI dubara nahi)
Naya msg:  X-Request-Id: abc-222  → naya AI call
```

---

## Plugin vs outbound `Idempotency-Key`

| | `X-Request-Id` | `Idempotency-Key` |
|---|---|---|
| Kaun banata hai | **Plugin** (inbound) | **Vendor backend** (outbound OTP etc.) |
| Kis pe | `/ai-reply` | `/message-to-customer` |
| Faida | Duplicate auto-reply rokna | Duplicate OTP rokna |

**Ek line:** `X-Request-Id` = is request ki pehchan; plugin banata hai taa ke retry / logs / bot cache safely kaam karein.

---

**User**

tu ya cache redis ma kitna daar rahta ha ? aur plan ma ya ha na  implementation ma backend ma ?

---

**Cursor**

Plan mein exact baat confirm karta hoon — `requestId` cache kahan likhi hai aur TTL di hai ya nahi.

Short clear jawab:

## Plan mein kya likha hai

**`X-Request-Id` ka reply-cache pehle vendor bot ki zimmedari hai**, hamare gateway Redis ki detail TTL ke saath nahi likhi.

Contract (section 17) mein:

```text
agar requestId pehle dekha → purana jawab wapas
warna AI chalao → store_cached_reply(requestId, replies)
```

Hamara backend Redis plan mein yeh ke liye hai:
- Celery broker  
- Socket.IO  
- presence (`EX 45` seconds — yeh alag cheez hai)  
- locks / rate limit  

Inbound `/ai-reply` pe plan kehta hai: **body store mat karo** (pass-through). `request_logs` mein sirf `request_id` + status + latency (reply text nahi).

Outbound alag hai: `Idempotency-Key` → **Postgres** (`unique vendor_id + idempotency_key`), Redis TTL nahi.

---

## “Redis mein kitna der?” — plan mein number nahi

Is liye seedha: **plan ne inbound `requestId` cache ke liye Redis TTL fix nahi ki.**

Agar implement karte waqt (bot ya optional gateway) Redis use karo, practical default:

| Jagah | Suggested TTL | Wajah |
|---|---|---|
| Vendor bot cache | **5–15 min** (jaise **10 min**) | Retry isi window mein hota hai; signature tolerance bhi ~5 min |
| Gateway agar khud cache kare (optional, plan mein required nahi) | Same **5–15 min** | Privacy: replies lambi der na rakho |

OTP/outbound = DB job row, “kitne der Redis” nahi.

---

## Implementation mein backend pe hai?

| Cheez | Backend (hamara) | Vendor bot |
|---|---|---|
| `X-Request-Id` accept / forward / logs | Haan | — |
| Same `requestId` pe AI skip + cached replies | Plan: **bot pe** | Haan (contract) |
| Redis pe yeh cache + TTL | Plan mein **mandatory nahi** | Bot team decide (Redis/memory/DB) |
| Outbound idempotency | Haan — **Postgres** | Vendor `Idempotency-Key` bheje |

---

**Ek line:** Haan idea plan mein hai (duplicate `requestId` → wahi jawab), lekin yeh mainly **vendor bot cache** hai; hamare gateway pe Redis TTL (jaise 10 min) abhi explicitly fix nahi — implement pe decide karna hoga. Agar chaho ke gateway khud bhi 10 min Redis cache kare, wo plan extension hoga (Agent mode mein likh sakte ho).

---

**User**

yani huma baas vendor ko recomendation dani ha ya hamra pass iplement nhi hongi hum simple vendor ka api call kar ka response la raha ha asa hi na agr vendor na implementation ki tu bhot acha uski cost bacha gi warna is sa hamari logic ma koi farak nhi parha ga asa hi na ?

---

**Cursor**

Haan — bilkul sahi.

## Inbound `requestId` cache

| | |
|---|---|
| Hamara kaam | Plugin → gateway → vendor `/ai-reply` → jawab wapas → plugin WhatsApp pe |
| Cache Redis/DB | **Zaroori nahi** — plan mein yeh **vendor recommendation** hai |
| Vendor ne implement kiya | Dobara AI call nahi → **unki cost bachti hai**, same jawab |
| Vendor ne nahi kiya | Hamari core logic **same** — call, response, forward |

Matlab: bina vendor cache ke bhi system chalega.

## Chhoti nuance (farq “logic” ka nahi, “risk” ka)

Agar plugin retry kare aur vendor cache na kare:
- Vendor pe **2 AI calls** (un ka paisa)
- Kabhi **2 alag replies** aa sakte hain  

Hamara flow phir bhi “jo response aaya wo bhejo” hi hai. Is liye recommend karte hain, force nahi.

## Alag mat milao

Outbound **`Idempotency-Key`** — yeh **hamari** implementation hai (Postgres), taa ke OTP do baar queue na ho. Yeh `requestId` recommendation se alag hai.

**Ek line:** Haan — `requestId` cache vendor ke liye tip (cost/safety); hum simple proxy: call → response. Unke baghair bhi hamari main logic nahi toot’ti.

---

**User**

Ek vendor ka slow bot doosron ko block na kare -> per-vendor concurrency cap + circuit breaker.


isko thora explain karo

---

**Cursor**

# Problem kya hai

Maan lo ek hi FastAPI process pe kai vendors hain.

Ali ka bot bohot slow hai (har request 25–30s). Agar 50 customers ek saath Ali ko message karein:

- 50 requests **Ali ke bot** ka intezar karti rehti hain  
- Server ke workers / connections unhi pe atak jate hain  
- Zara / dusre vendors ki tez requests bhi **line mein khadi** — unka bot theek hone ke bawajood reply late / timeout  

Yahi “ek slow bot doosron ko block kar raha hai.”

---

# Hal 1: Per-vendor concurrency cap

**Simple:** Har vendor ke liye limit — “ek waqt mein max N bot calls.”

Example: Ali ke liye max **5** parallel calls.

```text
Request 1–5  → bot ko gayin
Request 6+   → wait / 429 / fast fail (cap full)
Zara         → apni alag 5 slots — Ali se asar nahi
```

Ali slow ho to sirf **uski** queue bharti hai. Baaki vendors ki capacity free rehti hai.

Redis / in-memory semaphore: `vendor:{id}` pe counter.

---

# Hal 2: Circuit breaker

**Simple:** Agar Ali ka bot baar baar fail / timeout kare, to thori der **usay call hi mat karo**.

Jaise ghar ka breaker: short-circuit detect → power kaat → baad mein try.

```text
CLOSED  (normal)     → calls ja rahi hain
OPEN    (toota)      → 30–60s seedha fail, bot ko tang nahi
HALF    (test)       → 1–2 trial calls; theek → CLOSED, fail → OPEN
```

Faida:
- Slow/dead bot pe **endless wait** nahi  
- Unke server pe spam kam  
- Hamara process free  
- Dusre vendors unaffected  

Plugin ko is dauran 502/504 jaisa mil sakta hai → customer ko silent (plan ke mutabiq), popup “Last error”.

---

# Dono mil kar

| | Concurrency cap | Circuit breaker |
|---|---|---|
| Kab | Bohot traffic / slow responses | Lagatar errors / timeouts |
| Kya rokta hai | Ek vendor saari threads na kha jaye | Dead bot pe baar baar chipak na jao |
| Scope | Per vendor | Per vendor |

```text
Zara bot: 200ms  → normal
Ali bot:  30s + errors → max 5 concurrent, phir breaker OPEN
```

---

**Ek line:** Cap = ek vendor ki parallel limit; breaker = kharab bot ko thori der ignore. Dono is liye ke **Ali ka masla Zara ki service na kharab kare**.

---

**User**

plan ma kn sa implement hoa wa ha ?

---

**Cursor**

Dono **plan mein likhe hue hain** — abhi codebase mein banay hue nahi (plan document hai, implementation baad mein).

## Plan mein kya hai

| Feature | Plan mein? | Kahan |
|---|---|---|
| **Per-vendor concurrency cap** | Haan | Phase 1 worst cases + `/ai-reply` tests (“slow bot → concurrency cap”) |
| **Circuit breaker** | Haan | Wahi + tests (“bot down → circuit breaker open/close”) |
| Todo / order | “`/v1/ai-reply` … **limits**” | Phase 1 step 3 |

Matlab implement **karna hai** Phase 1 ke `/ai-reply` proxy ke andar (Redis semaphore / counter + fail-count pe breaker). Exact number (jaise max 5 concurrent, 30s open) plan ne detail mein fix nahi kiye — design rule + test cases hain.

## Alag “cap” mat milao

**Marketing daily cap (200/day)** — yeh outbound messages ki limit hai, bot concurrency/circuit se alag.

---

**Short:** Plan mein **dono** inbound bot-call ke liye hain. Abhi sirf plan; code tab jab Phase 1 backend likho.

---

**User**

aur ya concurracny ki value admin pannel sa ati ha kiya aur agr vendor ka chatbot pa messages araha ha let suppose 100 per minute aur hum concurrency 50 set kiya tu ya 50 message drop honga ? 

---

**Cursor**

Do alag sawal hain — short clear jawab:

## 1) Concurrency value admin panel se aati hai?

**Plan mein abhi clearly nahi** — yeh zyada **system default** (config/env) jaisi cheez hai, jaise max 5 ya 10 parallel bot calls per vendor.

Admin panel pe jo explicitly hai:
- inbound mode, outbound mode  
- **marketing daily cap** (alag cheez)  

Concurrency ko baad mein vendor detail pe “Max parallel bot calls” bana sakte ho, lekin current plan mein wo form field fix nahi.

---

## 2) 100 messages/min, concurrency 50 → 50 drop?

**Pehle confusion door karo:**  
Concurrency **“per minute 50”** nahi hai.  
Concurrency = **ek waqt mein kitni bot calls chal rahi hain** (parallel).

### Example A — bot tez (1s jawab)
100 messages **ek minute** mein aayein, lekin ek saath max ~2–3:

```text
Concurrency 50 → sab handle ho sakti hain
Drop ≈ 0
```

### Example B — 100 **ek dum** aayein, bot slow (30s)
Cap = 50:

```text
50  → bot pe in-flight
baqi 50 → wait nahi rakhte lambi der / 429 ya fast fail
```

Inbound auto-reply mein un 50 ka **WhatsApp pe jawab nahi** jayega (plugin silent).  
Yeh outbound jobs jaisi queue nahi jahan baad mein bhej dein — customer ka message real-time hai, late reply aksar bekaar.

Is liye “drop” ka feel: **haan, un requests ka reply nahi** — lekin diary se 50 jobs delete nahi; bas us waqt bot call reject / limit.

---

## Cap vs daily marketing cap

| | Concurrency | Marketing daily cap |
|---|---|---|
| Matlab | Parallel bot calls | Roz max marketing sends |
| 100 aayein | Parallel limit | Roz ki quota |
| Admin (plan) | Default / optional | Haan (200 default) |

---

**Ek line:** Concurrency abhi plan mein system limit hai (admin field zaroori nahi); 50 ka matlab “ek minute mein 50” nahi — “ek saath 50”. Zyada parallel pressure pe baqi requests fail/silent ho sakti hain, queued OTP jaisi baad mein nahi.

---

**User**

1.7.4 Fake WhatsApp page (extension ke automatic tests ke liye)



ya banao ga tm kiya aur iska advantage kiya ha ?

---

**Cursor**

# Haan — plan mein yeh banana hai

**Fake WhatsApp page** automatic tests (Playwright) ke liye banega. Asli `web.whatsapp.com` nahi — ek **nakli page** jo WhatsApp jaisi cheezein dikhata / behave karta hai, taa ke extension test ho sake.

Kab: Phase 1 tests / Phase 2 plugin tests ke saath (plan section 7.4 + 14.9).

---

## Yeh kya hota hai

Asli WhatsApp Web ke andar plugin `window.require('WAWeb...')` use karti hai. Fake page un functions ke **stubs** deta hai:

```text
fake.deliverIncoming(...)     → naya message aaya
fake.setConnState('offline')  → WA disconnect
fake.setAckDelay('never')     → send ke baad ack nahi
fake.rejectSend()             → send fail
```

Real Chrome + aap ki extension load → tests in controls se situations banate hain.

---

## Advantage (kyun zaroori)

| Bina fake page | Fake page ke saath |
|---|---|
| Har test pe asli WhatsApp login / QR | Login nahi chahiye |
| CI mein mushkil / unstable | CI mein automatic chal sakta hai |
| Ban / account risk | Zero WhatsApp risk |
| “Internet off” / “ack nahi aaya” control nahi | Exact moment pe state change |
| Slow / flaky | Repeatable, fast |

**Asli faide:**
1. **Plugin logic test** (burst, send queue, outbox, waState, dedupe) bina real chat ke  
2. **Worst cases** jo live pe dangrous hain (worker kill, offline mid-job, duplicate OTP path)  
3. **5 profiles routing** fake pages pe verify  
4. Release se pehle confidence — staging pe thora real WA, roz ka regression fake pe  

---

## Kya yeh asal WhatsApp replace karta hai?

**Nahi.** Fake = automated safety net.  
Release se pehle plan mein **manual checklist** asli number pe bhi hai (WiFi off, linked device remove, etc.).

---

**Ek line:** Haan, tests ke liye hum banayenge — nakli WhatsApp taa ke extension ke tough cases CI mein safe aur dobara-dobara chal saken, bina real account ke.

---

**User**

yani ma khud bi is pa test kar sakta ho ya sirf automatic testing ka liya ha aur is sa kisi no wali testing bi mock hongi ?

---

**Cursor**

# Short jawab

**Fake WhatsApp page** pehle **automatic tests (Playwright/CI)** ke liye design hai.  
Aap khud bhi browser mein khol kar dekh / try kar sakte ho **agar** uske saath chhota control UI / console API ho — lekin yeh **asli number pe message nahi bhejta**.

---

## 1) Aap khud test kar sakte ho?

| | |
|---|---|
| **Automatic** | Haan — asal maqsad yehi |
| **Manual (developer)** | Possible — page open + `fake.deliverIncoming(...)` / buttons |  
| **Asli customer number pe WA** | **Nahi** — yeh mock hai |

Matlab: aap is page pe plugin ka behavior dekh sakte ho (message aaya, reply “send” hui, offline, etc.).  
Doosre phone pe tick / notification **nahi** aayegi.

Asli number wali testing:
- Asli WhatsApp Web + aap ka number, **ya**
- Plan ka **Test Lab / simulate-incoming** (backend/bot), **ya**
- Staging pe real WA checklist  

---

## 2) Number wali testing bhi mock?

**Haan — is fake page pe number bhi mock hi hai.**

- `to: 92348...` → fake chat list / fake send log  
- `NOT_ON_WHATSAPP` → stub kehti hai “number nahi”  
- Koi real WhatsApp / Meta network call nahi  

Is liye:
- OTP “asli SIM pe aaya?” → fake page se **prove nahi** hota  
- “Plugin ne sahi number pe send function call ki?” → fake pe **haan**, verify ho sakta hai  

---

## Easy map

```text
Fake WhatsApp page  →  plugin + extension logic (mock numbers)
Test Lab / canned   →  backend + bot (bina WA)
Asli WA Web         →  real number pe real message
```

**Ek line:** Fake page primarily auto-tests + optional manual mock playground; real number delivery yahan se nahi — uske liye asli WhatsApp (ya staging number) chahiye.

---

**User**


1.plan ma jo Outbound vendor call ha uska structure simple rakho baas { "number": "03486102017", "message": "Aap ka OTP 482913 hai.", "type": "otp", "ttlSeconds": 300 } aur yaha type aur ttlsecond ki kiya need ha ?

---

**Cursor**

# Simple structure — theek hai

Outbound ko itna rakhna plan ke saath fit baithta hai:

```json
{
  "number": "03486102017",
  "message": "Aap ka OTP 482913 hai.",
  "type": "otp",
  "ttlSeconds": 300
}
```

Templates / variables baad mein add ho sakte hain; Phase 2 ke shuru mein yeh kaafi hai.

---

## `type` ki need

Ye **sirf label nahi** — backend is se decide karta hai:

| `type` | Practical faida |
|---|---|
| `otp` | **Priority sab se upar** (marketing se pehle), **fail pe auto-retry nahi** (double OTP khatra), aksar chhota TTL |
| `transactional` | Normal priority (order confirm, etc.) |
| `marketing` | **Neeche priority**, **zyada gap** (8–20s), **daily cap**, STOP/opt-out |

Agar `type` na ho to har message same treat hoga — OTP marketing ke peeche atak sakta hai, ya fail OTP dubara chala jaye.

**Default agar optional rakho:** `transactional` — lekin OTP bhejte waqt `otp` dena strongly recommend.

---

## `ttlSeconds` ki need

**TTL = is message ki zindagi.**

Example OTP: `ttlSeconds: 300` (5 min).

- Plugin 5 min offline → job **`expired`**, purana OTP **kabhi nahi** jata  
- Bina TTL: 2 ghante baad plugin online → late OTP customer ko confuse / security issue  

Marketing pe TTL bada (ya default 24h) theek; OTP pe chhota zaroori.

**Default agar optional:**  
- `otp` → 300  
- baaki → e.g. 86400  

Phir bhi body mein bhejna clear rakhta hai.

---

## Minimal aur bhi?

Bohot simple:

```json
{ "number": "03486102017", "message": "..." }
```

Phir backend defaults: `type=transactional`, `ttlSeconds=...`  
OTP ke liye phir bhi `type` (+ ideally `ttlSeconds`) dena best practice.

`Idempotency-Key` header alag rakhna OTP ke liye — body field nahi, lekin duplicate OTP rokne ke liye important.

---

**Ek line:** Structure simple rakhna theek; `type` = queue/priority/retry/cap rules; `ttlSeconds` = “itni der baad yeh message mat bhejna” (OTP ke liye critical).

---

**User**

yani ttl bi vendor ka hath ma ha na ttlSeconds agr wo bhaja tu acha ha ?

---

**Cursor**

Haan — **`ttlSeconds` vendor ke haath mein hai.**

- Vendor bheje → hum usi ke mutabiq job expire karte hain  
- Na bheje → hum **default** laga dete hain (`otp` ≈ 300, baaki zyada)

**Acha practice:** OTP pe hamesha bhejo, jaise `300` ya `600`, jo unke OTP expire se match kare.

Chhoti safety (humari taraf): bohot chhota (jaise 5s) ya bohot bara (mahine) reject / clamp kar sakte hain taa ke galat value se system na kharab ho — lekin choice pehle vendor ki hi hai.

---

**User**

8.7 Duplicate OTP kaise rokte hain (teen layer)



is section ko thk sa explain karo

---

**Cursor**

# 8.7 Duplicate OTP — teen layer (easy)

## Pehle masla (bina protection)

```text
1. Server → Plugin: "OTP 482913 Sara ko bhejo" (job_7)
2. Plugin → WhatsApp: bhej diya ✓
3. Plugin → Server: "sent" batane se PEHLE internet kat gaya
4. Server: "koi jawab nahi... shayad fail"
5. Server → Plugin: wahi job_7 dobara
6. Plugin → WhatsApp: OTP DOBARA ✗  (customer confuse / security)
```

Teen layers isi story ko rokne ke liye hain.

---

## Layer 1 — Plugin ki **outbox** (result kho na jaye)

**Idea:** Pehle apne paas likho, phir server ko bolo. Server “mil gaya” kahe tab hi mitao.

```text
WhatsApp pe bheja
    ↓
chrome.storage mein likho: job_7 = sent   ← outbox
    ↓
Server ko job_result bhejo
    ↓
Server Socket.IO ack: { ok: true }
    ↓
Outbox se mitao
```

Agar step 3 ke baad internet gaya:
- Outbox mein `sent` pada rehta hai  
- Internet aate hi plugin **dobara sirf result report** karta hai  
- WhatsApp pe **dobara send nahi**  

Yeh layer kehti hai: “main bhej chuka, server ko yaad dilata rahun.”

---

## Layer 2 — **Do stage** (`started` vs final result)

Job ki zindagi do hisson mein:

| Stage | Matlab | Server agar result na mile |
|---|---|---|
| Sirf job di, **`started` nahi** | Plugin tak pohanchi hi nahi / bheja nahi | Wapas `queued` → **dobara bhejna SAFE** |
| **`started` aa gaya** | Plugin ne kaha “main bhej raha hoon” (shayad WA pe ja chuka) | Status **`unknown`** → OTP pe **auto-retry NAHI** |

```text
dispatched  --started-->  sending  --sent/fail-->  terminal
     |                       |
  30s khamoshi            120s khamoshi
     ↓                       ↓
  queued (retry OK)      unknown (OTP: insan/vendor dekhe, auto dobara nahi)
```

**Kyun:** `started` ke baad retry = “shayad pehla OTP already chala gaya” → duplicate ka khatra. Is liye OTP yahan ruk jati hai.

---

## Layer 3 — Plugin ki **yaad** (jobId dedupe)

Plugin pichle ~**7 din** ke `jobId` + unka nateeja local rakhta hai.

Agar server galti se `job_7` dobara de:

```text
Plugin: "job_7 main pehle bhej chuka (sent)"
         → WhatsApp pe mat bhejo
         → Server ko wahi purana result wapas batao
```

Yeh last safety net hai jab Layer 1–2 ke bawajood duplicate job aa jaye.

---

## Teen layers ek picture mein

```text
                    OTP job
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
   Layer 2: stages              Layer 3: jobId yaad
   started? → retry ya unknown   pehle bheji? → skip send
         │
         ▼
   Layer 1: outbox
   result pehle local, phir server; reconnect pe resend RESULT not message
```

---

## Vendor API wali alag hifazat (8.7 ke bahar, lekin related)

Header **`Idempotency-Key`**: vendor ne same OTP request 3 dafa API hit ki → **ek hi job** banti hai.  
Woh “teen layer” se pehle ka gate hai — duplicate **job create** rokna.  
8.7 = job banne ke **baad** network/plugin wale duplicate send rokna.

---

**Ek line:** Outbox = result kho na jaye; stages = “shuru ho chuki” OTP ko andha retry na do; jobId yaad = same job do baar WhatsApp pe na jaye.

---

**User**

yr ak baat batao ya outbound job sirf otp ka case ma ha ya message ka case ma bi normal wala ?

---

**Cursor**

**Dono — sirf OTP nahi.**

Outbound job system **har us message** ke liye hai jo vendor API se bhejta hai jab customer ne pehle message nahi kiya / hum unhein push karna chahte hain.

| `type` | Example | Same job pipeline? |
|---|---|---|
| `otp` | OTP code | Haan |
| `transactional` | Order confirm, delivery update | Haan |
| `marketing` | Offer / promo | Haan |

Sab `outbound_jobs` table → Celery → Socket.IO → plugin → WhatsApp.

Farq sirf **rules** ka hai:
- OTP: priority high, chhota TTL, fail pe auto-retry nahi, duplicate se zyada hifazat  
- Transactional: beech ki priority  
- Marketing: slow pacing, daily cap, STOP/opt-out  

**Inbound** alag hai: customer ne pehle message kiya → `/ai-reply` → bot jawab.

**Ek line:** Outbound = vendor → customer (OTP + normal + marketing); OTP usi system ka ek special case hai.

---

**User**

8.10 Pacing aur ban ka risk
Ek waqt mein ek job (DB index).
OTP ke baad 1-2s gap, marketing ke beech random 8-20s.
OTP hamesha marketing se pehle (priority DESC).
Daily cap per vendor (default 200 marketing/day, naye number pe kam).
Vendor ko clearly batao: unofficial automation se ban ka risk hai. Bari scale pe OTP/marketing ke liye official WhatsApp Cloud API behtar hai (section 13, point 9: channel abstraction).





ya nhi aya ha samj yr bilkol bi isko thk sa explain karo

---

**Cursor**

# 8.10 Pacing aur ban risk — bilkul simple

## Pehle “ban risk” kya hai

Aap **official WhatsApp Business API** use nahi kar rahe.  
Plugin WhatsApp Web pe baith kar messages bhej raha hai — yeh Meta ke rules ke against / grey area hai.

Agar ek number se:
- bohot tez  
- bohot zyada  
- bohot marketing  

messages jayein, WhatsApp soch sakta hai yeh spam bot hai → **number restrict / ban**.

Is liye hum **jaan boojh kar dheere** bhejte hain. Isi ko **pacing** kehte hain.

---

## 1) Ek waqt mein ek job

Ek vendor ke liye ek saath 50 messages WhatsApp pe nahi daurte.

```text
Job1 bhejo → complete → Job2 → complete → Job3 ...
```

Jaise ek counter pe ek customer. Line lambi ho sakti hai, lekin ek saath bheed nahi.

---

## 2) Gap (thora rukna)

WhatsApp pe human jaisa thora pause:

| Type | Gap | Kyun |
|---|---|---|
| **OTP** ke baad | **1–2 second** | Jaldi chahiye, lekin blast nahi |
| **Marketing** ke beech | **random 8–20 second** | Spam pattern kam; hamesha exact 10s = bot jaisa lagta hai, is liye random |

**Example:** 10 marketing messages  
≈ har message ke baad ~10–15s → total ~2–3 minute, 2 second mein 10 nahi.

---

## 3) OTP hamesha marketing se pehle

Queue mein mix ho:

```text
queued:  marketing, marketing, OTP, marketing
```

Uthane ka order **priority** se:

```text
1st → OTP          (priority 9)
phir → transactional
phir → marketing   (priority 0)
```

**Kyun:** Customer OTP wait kar raha hai. Marketing pehle bhej kar OTP 5 minute late = bekaar + gussa.  
OTP time-sensitive hai, promo nahi.

---

## 4) Daily cap (roz ki limit)

Har vendor: default **~200 marketing / din**.

201st marketing → API `429` / reject.

Naye number pe **aur kam** (warm-up): pehle 20–50/din, baad mein barhao — taake naya number achanak blast na kare.

**OTP / transactional** is 200 mein usually alag / soft treat (plan mein marketing cap focus hai) — lekin phir bhi andha flood mat.

---

## 5) Vendor ko clear batao

Contract / docs mein likhna:

> Yeh unofficial automation hai. Ban ka risk aap ke number pe ho sakta hai.  
> Bari scale OTP/marketing ke liye **official WhatsApp Cloud API** better hai.

Hum channel abstraction is liye sochte hain ke kal Cloud API jod saken — aaj plugin, kal official.

---

## Ek din ki kahani

```text
10:00  OTP × 3     → jaldi, 1–2s gap, pehle
10:05  Marketing 50 → har ~10s ek, queue se
...
Shaam tak marketing 200 → agla kal
Ek aur marketing API → 429 "limit khatam"
```

Plugin offline ho to jobs wait / TTL — lekin jab online ho, **phir bhi ek-ek karke pace** ke saath.

---

## Short cheatsheet

| Rule | Seedha matlab |
|---|---|
| 1 job at a time | Blast nahi |
| OTP gap 1–2s | Tez lekin sane |
| Marketing 8–20s random | Spam kam |
| OTP pehle | Important pehle |
| 200/day marketing | Roz ki ceiling |
| Ban warning | Vendor aware rahe |

**Ek line:** Pacing = WhatsApp ko “insaan / normal business” jaisa dikhana; warna tez automation se number ban ho sakta hai — is liye dheere, priority ke saath, roz limit ke saath bhejte hain.

---

**User**

 Daily cap wagera ki limit control admin paneel sa hongi per vendor q ka mujhe nhi dikha plan ma ya kahi bi

---

**Cursor**

Haan — **plan mein hai**, bas chhoti line mein, is liye miss ho sakta hai.

## Plan mein kahan likha hai

1. **Database (vendors table):**  
   `marketing_daily_cap int default 200`  
   → har vendor ki apni value

2. **Admin → Vendor detail:**  
   > Modes: Inbound dropdown, Outbound (real_plugin/paused), **daily cap**.

Matlab: admin vendor khole → uski daily marketing limit set/change kar sake (default 200).

---

## Practical behavior

| | |
|---|---|
| Control | **Admin panel, per vendor** |
| Default | 200 marketing/day |
| Naya / risky number | Admin kam kar de (jaise 20–50) |
| Cap full | Us vendor ki nayi marketing → `429` |

OTP/transactional is field se alag treat (plan marketing cap pe focus karta hai).

---

**Short:** Haan, daily cap admin se per-vendor; DB column + vendor detail pe “daily cap” — detail wireframe nahi, lekin intent clear hai. Agar chaho to plan mein ek clear form field (“Marketing daily cap: [ 200 ]”) likhwa sakte ho Agent mode mein.

---

**User**

agr ma kisi vendor ka liya per day 200 message ka cap rakho wo kasa honga ?

---

**Cursor**

# Per vendor 200/day cap — kaise chalega

## 1) Admin set karta hai

Vendor detail pe:

```text
Marketing daily cap: [ 200 ]
```

DB mein `vendors.marketing_daily_cap = 200`.

---

## 2) Din bhar count kaise badhta hai

Jab vendor call kare:

```text
POST /message-to-customer
{ "number": "...", "message": "...", "type": "marketing" }
```

Backend check:

```text
Aaj is vendor ki kitni marketing jobs SENT (ya queued/accepted) ho chuki?
Agar count >= 200 → 429 "daily limit reached"
Warna → job banao, count +1
```

**OTP / transactional** is 200 mein usually **nahi** gin’te (warna OTP band ho jaye). Cap = **marketing** pe.

---

## 3) Example (Ali Boutique, cap 200)

```text
Subah 9:00   marketing #1    → OK (1/200)
...
Shaam 6:00   marketing #200  → OK (200/200)
Shaam 6:05   marketing #201  → 429  "kal try karo / limit barhao"
Shaam 6:10   OTP             → OK   (cap se bahar)
```

---

## 4) “Din” kab reset

Midnight (timezone decide: e.g. `Asia/Karachi`) pe counter 0.

Ya “last 24 rolling hours” — plan simple **calendar day** better (samajhna aasan).

Count kaise rakhein (implementation):
- Redis: `marketing:vendor:{id}:2026-10-31` → INCR, TTL next midnight  
- ya DB: daily counter table  

---

## 5) Vendor ko kya dikhe

Response jab limit lagi:

```json
{
  "error": { "code": "DAILY_CAP_EXCEEDED", "message": "Marketing limit 200/day reached" }
}
```
HTTP **429**, optionally `Retry-After` (kal tak seconds).

Admin panel pe: “Aaj 187 / 200 used”.

---

## 6) Aap ka control

| Cap | Matlab |
|---|---|
| 200 | Normal |
| 50 | Naya / cautious number |
| 0 | Marketing band (sirf OTP/transactional) |

---

**Ek line:** Admin 200 set kare → har marketing send pe counter +1 → 200 ke baad usi din ki marketing block, OTP chal sakti, agle din counter reset.

---

**User**

agr ma kisi specfic vendor A KO 500 AUR vendor B ko 50 dana chahta tu da sakta ho aur yr limit db sa check hongi redis sa ya bi batao

---

**Cursor**

Haan — **bilkul de sakte ho.**

## Per vendor alag limit

| Vendor | Admin set | DB field |
|---|---|---|
| Vendor A | **500** | `marketing_daily_cap = 500` |
| Vendor B | **50** | `marketing_daily_cap = 50` |

Har vendor ki apni row, apni value. Default 200 sirf tab jab set na kiya ho.

---

## Check DB se ya Redis se?

**Dono mil kar (best practice):**

| Cheez | Kahan | Kyun |
|---|---|---|
| **Limit kitni hai** (500 / 50) | **Postgres** (`vendors.marketing_daily_cap`) | Admin yahi change karta hai, permanent |
| **Aaj kitni use hui** (counter) | **Redis** (tez INCR) | Har request pe DB UPDATE heavy |

Flow:

```text
1. Vendor B marketing bheje
2. DB/cache se lim = 50
3. Redis: INCR marketing:B:2026-10-31
4. Agar count > 50 → 429
   Warna job banao
```

Redis key example: `marketing_count:{vendor_id}:{date}` + midnight pe expire.

---

## Agar sirf DB?

Possible (jobs table pe `COUNT` aaj ki marketing) — theek lekin zyada load. Plan/speed ke liye **limit DB, counter Redis** better.

Redis wipe ho jaye to safety: DB se aaj ki count dubara bhar lo (reconcile).

---

**Ek line:** Haan A=500, B=50 alag; **cap value DB**, **aaj ka hisaab Redis** (tez), fail pe `429`.

---

**User**

aur message kitna tarika ka ha outbound jasa otp marketing normal aur in ma kitna daar ka delay ha aur deleay actual dalta kasa ha mujhe ya batao

---

**Cursor**

# Outbound message types + delay kaise lagti hai

## Kitne tarike

Plan mein **3 types**:

| `type` | Simple naam | Example |
|---|---|---|
| `otp` | OTP | Login / forgot password code |
| `transactional` | Normal / business | Order confirm, delivery, invoice |
| `marketing` | Promo | Offer, sale, newsletter |

API mein:

```json
{ "number": "0348...", "message": "...", "type": "otp" }
```

`type` na bhejo to default aksar **`transactional`** (normal).

---

## Kitna delay (pacing)

Yeh **do messages ke beech** ka rukna hai (ek vendor pe, ek ke baad ek):

| Type | Beech ka gap | Kyun |
|---|---|---|
| **OTP** ke baad | **1–2 second** | Jaldi, lekin blast nahi |
| **Transactional** ke baad | **1–2 second** (OTP jaisa / thora soft) | Normal business |
| **Marketing** ke beech | **random 8–20 second** | Spam / ban risk kam |

**Priority alag cheez hai:** queue mein OTP pehle uth'ti hai, phir transactional, phir marketing — chahe marketing pehle API pe aa gayi ho.

---

## Delay **actual** kaise padti hai

Plugin / WhatsApp pe `sleep(10)` loop nahi.  
**Celery** kehti hai: “agli job itni der baad chalao.”

```text
1. Job A complete (sent / failed / unknown)
2. Backend dekhe: agali job ka type kya hai?
3. Gap decide:
     OTP/transactional → ~1–2s
     marketing        → random 8..20s
4. Celery: dispatch_next.apply_async(..., countdown=gap)
5. Gap ke baad Worker agli job uthata hai → plugin ko bhejta hai
```

Redis mein bhi “is vendor ke liye next allowed time” rakh sakte ho taake do workers ek saath na bhej dein.

**Important:**  
- Delay = **bhejne ke beech**  
- Vendor ki API call **foran 202** deti hai (job queue mein) — vendor 8–20s wait nahi karta  
- Messages queue mein padti rehti hain, **ek-ek karke** pace se nikal'ti hain  

---

## Chhota example

Vendor ne ek saath bheja: marketing, marketing, OTP

```text
T+0s     OTP bheji (priority)
T+1–2s   pehli marketing
T+10–18s dusri marketing  (random 8–20s gap)
```

---

**Ek line:** 3 types — otp / transactional (normal) / marketing; delay Celery `countdown` se job ke baad lagti hai (OTP/normal ~1–2s, marketing ~8–20s random); vendor API turant accept karti hai, bhejna dheere hota hai.

---

**User**

tmna plan ma kiya finalize kiya ha wo batao mujhe 

---

**Cursor**

# Plan mein jo finalize hai (summary)

## Badi picture
- **Ek FastAPI backend**: gateway + Socket.IO + Celery + admin (Jinja2/HTMX) + Test Lab  
- **Plugin** sirf **X-API-Key** rakhta hai; bot URL plugin ko nahi pata  
- **Ek key = ek WhatsApp number = ek Chrome profile**  
- Purana Node `server/` = sample vendor bot; naya kaam `backend/` mein  

---

## Phase 1 (inbound — customer message → AI reply)
- Plugin → `POST /v1/ai-reply` + headers (`X-API-Key`, `X-Device-Id`, `X-WA-Number`, `X-Request-Id`)  
- Backend: key → vendor → **inbound_mode** → bot forward (signed) → `replies[]` → plugin WhatsApp pe  
- Modes: `forward` / `canned` / `echo` / `mock_bot` / `paused`  
- Dashboard / log / contacts poll **hata dena**  
- Popup: API key + whoami status  
- Per-vendor **concurrency + circuit breaker** (system limit)  
- Dev: localhost bot URL allowed; prod mein nahi  

---

## Phase 2 (outbound — vendor → customer)
- `POST /v1/message-to-customer`  
- Aap ne simple body finalize ki direction:  

```json
{ "number": "03486102017", "message": "...", "type": "otp", "ttlSeconds": 300 }
```

- Types: **`otp` | `transactional` | `marketing`**  
- **TTL** vendor bhej sakta hai; na bheje to defaults  
- Header **`Idempotency-Key`** (OTP duplicate jobs rokne ke liye)  
- Flow: DB job → Celery → Socket.IO → sahi vendor plugin → WhatsApp  
- **1 vendor = 1 in-flight job**; baaki queue  
- Pacing: OTP/normal ~**1–2s**; marketing ~**8–20s random**  
- OTP priority sab se upar  
- **Daily marketing cap** per vendor, **admin se** (default 200; A=500, B=50 alag) — limit DB, counter Redis  
- Duplicate OTP: outbox + started/unknown + jobId dedupe  
- Templates / Yes-No text options plan mein hain; **real WA buttons nahi** (Cloud API baad mein)  

---

## Admin
- FastAPI ke andar (alag React nahi)  
- Vendor create → live + test key **ek dafa**  
- Modes, daily cap, keys rotate/revoke, jobs, Test Lab  

---

## Test / safety
- Test keys (`wab_test_`), magic numbers, virtual plugin  
- Toxiproxy = sirf **dev/CI** network chaos  
- Fake WhatsApp page = **auto (+ optional manual mock)**; real number delivery nahi  
- Vendor contract: signature HMAC, media types, go-live checklist  

---

## Clear business rules
- Unofficial plugin = **ban risk**; bari scale pe official Cloud API better  
- Bot fail → customer ko auto error message nahi; popup / Test Lab se debug  
- `requestId` cache = **vendor recommendation** (cost); hamari core logic proxy  

---

## Abhi code nahi — plan document
File: `VENDOR_GATEWAY_PLAN.md`  
Implementation todos Phase 1 → 1B → Phase 2 spike → outbound → tests  

---

## Section 16 — abhi “confirm” wale defaults (plan ke sawal)
Agar aap ne explicitly override nahi kiya, plan yeh assume karta hai:
- 1 key = 1 number  
- OTP pe Idempotency-Key zaroori (recommended)  
- Marketing default cap 200  
- Outbound pehle simple text; templates Phase 2 feature  
- Body purge ~24h  
- Developer Portal Phase 3  

Agar kisi default ko badalna ho (jaise marketing raw text band), bata dena — Agent mode mein plan update ho sakta hai.

---

**User**

yr mera kahtna ka matlab ka outbound message tmna kiya finalize kiya outbound message ka delay ko laka per vendor wo in simple words batao mujhe yr

---

**Cursor**

# Outbound — simple finalize

## Message kya hai
Vendor API se customer ko **seedha message** (OTP, normal, marketing).  
Har message ek **job** ban kar queue mein jati hai, phir **us vendor ke plugin** se WhatsApp pe jati hai.

## Delay kya finalize hai
**Har vendor alag queue** — Vendor A ka delay Vendor B ko asar nahi karta.

Us **ek vendor** ke andar:

| Type | Beech mein rukna |
|---|---|
| OTP | ~1–2 second |
| Normal (transactional) | ~1–2 second |
| Marketing | ~8–20 second (random) |

- Ek waqt mein **us vendor ki 1 hi** message bhejti hai  
- OTP **pehle**, marketing **baad mein**  
- Roz ki marketing limit **admin per vendor** (jaise A=500, B=50)

## Simple example (sirf Vendor A)
```text
OTP → (1–2s) → normal → (1–2s) → marketing → (10s) → marketing → ...
```
Vendor B apni alag line pe chal raha hota hai.

**Ek line:** Outbound = vendor→customer jobs; delay per vendor, type ke hisab se (OTP/normal tez, marketing dheemi).

---

**User**

yani celery job pick baad sleep karta ha 1--2 sec asa hi na aur ya per vendor ka ha ya agr multiple vendor tu case ma kasa pick honga ?

---

**Cursor**

# Simple jawab

## Sleep job pick ke baad?

**Bilkul `sleep(2)` worker ke andar nahi** (plan ke mutabiq).

Asal flow:

```text
1. Job pick → plugin ko bhejo → sent/fail
2. Phir agla kaam schedule: "is vendor ke liye 1–2s (ya 8–20s) BAAD dobara dekho"
3. Celery countdown = wait BAHAR, worker free rehta hai
```

Matlab rukna **agli job se pehle** hai, pick kar ke thread block karke sone se better.

---

## Per vendor hai?

**Haan — har vendor ki apni line.**

- Vendor A: abhi marketing bhej raha, 12s baad agli  
- Vendor B: usi waqt OTP bhej sakta hai  

A ka delay B ko nahi rokta.

---

## Multiple vendors — pick kaise?

```text
Vendor A queue:  jobA1, jobA2, jobA3
Vendor B queue:  jobB1, jobB2
```

- `dispatch_next(A)` → sirf A ki top job (priority: OTP pehle)  
- `dispatch_next(B)` → sirf B ki top job  
- Ek vendor pe **ek waqt mein ek** in-flight  
- Kai vendors **parallel** chal sakte hain (alag workers / alag tasks)

```text
Same second:
  A → OTP bhej rahi
  B → marketing bhej raha
  C → idle (queue khali)
```

---

**Ek line:** Rukna per-vendor, agli job se pehle (Celery countdown), pick ke baad worker so nahi jata; multiple vendors ek saath apni-apni job bhej sakte hain.

---

**User**

mujhe thora aur acha sa batao yr ka celery ka ka let supppose ak worker jo quee sa job pick karta ha tu celery ko kasa pata chalga is vendor ki abhi job process hoe ha ab 2 sec baad aur agr 10 vendor tu kiya honga mujhe plan ko dakh ka jawab dana khud sa kuch invent nhi karna ha 

---

**Cursor**

# Plan ke mutabiq — Celery ko kaise pata (invent nahi)

## Sab se zaroori baat

Plan mein task ka naam hai: **`dispatch_next(vendor_id)`**  
Har dafa argument mein **ek vendor id** jati hai.  
Celery “global queue se koi bhi job” nahi uthati — **usi vendor** ke liye dekhti hai.

Worker andar **`sleep(2)` nahi** karta. Plan clearly kehta hai: gap ke liye **`countdown`** se **wahi task baad mein** schedule.

---

## Ek vendor — step by step (plan wala code)

```text
dispatch_next("VendorA") chalata hai:
  1. Plugin online + WA connected? nahi → return (job queued rehti)
  2. peek_next_job(VendorA) — is vendor ki top job
  3. pace_wait_seconds(VendorA, priority) → wait > 0?
        HAAN → dispatch_next("VendorA") ko countdown=wait pe schedule karo
               abhi job claim MAT karo, return "paced"
        NAHI → claim job → plugin ko emit
```

**2 sec ka matlab plan mein:**  
Abhi bhejne se pehle agar pace kehta hai “abhi mat”, to worker so nahi jata — woh **`dispatch_next(VendorA)` 2 sec baad dubara queue mein daal deta hai** (`apply_async(..., countdown=wait)`).

Job complete hone ke baad plan ke sequence mein:

```text
plugin → job_result
backend → phir se dispatch_next after gap
```

Yani agli pick bhi **usi `vendor_id`** ke saath, gap ke baad.

Is liye Celery ko “yaad” is se hoti hai:
- task ke args mein **`vendor_id`**
- pacing function **`pace_wait_seconds(vendor_id, ...)`**
- agla schedule bhi **`dispatch_next(usi vendor_id)`**

---

## 10 vendors — plan ke hisab se kya hoga

Har vendor **apni alag chain**:

```text
dispatch_next(V1) ... countdown ... dispatch_next(V1) ...
dispatch_next(V2) ... countdown ... dispatch_next(V2) ...
...
dispatch_next(V10) ...
```

- V1 ki 2 sec wait **sirf V1** ki agli job rok'ti hai  
- V2 usi waqt apni job bhej sakta hai  
- DB rule: **ek vendor pe ek in-flight** (`one_inflight_per_vendor`)  
- 10 vendors → theory mein **10 parallel** in-flight (har ek ka ek)

Nayi job aaye to API: `enqueue dispatch_next` **usi vendor** ke liye.  
Plugin connect pe bhi: `dispatch_next.delay(ctx.vendor_id)` — phir usi vendor ki catch-up.

Beat har 30s: jin vendors ki queued jobs hain aur koi in-flight nahi → unke liye `dispatch_next` dobara (signal kho jaye to).

---

## Galat picture vs plan

| Galat samajh | Plan |
|---|---|
| Worker job pick karke 2s so jata hai | `sleep` nahi; `countdown` se task baad mein |
| Ek queue, 10 vendors mil kar wait | Har task **`vendor_id` scoped** |
| Ek worker sab rok de | Dusre vendors ke `dispatch_next` alag chalte hain |

---

**Ek line (sirf plan):** Celery ko vendor id task ke saath milti hai; pace se pehle wait calculate hota hai; wait ho to **usi vendor** ka `dispatch_next` `countdown` se baad mein lagta hai; 10 vendors = 10 alag `dispatch_next(vendor_id)` lines, ek doosre ki 2 sec pe atki nahi.

---

**User**

nhi aya bilol easy kar ka batao 

---

**Cursor**

# Bilkul easy

Socho **10 vendors = 10 alag lines** (Bank A, Bank B, …).  
Har line ka apna ticket number: `VendorA`, `VendorB`.

---

## Celery kya karti hai

Ek chhota kaam bar bar:

> “**Is vendor** ki koi pending message hai? Agar haan aur wait khatam ho gaya, to **ek** bhejo.”

Kaam ka naam plan mein: `dispatch_next(VendorA)`  
Andar likha hota hai **kaunsa vendor** — bhool’ti nahi.

---

## 2 second kaise

Worker message uthake so **nahi** jata.

Agar abhi bhejna jaldi hai (pace):

```text
Abhi mat bhejo.
Clock pe alarm: 2 second baad
  “VendorA ke liye dobara check karo”
```

2 sec baad **wahi VendorA** wala kaam phir chalta hai, phir message jata hai.

---

## 10 vendors

```text
VendorA line:  msg → (2s) → msg → (10s) → msg
VendorB line:  msg → (2s) → msg
VendorC line:  ...
```

- A rukka hua hai 2 sec → **B rukta nahi**  
- Har vendor ki wait **uski apni**  

Jaise 10 counters — ek counter pe customer slow hai, doosra counter chal’ta rehta hai.

---

## Ek picture

```text
API: “VendorA ko OTP bhejo”  →  VendorA ki diary mein likho
                                 →  “VendorA check karo” task

API: “VendorB ko promo”      →  VendorB ki diary
                                 →  “VendorB check karo” task   (alag)
```

---

**Ek line:** Har vendor ki alag line; 2 sec usi line ka alarm hai; doosre vendors ko farq nahi.

---

**User**

tu ya time redis ma maintain hota ha haar vendor ka according ka uski next job kab chalagi

---

**Cursor**

# Short jawab (sirf plan ke hisab se)

**Asal “agli kab chalegi” plan mein pehle Celery ke `countdown` se hai — har dafa usi `vendor_id` ke saath.**

```text
Abhi mat bhejo
  → dispatch_next(VendorA) ko 2 sec baad schedule karo
```

Matlab clock / alarm **usi vendor** ke task pe lagi hoti hai.  
Redis plan mein **Celery broker** hai, is liye yeh scheduled task broker (Redis) ke through rehti hai — lekin plan ne alag se yeh nahi likha ke “hum `pace:vendor:A = 7:05:02` jaisi key khud likhte hain.”

---

## Plan mein clearly kya hai

| Cheez | Plan |
|---|---|
| Agli check kab | `dispatch_next(vendor_id)` + **`countdown=wait`** |
| Wait kaise | `pace_wait_seconds(vendor_id, priority)` |
| Redis explicitly | broker, Socket.IO, **presence**, locks, **rate limit** |
| Job pe time field | DB: `available_at` (job kab uthne layak) |

---

## Aap ke sawal ka seedha jawab

> “Har vendor ka next job time Redis mein maintain?”

- **Idea level:** haan, wait **per vendor** hai.  
- **Plan ki likhi hui mechanism:** next run = **Celery schedule (`countdown`)** us `vendor_id` ke liye (broker Redis ho to wohin rehti hai).  
- **Alag Redis key “next_at”** daily-cap / presence jaisi **explicit detail pace ke liye current plan text mein nahi** — sirf `pace_wait_seconds(...)` function.

Implementation pe `pace_wait_seconds` andar Redis se “last sent / next allowed” padh sakti hai, lekin woh plan ne step-by-step fix nahi ki; jo fix hai woh **countdown + vendor_id** hai.

**Ek line:** Haan per vendor; time primarily Celery `countdown` se; Redis broker hai, alag pace-key plan mein roz-cap jaisi clear nahi.

---

**User**

yr plan ma tmna jo job pick ka beach ma time ha jasa normal message ma 1 sa 2 sec aur marketing message ma 10 sec sa above yr ya thk clear plan ma mention nhi ha celery job kasa pick karaga per vendor usko kasa pata chala ga job time ka cap isko thk sa mention karo

---

**Cursor**

Ask mode mein plan file edit nahi kar sakta. Neeche woh **clear section** hai jo plan mein add/replace hona chahiye. Agent mode on karo to main `VENDOR_GATEWAY_PLAN.md` mein yeh daal dunga.

---

## Plan mein yeh clearly likhna chahiye (copy-ready)

### Outbound pacing — kaise pick, kaise wait (per vendor)

**Rule (finalize):**
- Har vendor ki **apni alag line** hai.
- Task hamesha: `dispatch_next(vendor_id)` — sirf usi vendor ki jobs.
- Ek vendor pe **ek waqt mein ek hi** job in-flight (DB unique index).
- Worker **`sleep()` nahi** karta.

**Gaps (finalize):**

| Message type | Do jobs ke beech minimum gap |
|---|---|
| `otp` | 1–2 seconds |
| `transactional` (normal) | 1–2 seconds |
| `marketing` | random **8–20 seconds** (target ~10s+) |

**Celery ko time kaise pata chalta hai (do cheezein):**

1. **Redis pace key (per vendor)** — last send ke baad “next allowed at”:
   - Key: `pace:vendor:{vendor_id}`
   - Value: unix timestamp jab agli job allow hai
   - Job successfully claim/send cycle ke baad update:
     - otp/transactional → `now + 1..2s`
     - marketing → `now + random(8,20)s`

2. **Celery `countdown`** — agar abhi `now < next_allowed`:
   - Job abhi **claim mat karo**
   - `dispatch_next(vendor_id)` ko `countdown = next_allowed - now` se schedule karo
   - 2s/10s baad **wahi vendor** dubara pick try karega

**Pick order (usi vendor ke andar):**
- DB: `WHERE vendor_id=? AND status='queued' AND available_at <= now() ... ORDER BY priority DESC, created_at`
- Priority: otp (9) > transactional (5) > marketing (0)

**10 vendors:**
- `dispatch_next(A)`, `dispatch_next(B)`, … alag-alag
- A ki 10s marketing wait B ko nahi rok’ti

**Flow (simple):**

```text
job_result (Vendor A) complete
  → Redis: pace:vendor:A = now + gap(type)
  → enqueue dispatch_next(A)

dispatch_next(A) chala:
  → Redis se next_allowed padho
  → agar wait bachi → countdown se phir A
  → warna A ki top queued job claim → plugin ko bhejo
```

**Daily cap alag hai:** marketing 200/day admin per vendor — yeh pacing gap nahi, roz ki limit hai.

---

Agent mode mein bolo **“plan update karo pacing clear kar do”** — main section 8.3 / 8.10 ko isi text se replace/expand kar dunga.

---

**User**

yr ya template wali chez nhi aye ha samj mujhe bilkol bi humna tu simple ya discuss kiya na ka hum vendor outbound message kar structure simple rakha ga tu phr ya asa q ?

---

**Cursor**

# Short jawab — aap bilkul sahi confuse hue

Humne baad mein **simple outbound** finalize kiya:

```json
{ "number": "...", "message": "...", "type": "otp", "ttlSeconds": 300 }
```

Ismein **template ki zaroorat nahi**.

Plan mein jo **templates / Reply 1 Yes 2 No** wala lamba hissa hai, woh **pehle wali badi soch** se aaya (Yes/No buttons, OTP template Meta-style). Woh aap ke “structure simple rakho” wale faisle ke **baad sync nahi** hua — is liye plan ab **do raaste** dikhata hai aur aapko lagta hai contradiction hai.

---

## Do alag cheezein

| | Simple outbound (aap ka finalize) | Templates (plan ka purana extra) |
|---|---|---|
| Vendor bhejta hai | seedha `message` text | `template` + `variables` |
| Backend | text job mein save → plugin | pehle text render, phir bheje |
| Yes/No | nahi | “Reply 1 / 2” text options |
| Phase 1/2 shuru | **yahi kaafi** | optional / baad mein |

Aap ne jo discuss kiya → **pehli column**.  
Template section → **doosri column** (nice-to-have), abhi zaroori nahi.

---

## Phir plan mein q likha?

Kyun ke pehle aap ne buttons / OTP template jaisi baat ki thi, plan mein honest jawab diya:
- real WA buttons nahi  
- iski jagah text template + 1/2 options  

Phir aap ne kaha structure simple — **woh nayi direction** — lekin template chapters plan se hataaye / “Phase 3 optional” mark nahi hue, is liye ab clutter dikhta hai.

---

## Aap ke liye clear finalize (simple words)

**Abhi outbound = sirf raw message.**  
Vendor poora text khud likhe (`message`).  
`type` + `ttlSeconds` + delay/cap/queue — woh alag.

Templates = **baad ki feature**, abhi samajhne / banane ki zaroorat nahi jab tak aap na bolo.

---

Agent mode mein bolo to plan se template ko **“Optional / Phase 3”** mark kar ke primary contract sirf simple JSON pe clear kar sakta hoon, taa ke yeh confusion na rahe.

---

**User**

1.Yr abhi concurrent wala modula plan sa nikal do jo per vendor pa lagana isko hum baad ma implement karaga for now abhi ya over engineering ha 

2.yr plan ma tmna jo job pick ka beach ma time ha jasa normal message ma 1 sa 2 sec aur marketing message ma 10 sec sa above yr ya thk clear plan ma mention nhi ha celery job kasa pick karaga per vendor usko kasa pata chala ga job time ka cap isko thk sa mention karo

3.outbound template ka jo mana bataya tha { "number": "...", "message": "...", "type": "otp", "ttlSeconds": 300 } is ka hisab sa hi karna aur plan ma type batana ka kn kn si ha type aur kis ma kiya hota ha aur baas zahan ma ya rakho plan banata wa outbound message user jo bhaja type ka sath simple plugin pa forward karaga abhi tmna current plan ma bhot zada isko complex kiya hoa ha 

4.developer ko kasa different environment ma test karna ha ya sb clearly mention aur admin pannel pa on baord pa kiya kiya fields laga vendor sa wo bi thk sa mention ho




imp instruction:
tmna plan abhi bhot acha banaya ha lakin abhi bi kuch chezo ki kami ha is ma technical chez kuch jagah pa thk sa explain nhi ma ya nhi bol raha ho bhot muskil kar ka batao tm chezo ko bhot easy example sa hi batana lakin haar point ko thk sa discuss karna jasa ka mana kaha tha vendor ka api contract thk sa define karna haar chez lakin wo bi itna acha sa available nhi ha isma ak dedicated section honga taka ma vendor ko abhi sa bhajo taka jb tk ma system ready karo tu hamara vendor hamra contract ka hisab sa api ko finish kara bhai tm plan pora hi nhi change karna baas jo improvement bataya ha us sa better karna aur plan simple roman urdu ma ho
 

---

**Cursor**

Plan document ko rewrite nahi — aap ke 4 improvements ke hisab se targeted update ka plan bana raha hoon.

---

**User**


1.Yr abhi concurrent wala modula plan sa nikal do jo per vendor pa lagana isko hum baad ma implement karaga for now abhi ya over engineering ha 

2.yr plan ma tmna jo job pick ka beach ma time ha jasa normal message ma 1 sa 2 sec aur marketing message ma 10 sec sa above yr ya thk clear plan ma mention nhi ha celery job kasa pick karaga per vendor usko kasa pata chala ga job time ka cap isko thk sa mention karo

3.outbound template ka jo mana bataya tha { "number": "...", "message": "...", "type": "otp", "ttlSeconds": 300 } is ka hisab sa hi karna aur plan ma type batana ka kn kn si ha type aur kis ma kiya hota ha aur baas zahan ma ya rakho plan banata wa outbound message user jo bhaja type ka sath simple plugin pa forward karaga abhi tmna current plan ma bhot zada isko complex kiya hoa ha 

4.developer ko kasa different environment ma test karna ha ya sb clearly mention aur admin pannel pa on baord pa kiya kiya fields laga vendor sa wo bi thk sa mention ho




imp instruction:
tmna plan abhi bhot acha banaya ha lakin abhi bi kuch chezo ki kami ha is ma technical chez kuch jagah pa thk sa explain nhi ma ya nhi bol raha ho bhot muskil kar ka batao tm chezo ko bhot easy example sa hi batana lakin haar point ko thk sa discuss karna jasa ka mana kaha tha vendor ka api contract thk sa define karna haar chez lakin wo bi itna acha sa available nhi ha isma ak dedicated section honga taka ma vendor ko abhi sa bhajo taka jb tk ma system ready karo tu hamara vendor hamra contract ka hisab sa api ko finish kara bhai tm plan pora hi nhi change karna baas jo improvement bataya ha us sa better karna aur plan simple roman urdu ma ho
 







imp instruction :
current durectory ma jo VENDOR_GATEWAY_PLAN isko update karna yr 

---

**Cursor**

Yeh edit sirf markdown file mein hai, to is mode mein theek hai. `VENDOR_GATEWAY_PLAN.md` ko aap ke 4 improvements ke hisab se update kar raha hoon. Baqi plan wahi rakha hai.

Ek cross-reference ghalat reh gaya (webhook ab 17.B.8 mein hai). Theek kar raha hoon.

`VENDOR_GATEWAY_PLAN.md` (current directory) update ho gayi hai. Maine aapke 4 points aur vendor contract ke hisaab se poori file dobara likhi. Plan mode ki wajah se abhi koi code nahi likha gaya.

**Aapke 4 points kahan hain**

1. **Concurrency aur circuit breaker:** scope se hata diye hain. Ab sirf Phase 3 ke note mein hain (section 6.3 aur 12.D).
2. **Celery pick aur time gap:** section 8.3 mein explain kiya hai. Har vendor ke liye Redis mein do pace keys hain, `fast` aur `slow`.
   - `fast` lane OTP aur normal messages ke liye hai, 1–2 sec gap.
   - `slow` lane marketing ke liye hai, 8–20 sec random gap.
   - Celery sleep nahi karta. Wo har vendor ke liye ek countdown alarm (`wake:vendor:{id}`) lagata hai.
   - Plan mein 10 vendors wali example aur "OTP marketing ke gap ko jump karta hai" wali example bhi hai.
3. **Simple outbound:** body sirf `{ "number", "message", "type", "ttlSeconds" }` hai (sections 8.1 aur 8.1.1).
   - `type` ke teen options hain: `otp`, `transactional` aur `marketing`. Har type ki priority, gap, default TTL, cap aur retry rule table mein hai.
   - Plugin sirf message forward karta hai.
   - Templates, reply_context, Yes/No options aur STOP Phase 3 mein hain.
4. **Developer testing aur admin onboarding:**
   - Section 3.5 mein Step A–H hain, dev/test/staging/prod ke table ke saath.
   - Section 9.1 mein vendor onboarding form ke fields validation ke saath hain.

**Section 17: Vendor API Contract v1.** Ye section vendor ko abhi share kar sakte hain. Ismein inbound bot endpoint (media samples, HMAC verification ka Python aur Node code), outbound API, webhook, test numbers, limits, checklist aur FAQ hain.

Section 1.1 mein technical terms (Celery, Redis, lease, HMAC wagera) ki easy glossary bhi add ki hai. Ek cross-reference ghalat tha (webhook ka section 17.B.5 likha tha, sahi 17.B.8 hai). Wo theek kar diya.

**Jo defaults maine choose kiye hain** (aap badal sakte hain):
- Default TTL: otp 300 sec, transactional 3600 sec, marketing 86400 sec.
- TTL 30 se 86400 sec ke beech clamp hoti hai.
- Agar `type` na bheja jaye to default `transactional` hai.
- Marketing daily cap default 200 per vendor hai. Admin isse har vendor ke liye badal sakta hai.

Purani Cursor plan file `~/.cursor/plans/vendor_gateway_two_phase_bad75e66.plan.md` ab outdated hai. Workspace wali `.md` hi main file hai.

Aap plan padh kar batayein ke koi section aur simple karna hai ya koi default badalna hai. Uske baad Phase 1 implementation shuru kar sakte hain.

---

**User**

Magic numbers (test key ke saath): virtual plugin number ke aakhri hisse se behave karta hai:

isko simple kar ka batao

---

**Cursor**

## Magic numbers: simple matlab

**Problem:** Aap vendor ya developer hain aur apni API test karna chahte hain. Har baar asli WhatsApp number par message bhejna mushkil hai. Aap "number WhatsApp par nahi hai" wala error bhi asli number se nahi bana sakte.

**Hal:** Agar aap **test key** (`wab_test_...`) se request bhejte hain, to message asli WhatsApp par nahi jata. Ek **nakli plugin** (virtual plugin) jawab deta hai. Wo number ke **aakhri 4 digits** dekh kar tay karta hai ke kya natija dena hai. Isliye inhe "magic numbers" kehte hain. Number ke aakhri digits ek switch ki tarah kaam karte hain.

### Table

| Number ka end | Nakli plugin kya karta hai | Aap ko kya dikhta hai |
|---|---|---|
| `...0001` | "Ye number WhatsApp par nahi hai" | status `failed` (`NOT_ON_WHATSAPP`) |
| `...0002` | Pehli baar kehta hai "WhatsApp abhi ready nahi". Dusri baar bhejta hai | pehle retry, phir `sent` |
| `...0003` | Koi jawab hi nahi deta | status `unknown` |
| `...0004` | 30 second intezar karta hai, phir bhej deta hai | der se `sent` |
| baqi koi bhi number | 1 se 3 second mein bhej deta hai | `sent` |

### Example

Maan lein aap vendor ke developer hain:

```text
POST /v1/message-to-customer
X-API-Key: wab_test_abc...

{ "number": "923000000001", "message": "Aap ka OTP 482913 hai.", "type": "otp", "ttlSeconds": 300 }
```

Number `...0001` par khatam hota hai. Aap ko `failed / NOT_ON_WHATSAPP` milega.

Ab aap check kar sakte hain ke aap ka code is failure par theek chalta hai. Maslan, customer ko "number WhatsApp par nahi, SMS se bhej rahe hain" dikhana. Ye sab bina kisi asli phone ke test ho gaya.

Isi tarah:
- `...0003` bhej kar dekhein ke aap ka code `unknown` status par kya karta hai. Yahan OTP dobara mat bhejein, kyunki shayad customer ko pahunch chuka ho.
- `...0004` bhej kar dekhein ke 30 second ki der par aap ka timeout theek chalta hai ya nahi.

### Zaroori baatein
- Ye sirf **test key** par chalta hai. **Live key** (`wab_live_...`) par ye nahi chalta. Wahan `...0001` ek asli number hi samjha jayega.
- Isse developer ko apne code ke **saare raaste** (success, failure, der, koi jawab nahi) test karne ko milte hain. Isme na asli WhatsApp lagta hai aur na ban ka risk hota hai.

---

**User**

ma samj tu gaya lakin huma prod ma ya pata chal jaye ga ka user ka no whats up pa nhi ha ?

---

**Cursor**

## Short jawab

**Plan ke hisaab se haan, hum pata chalane ki koshish karte hain. Lekin ye abhi pakka nahi hai.** Ye Phase 2 ke Spike ke baad confirm hoga.

### Plan mein kya likha hai

Plugin ke send steps (section 8.8, step 4) mein likha hai:

```text
Step 4: Number WhatsApp pe hai? Nahi to NOT_ON_WHATSAPP.
```

Plugin pehle check karta hai ke number WhatsApp par registered hai ya nahi. Registered na ho to wo message nahi bhejta. Wo server ko `failed / NOT_ON_WHATSAPP` bhej deta hai. Server ye status vendor ko status GET ya webhook se de deta hai.

Ye bilkul waisa hai jaise aap WhatsApp Web mein "New chat" par kisi number ko search karein. Agar wo WhatsApp par nahi hai, to WhatsApp khud kehta hai "ye number WhatsApp par nahi hai".

### Honest baat: abhi pakka nahi

Plan ke section 11 (Spike) mein likha hai ke ye cheez **abhi verify nahi hui**:

> "Unknown number ka 'WhatsApp pe hai ya nahi' check, naya chat banana, send."

Plugin WhatsApp Web ke **internal functions** par chalta hai. Un mein number check karne ka koi official function nahi hai. Humein asli WhatsApp Web par dekhna hoga ke ye kaam karta hai ya nahi. WhatsApp apna code badalta rehta hai, isliye ye kabhi bhi toot sakta hai.

### Do mumkin natije

| Natija | Kya hoga |
|---|---|
| **Check kaam karta hai** | Number WhatsApp par na ho to turant `failed / NOT_ON_WHATSAPP` milta hai. Vendor SMS se bhej sakta hai. |
| **Check bharosemand nahi** | Plugin message bhejne ki koshish karega aur ack nahi aayega. Plan ke rule ke mutabiq 15 second baad status `unknown` hoga (retry nahi hoga). Is surat mein `NOT_ON_WHATSAPP` aur "network ka masla" mein farq nahi kiya ja sakega. |

### Vendor ko kya batana chahiye

Abhi vendor contract mein `NOT_ON_WHATSAPP` ek pakke wade ki tarah likha hai. Spike ke baad agar check bharosemand na nikla, to contract mein ye likhna padega ke "agar `failed` ya `unknown` aaye to dono surat mein backup channel (jaise SMS) use karein".

Spike ke baad mein aap ko batata hoon ke kaun sa natija nikla. Uske baad plan aur vendor contract us ke hisaab se update kar sakte hain.

---

**User**

Apna bot localhost:9000/ai-reply par chalao


ya local pa bot tm banao ga na taka normal test karo jb tk vendor ki apis na aye ?

---

**Cursor**

## Short jawab

**Haan, plan mein ye pehle se hai.** Aap ko local bot ke liye alag se kuch banana nahi padega. Lekin abhi hum Ask mode mein hain, isliye main code nahi likh sakta. Jab aap Agent mode mein jaa kar kahenge, tab banega.

### Aap ka sawal kya hai

Line 246 ka step hai: "Apna bot `localhost:9000/ai-reply` par chalao". Aap puch rahe hain ke ye bot mein banaunga ya aap ko khud banana padega, taake vendor ki asli API aane tak normal testing ho sake.

### Plan mein is ke liye 3 cheezein hain

Vendor ki API na ho to tab bhi aap in teeno se test kar sakte hain:

| Cheez | Kya hai | Kab use karein |
|---|---|---|
| **Reference bot** (Python + Node) | Chhota sa asli bot, jo vendor contract follow karta hai (signature verify bhi karta hai). Ye "Vendor kit" mein aata hai (implementation order, step 6). Isse `localhost:9000` par chalate hain. | Jab aap poora asli raasta test karna chahte hain: plugin, backend aur phir aap ka bot |
| **`mock_bot` mode** | Backend ke andar ka nakli bot. Alag se chalane ki zaroorat nahi. Isme failure scenarios hain: `ok`, `slow`, `error500`, `badjson`, `empty`, `huge`, `redirect`, `wrongshape`. | Jab aap backend ki failure handling test karna chahte hain |
| **`canned` aur `echo` mode** | `canned` hardcoded jawab deta hai. `echo` jo aap ne likha wahi wapas bhej deta hai. Dono mein bot ko koi HTTP call nahi jati. | Sab se aasan. Sirf plugin se backend tak ka raasta dekhna ho |

### Aap ke liye sab se aasan tarika

1. Pehle `echo` ya `canned` mode se shuru karein. Isme bot ki zaroorat hi nahi.
2. Phir reference bot ko `localhost:9000` par chalayein aur mode `forward` karein.
3. Admin mein `bot_base_url = http://localhost:9000` dein. Dev mein localhost allowed hai (`ALLOW_PRIVATE_BOT_URLS=true`).

Is tarah vendor ki API aane tak aap ka poora flow normal test ho jata hai. Jab vendor ki asli API aaye, to sirf `bot_base_url` badalni hogi. Baqi sab kuch wahi rahega.

### Ek cheez tay karni hogi

Reference bot ke saath **kya** jawab dena hai, ye aap ko batana hoga. Do options hain:
- Simple jawab: jaise "Aap ne likha: ..."
- Asli AI jawab: Groq ya vision model ke saath (jaise purane Turabees server mein tha)

Agar aap chahte hain ke main reference bot banaun, to Agent mode mein jaa kar bata dein ke simple jawab chahiye ya AI wala.

---

**User**

yani t_base_url = http://localhost:9000 ya bot mujhe khud alag sa banana honga ya tm ak normal bot banao ga taka ma easily ya test karo sb ya ha na ma batao 

---

**Cursor**

## Jawab

**Aap ko khud bot banana nahi padega. Main ek normal test bot bana dunga.** Plan mein ye pehle se likha hai (implementation order, step 6: "reference bots Python + Node").

Abhi hum Ask mode mein hain, isliye main kuch bana nahi sakta. Jab aap Agent mode mein jaa kar bolenge "bot banao", tab main bana dunga.

### Bot kaisa hoga

Ye chhota sa **test bot** hoga. Is mein ye sab hoga:
- `localhost:9000/ai-reply` par chalega.
- Aap ke message ka simple jawab dega. Maslan: "Aap ne likha: Red lehenga available hai?"
- Image, audio ya document aaye to bata dega ke "1 image mili".
- Backend ka signature verify karega, jaisa asli vendor ko karna hota hai.

### Aap kya karenge

```text
1. Bot chalayein (ek command, port 9000)
2. Admin mein bot_base_url = http://localhost:9000 likhein
3. inbound_mode = forward rakhein
4. WhatsApp mein message bhejein -> bot ka jawab wapas aayega
```

Isse aap poora raasta dekh sakte hain: WhatsApp, plugin, backend, bot, phir wapas WhatsApp.

### Asli vendor aaye to

Asli vendor ki API aane par sirf `bot_base_url` badalni hogi. Baqi kuch nahi badlega.

### Ek cheez aap se chahiye

Bot ka jawab kaisa ho? Do options hain:
1. **Simple jawab** (echo jaisa): jaldi ban jata hai aur testing ke liye kaafi hai.
2. **AI wala jawab** (Groq ya vision model): purane Turabees server jaisa, lekin API key chahiye.

Mera suggestion hai ke pehle **simple jawab** wala bot banayein. AI baad mein jab chahein jod sakte hain.

---

**User**

POST /v1/sandbox/simulate-incoming      # backend aap ke bot ko call karta hai, jawab + validation report wapas
POST /v1/message-to-customer            # test key => virtual plugin, magic numbers



easy way ma explain karo

---

**Cursor**

## Ye do endpoints kya hain

Ye dono **vendor ke developer** ke liye hain. Isse wo bina WhatsApp ke apna kaam test kar sakta hai. Dono mein **test key** (`wab_test_...`) lagti hai.

Do tarah ka test hai:
- **Inbound:** customer ne message bheja, hum vendor ke bot ko bhejte hain.
- **Outbound:** vendor customer ko message bhejna chahta hai.

### 1. `POST /v1/sandbox/simulate-incoming` (inbound ka test)

**Matlab:** "Aisa samjho ke customer ne message bheja hai. Hum aap ke bot ko bhej ke dekhte hain."

**Kaise kaam karta hai:**

```text
Vendor developer ----(test key + nakli message)----> Hamara backend
                                                          |
                                                          | asli format ki request
                                                          v
                                                    Vendor ka bot
                                                          |
                                                          | jawab
                                                          v
Vendor developer <----(report)---- Hamara backend
```

**Example:** Developer ye bhejta hai:

```text
POST /v1/sandbox/simulate-incoming
X-API-Key: wab_test_abc...

{ "text": "Red lehenga available hai?" }
```

Hum us ke bot ko bilkul asli format ki request bhejte hain. Is mein signature, headers, sab kuch hota hai. Phir developer ko ek **report** wapas milti hai:

```text
Hum ne aap ke bot ko ye bheja:   { text: "Red lehenga available hai?", ... }
Bot ne ye jawab diya:            { "replies": [ { "type": "text", "text": "Ji hai!" } ] }
Time laga:                       1.2 second
Galtiyan:                        koi nahi
```

Agar bot ka jawab galat format mein ho (jaise `replies` ki jagah `reply`), to report mein likha hoga: "Galat shape: `replies` array chahiye."

**Faida:** Developer apna bot ka code **bina WhatsApp ke** test kar leta hai aur pata chal jata hai ke contract sahi follow ho raha hai ya nahi.

### 2. `POST /v1/message-to-customer` (test key ke saath, outbound ka test)

**Matlab:** "Customer ko message bhejna hai (jaise OTP). Test key lagayi hai, to asli WhatsApp par nahi jayega."

**Kya hota hai:**

```text
Vendor developer ----(test key + number + message)----> Hamara backend
                                                              |
                                                       nakli plugin (virtual plugin)
                                                              |
                                                       Number ke aakhri digits dekh kar
                                                       natija deta hai (magic numbers)
```

**Example:**

```text
POST /v1/message-to-customer
X-API-Key: wab_test_abc...

{ "number": "923000000001", "message": "Aap ka OTP 482913 hai.", "type": "otp", "ttlSeconds": 300 }
```

Number `...0001` par khatam hota hai. Isliye koi asli message nahi jata. Developer ko status milta hai `failed / NOT_ON_WHATSAPP`. Is se wo apne code ka failure wala raasta test kar leta hai.

### Dono ka fark

| | `simulate-incoming` | `message-to-customer` (test key) |
|---|---|---|
| Kis taraf ka test | Customer, phir hum, phir vendor ka bot | Vendor, phir hum, phir customer |
| Kya test hota hai | Vendor ka bot sahi jawab deta hai ya nahi | Vendor ka code `sent`, `failed`, `unknown` status sambhalta hai ya nahi |
| Asli WhatsApp lagta hai | Nahi | Nahi |
| Result | Ek report (kya gaya, kya aaya, kya galat) | Status (magic number ke hisaab se) |

### Ek zaroori baat

Ye dono abhi sirf **plan** mein hain, code mein nahi bane. Jab Phase 1 aur Phase 1B implement hoga, tab ye endpoints milenge.

---

**User**

Ek nakli web.whatsapp.com page jis mein asli WhatsApp jaise internal functions ke stubs hain (message aana, send, connection state, ack). Control API:



ya fake page ka FastAPI route kiya honga ?

---

**Cursor**

## Jawab

**Nahi, ye FastAPI ka route nahi hai.** Fake WhatsApp page **backend ka hissa nahi** hai. Ye ek alag cheez hai, jo sirf **plugin (Chrome extension) ko test karne** ke liye hai.

### Ye kya hai

Ek chhota sa nakli `web.whatsapp.com` page hota hai. Ye ek simple HTML/JS page hai, jo test ke waqt Chrome mein khulta hai. Plugin samajhta hai ke ye asli WhatsApp Web hai.

Is page ke andar WhatsApp ke internal functions ke **nakli versions** (stubs) hote hain:
- message aana
- message bhejna
- connection state (connected, offline, logged_out)
- ack aana (message pahunch gaya)

### "Control API" kya hai

Ye FastAPI ka endpoint nahi hai. Ye us fake page ke andar ke **JavaScript functions** hain. Test code (Playwright) in functions ko call karke page ko hukm deta hai:

```text
fake.deliverIncoming({chat, text, media})   -> "ek customer ka message aaya"
fake.setConnState('offline')                -> "ab internet/WhatsApp offline ho jao"
fake.setAckDelay('never')                   -> "ack kabhi mat bhejo"
fake.rejectSend()                           -> "send fail kar do"
```

### Poora picture

```text
Playwright test (aap ka test code)
        |
        | fake.setConnState('offline') jaisa hukm
        v
Fake WhatsApp page (Chrome mein khula, plugin yahin chalta hai)
        ^
        | plugin is page ke nakli functions ko call karta hai
        |
Plugin (Chrome extension)
        |
        | Socket.IO / HTTP
        v
Hamara FastAPI backend (asli, ya test wala)
```

Backend ko pata hi nahi chalta ke ye nakli page hai. Wo bas plugin se baat karta hai.

### Faida

Test ke dauran asli WhatsApp ki zaroorat nahi. Aap kisi bhi waqt "offline ho jao" ya "send fail" jaisi surat bana sakte hain aur dekh sakte hain ke plugin sahi behave karta hai. Asli WhatsApp mein aisi surat banana mushkil hai.

### FastAPI se farq

| | FastAPI routes | Fake WhatsApp page |
|---|---|---|
| Kahan chalta hai | Server par | Chrome ke andar |
| Kis ka test | Backend ka | Plugin ka |
| Kaun call karta hai | Plugin, vendor, admin | Playwright test code |

Plan mein ye section 7.4 aur 14.9 mein hai. Ye Phase 1 ke plugin tests ke liye hai. Aap manual testing ke liye asli WhatsApp hi use kar sakte hain.

---

**User**

tu ya fake page fastapi jinja2 ma honga na jo ka endpoint sa serve honga asa hi na ?

---

**Cursor**

## Jawab

**Ye plan mein likha nahi hai.** Plan sirf kehta hai "ek nakli `web.whatsapp.com` page" (section 7.4). Ye nahi likha ke wo kahan se serve hoga. Neeche jo hai wo meri suggestion hai, plan ka hissa nahi.

### Aap ka idea chal sakta hai, lekin ek masla hai

Aap ka idea ye hai ke fake page FastAPI mein Jinja2 se banaya jaye aur ek endpoint (jaise `/fake-wa`) se serve ho. Ye technically ho sakta hai.

Masla ye hai ke extension sirf `web.whatsapp.com` URL par chalti hai (manifest ke `matches` mein). Agar fake page `http://localhost:8000/fake-wa` par khula, to extension us par load hi nahi hogi. Plugin ko pata hi nahi chalega ke koi page hai.

### Teen options

| Option | Kaise | Faida | Nuksan |
|---|---|---|---|
| **A. Playwright route interception** (meri recommendation) | Test code Chrome ko kehta hai: "jab `web.whatsapp.com` khule, to asli site ki jagah meri fake HTML file dikha do." URL wahi rehta hai. | Extension ka code ya manifest badalna nahi padta. FastAPI se koi lena dena nahi. Ek simple static HTML + JS file kaafi hai. | Sirf automatic tests mein chalta hai |
| **B. FastAPI se serve** (aap ka idea) | `/fake-wa` route Jinja2 se page deta hai. | Manual playground ban sakta hai (browser mein khol kar control buttons dabayein) | Dev extension build ke manifest mein `localhost` bhi `matches` mein dalna padega. Prod build mein ye kabhi nahi hona chahiye. |
| **C. Alag chhota static server** | Backend se alag ek simple file server | Backend saaf rehta hai | Ek aur cheez chalani padegi, aur manifest wali wohi dikkat |

### Meri suggestion

**Option A** se shuru karein. Ye sab se saaf hai. Fake page ek static file hoti hai (`tests/fake-whatsapp/index.html`), jise Playwright `web.whatsapp.com` ki jagah serve karta hai. Is mein FastAPI ya Jinja2 ki zaroorat nahi.

Agar baad mein aap ko **manual playground** chahiye (khud browser mein buttons dabane ke liye), to option B jod sakte hain. Lekin tab dev build mein manifest mein `localhost` allow karna hoga, aur prod build mein ye na ho, iska check lagana hoga.

### Plan mein kya badalna hoga

Plan section 7.4 mein ek line jodni chahiye ke fake page kaise serve hota hai. Abhi wo adhoora hai. Agar aap Agent mode mein jaa kar kahein, to main ye wazahat plan mein add kar dunga. Aap option A chahte hain ya B, wo bata dein.

---

**User**

agr mujhe ya fake whatsup wali chez hatana ha tu mujhe plan sa kn sa section hatana honga ?

---

**Cursor**

## Jawab

Fake WhatsApp ko poori tarah hatane ke liye plan mein **ek section hatana hai aur baqi jagah chhoti chhoti edits karni hain**. Ek akela section hatane se kaam nahi chalega, kyunki ye cheez kai jagah mention hai.

### Poori tarah hatana ho

| Jagah (line) | Kya karna hai |
|---|---|
| **Section 7.4** (line 570 se 583) | **Poora hata dein.** Yehi asli section hai. |
| **Section 14.9** (line 1309 se 1337) | Ye poora Playwright + fake WhatsApp ke ird gird hai, **hata dein**. Is mein plugin ke offline, service worker kill, ack, 5-profile routing wale tests hain. |
| Line 117 aur 119 (glossary) | "Fake WhatsApp" wali line hata dein |
| Line 282 (section 3.5 Step F) | `npx playwright test  # extension + fake WhatsApp` wali line hata dein |
| Line 302 (environment table) | `test` row se "Fake WhatsApp page" nikal dein |
| Line 1167 | Architect review ke item 1 se "fake WhatsApp" ka lafz nikal dein |
| Line 1196 | Tools list se "Playwright (... + fake WhatsApp)" nikal dein |
| Line 1362, 1377, 1400, 1408 | Implementation order aur todo list se Playwright wale items hata dein |

### Ek zaroori cheez: kya khoyenge

Fake WhatsApp hatane se plugin ke **automatic tests** nahi rahenge. Section 14.9 mein ye sab tests the:
- WhatsApp offline hone par job nahi bheji jati
- Service worker band ho to wapas jag jaye
- Ack na aaye to `unknown` ho
- Page reload ke beech mein job aaye to duplicate na bane
- 5 Chrome profiles mein job sahi plugin ke paas jaye

Ye sab ab sirf **manual** test honge, asli WhatsApp par. Duplicate OTP jaise worst cases manual mein dobara dobara banana mushkil hota hai. Plugin ka code hi sab se risky hissa hai (WhatsApp ke internal functions), is wajah se ye tests kaafi value dete hain.

### Beech ka raasta

Agar aap ko lagta hai ke fake page banana bhaari hai, to ye bhi kar sakte hain:
- **Phase 1 mein hata dein.** Phase 1 mein plugin ka kaam sirf API key lagana aur inbound jawab hai. Wahan asli WhatsApp se manual test kaafi hai.
- **Phase 2 mein wapas laayein.** Outbound mein offline, ack aur duplicate wale risk hain, jahan fake page sab se zyada kaam aata hai.

Is surat mein plan mein section 7.4 aur 14.9 ko "Phase 2" mark kar dein. Poora hatana nahi padega.

### Jo nahi hatana

**Virtual plugin** (section 7.3) fake WhatsApp se alag cheez hai. Ye backend ka nakli Socket.IO client hai, aur test keys aur magic numbers isi par chalte hain. Isse mat hatayein. Wo FastAPI side ki testing ke liye zaroori hai.

Aap Agent mode mein bata dein ke poora hatana hai ya sirf Phase 2 mein shift karna hai, to main plan edit kar dunga.

---

**User**

tu agr ma na hatao tu kam tu sir automatic testing ka kam ka ha na mujhe is sa koi farak tu nhi honga ?

---

**Cursor**

## Jawab

**Haan, aap sahi samjhe hain.** Fake WhatsApp sirf **automatic testing** ke liye hai. Agar aap isko na hatayein aur na banayein, to asli system (production) par koi farq nahi padta.

### Production par kuch nahi badalta

Fake page production mein kabhi load nahi hota. Wo sirf test ke waqt Playwright ke saath chalta hai. Asli plugin, backend, vendors aur customers ko is se koi lena dena nahi.

### Farq kahan padta hai

Farq **testing ke tareeqe** mein padta hai. Do surtein hain:

| | Fake page rakhein (aur banayein) | Fake page na banayein |
|---|---|---|
| **Plugin ki testing** | Automatic: offline, ack, duplicate, 5 profiles sab ek command se | Manual: asli WhatsApp par khud karni padegi |
| **Mehnat (shuru mein)** | Fake page banane mein waqt lagta hai | Kuch nahi |
| **Mehnat (baad mein)** | Har change ke baad test khud chal jata hai | Har change ke baad dobara manual test |
| **Risk** | Kam | Zyada, khas kar duplicate OTP aur offline wale cases mein |

### Ye cheez rakhein ya hatayein

Plan mein jo rakha hai (aur aap ko nahi hatana):
- **Virtual plugin** (7.3): backend ke andar. Test key aur magic numbers isi par chalte hain. Ye alag cheez hai.
- Backend ke tests: pytest, race tests, pacing tests wagera.

Ye sab fake WhatsApp ke baghair bhi chalte hain. Sirf **plugin ke andar ke tests** (14.9) fake page par depend karte hain.

### Meri raye

- **Phase 1 (inbound):** fake page ki zaroorat nahi. Asli WhatsApp par manual test kaafi hai.
- **Phase 2 (outbound, OTP):** yahan fake page kaam ka hai. OTP ka duplicate jaana sab se bura case hai. Internet off/on, WhatsApp logout aur page reload jaise cases asli WhatsApp par baar baar banana mushkil aur risky hai (ban ka khatra bhi hai).

Is liye behtar ye hai ke abhi plan mein rehne dein. Phase 2 tak ye banane ki zaroorat nahi padegi. Us waqt tak aap decide kar sakte hain.

---

**User**

ya wasa karta kiya ha means fake whatsup test karta kasa aur ya kam ka ha bi ya nhi ?

---

**Cursor**

## Fake WhatsApp kaise test karta hai

**Ek misaal se samjhein.** Aap driving seekh rahe hain. Asli sadak par seekhna risky hai, isliye pehle simulator mein seekhte hain. Simulator mein aap "baarish ho jaye" ya "brake fail ho jaye" jaise haalat khud bana sakte hain. Fake WhatsApp plugin ke liye wahi simulator hai.

### Kaise kaam karta hai

```text
1. Test chalta hai. Chrome khulta hai, jis mein aap ka plugin load hai.
2. Chrome "web.whatsapp.com" kholta hai, lekin asli WhatsApp ki jagah nakli page dikhta hai.
3. Plugin samajhta hai ke ye asli WhatsApp hai.
4. Test code nakli page ko hukm deta hai, aur dekhta hai ke plugin kya karta hai.
```

### Ek test ka example (plan ke section 14.9 se)

**Test:** "WhatsApp offline ho to OTP na jaye, wapas aaye to chala jaye."

```text
Step 1: Test kehta hai fakeWA.setConnState('offline')
        -> nakli WhatsApp: "main offline hoon"

Step 2: Test backend par ek OTP job daalta hai
        -> backend job plugin ko bhejta hai

Step 3: Test check karta hai: plugin ne message bheja? 
        -> fakeWA.sentMessages() khali hona chahiye (plugin ne rok liya)
        -> job ka status 'queued' hona chahiye

Step 4: Test kehta hai fakeWA.setConnState('connected')
        -> nakli WhatsApp: "main wapas online hoon"

Step 5: Test check karta hai: ab plugin ne message bheja? 
        -> fakeWA.sentMessages() mein 1 message hona chahiye
        -> job ka status 'sent' hona chahiye
```

Agar plugin offline mein bhi message bhej de, to test **fail** ho jata hai. Is se pata chal jata hai ke code mein bug hai.

### Kaun kaun si surat bana sakta hai

| Aap kya chahte hain | Asli WhatsApp par | Fake WhatsApp par |
|---|---|---|
| WhatsApp offline karna | Mushkil, phone ka net band karna padta hai | Ek command |
| Ack kabhi na aaye | Banana lagbhag mumkin nahi | `setAckDelay('never')` |
| Send fail ho jaye | Mushkil | `rejectSend()` |
| 5 profiles ek saath | 5 asli numbers chahiye, ban ka risk | 5 fake pages, zero risk |
| Test 100 baar chalana | Asli number ban ho sakta hai | Koi masla nahi |

### Kya ye kaam ka hai? Haan, lekin ek hadd tak

**Kaam ki cheezein:**
- Plugin ka **apna logic** check hota hai. Maslan: offline mein rukna, duplicate na bhejna, page reload ke baad dobara na bhejna, sahi profile ko sahi job milna.
- Ye logic hi sab se zyada bugs wali jagah hai (khas kar OTP duplicate).
- Test automatic chalta hai. Har code change ke baad khud check ho jata hai.

**Hadd (ye baat zaroor jaan lein):**
- Fake page ke functions **hum khud banate hain**. Wo asli WhatsApp jaise **dikhte** hain, lekin asli WhatsApp ke andar ke functions ke naam aur tareeqe hum abhi pakka nahi jaante (plan ka Spike, section 11, isi liye hai).
- Isliye agar fake page par test pass ho jaye, iska matlab sirf itna hai ke "plugin ka logic theek hai". Iska matlab ye **nahi** ke "asli WhatsApp par bhi chalega".
- Agar WhatsApp apne internal functions badal de, to fake page par test pass hote rahenge lekin asli WhatsApp par plugin toot jayega.

### Nateeja

| Sawal | Jawab |
|---|---|
| Plugin ke logic bugs pakadne ke liye kaam ka hai? | Haan, bahut |
| Asli WhatsApp compatibility ki guarantee deta hai? | Nahi |
| Phase 1 mein zaroori hai? | Nahi |
| Phase 2 (OTP, outbound) mein? | Haan, kaafi kaam ka |

Asli WhatsApp par chalne ke liye aap ko phir bhi **Spike** (asli WhatsApp par research) aur kuch **manual tests** chahiye honge. Fake page un ki jagah nahi leta. Wo sirf plugin ke apne logic ki hifazat karta hai.

---

**User**

lakin ya web.WhatsApp pa kasa fake page kholta ha aur usma automatic pulgin khud hi honga na mujhe unpack tu nhi karna honga plugin ?

---

**Cursor**

## Jawab

**Nahi, aap ko kuch manually unpack ya load nahi karna padega.** Test code khud Chrome kholta hai, khud plugin load karta hai, aur khud fake page dikhata hai.

Plan section 14.9 mein sirf itna likha hai: "Real Chromium (`--load-extension`, `xvfb-run` ya `--headless=new`)". Neeche ki detail Playwright ke standard tareeqe se hai, plan mein itni detail nahi likhi.

### Step by step: test ke waqt kya hota hai

```text
Aap ek command chalate hain:   npx playwright test
```

**Step 1: Chrome khulta hai, plugin pehle se load hota hai**

Test code Chrome ko is tarah start karta hai:

```ts
const context = await chromium.launchPersistentContext(userDataDir, {
  args: [
    `--disable-extensions-except=${pathToExtension}`,
    `--load-extension=${pathToExtension}`,   // plugin ka folder
  ],
});
```

`pathToExtension` aap ka plugin ka folder hota hai (dev build wala). Ye wohi kaam hai jo aap Chrome mein "Load unpacked" se karte hain. Bas yahan test code khud kar deta hai. Aap ko Chrome ke extensions page par jaana nahi padta.

**Step 2: `web.whatsapp.com` ko nakli page se badla jata hai**

```ts
await context.route('https://web.whatsapp.com/**', route =>
  route.fulfill({ contentType: 'text/html', body: fakeWhatsAppHtml })
);
```

Matlab Chrome ko kaha: "jab bhi `web.whatsapp.com` khole, internet par mat jao, ye meri fake HTML file de do."

**Step 3: Page kholte hi plugin khud chal padta hai**

```ts
const page = await context.newPage();
await page.goto('https://web.whatsapp.com/');
```

Address bar mein URL `web.whatsapp.com` hi dikhta hai. Isliye plugin ke manifest ke `matches` mein jo `web.whatsapp.com` likha hai, wo match ho jata hai. Plugin ke content scripts khud inject ho jate hain, bilkul asli WhatsApp ki tarah. Manifest ya plugin ke code mein kuch badalna nahi padta.

**Step 4: Test control karta hai**

```ts
await page.evaluate(() => window.fake.setConnState('offline'));
```

### Picture

```text
Aap: npx playwright test
          |
          v
Test code Chrome kholta hai (plugin load, fake page serve)
          |
          v
web.whatsapp.com  --->  [nakli HTML]  <--- plugin yahan inject hota hai
          ^                                      |
          | fake.setConnState(...)               | plugin ka logic chalta hai
       Test code                                 v
                                           Hamara backend
```

### Do chhoti baatein

1. **Headless mode:** Chrome extensions normal headless mode mein nahi chalte. Isliye plan mein `--headless=new` ya `xvfb-run` (nakli screen) likha hai. Ye CI server par chalane ke liye hai. Aap laptop par khud dekhna chahein to Chrome ki asli window bhi khul sakti hai.
2. **Dev build:** Test ke liye plugin ka dev build (jis mein `config.js` se backend ka localhost URL hota hai) use hota hai, section 3.2 ke mutabiq. Asli production build nahi.

Aap sirf `npx playwright test` chalate hain. Baqi sab test code khud sambhal leta hai.

---

**User**

Is qaide ka faida: Ali ne 100 marketing API se bhejin to 100 alarm nahi lagte, sirf ek lagta hai.

Ek chhota example (Ali, vendor A):

10:00:00.0  OTP#1 bheji. job_result aaya.
            fast = 10:00:01.5, slow = 10:00:12.0
10:00:00.1  dispatch_next(A): top = marketing#1, slow ke liye 11.9s baki
            -> alarm 10:00:12 pe lagaya, task khatam
10:00:03.0  Ali ne OTP#2 bheja. API -> dispatch_next(A)
            top = OTP#2 (priority), fast (10:00:01.5) guzar chuka, wait 0
            -> OTP#2 claim, plugin ko bhej di
            job_result ke baad fast = 10:00:04.5, slow = 10:00:15 (naya)
10:00:15    alarm baja: dispatch_next(A): marketing#1 chal gayi
OTP ko marketing ke gap ka intezar nahi karna padta. Isi liye do alag keys hain.

Ab 10 vendors:

A: dispatch_next("A") + Redis pace:vendor:A:*  ->  apni line
B: dispatch_next("B") + Redis pace:vendor:B:*  ->  apni line
...
J: dispatch_next("J") + Redis pace:vendor:J:*  ->  apni line
Har vendor ke keys alag, har vendor ka task alag. A ka 12s wait B ko rokta nahi.
Multiple Celery workers ek saath chal sakte hain, har task ek vendor ke liye.
Ek vendor ke liye kabhi do job ek waqt mein nahi jati (DB unique index "busy" kehta hai).
Worker sleep() nahi karta: wait ke liye Celery ka countdown (alarm) lagta hai. Worker foran free ho jata hai aur dusre vendors ka kaam kar sakta hai. Countdown sirf seconds ka hai (20s tak), is liye Redis broker ke saath masla nahi.

Daily cap alag cheez hai: marketing 200/day (section 8.10) ka counter alag Redis key hai, gap se nahi milta.

Kyun ye safe hai:

dispatch_next do baar chal jaye to koi nuksan nahi (claim atomic, "busy"/"paced" return).
Worker alag process hai, wo seedha socket ko nahi bol sakta. Isliye Socket.IO ka Redis manager (write-only) use hota hai: worker Redis pe likhta hai, API process wahan se plugin tak pohancha deta hai.
Celery settings: acks_late=True, reject_on_worker_lost=True, prefetch_multiplier=1, result backend nahi (DB status kaafi hai), queues: dispatch, maintenance, webhooks.






simple way ma short batao

---

**Cursor**

## Short version

**Problem:** Ali ne 100 marketing messages bheje. Har message ke liye alag timer (alarm) lagana faltu hai.

**Hal:** Har vendor ke liye **ek hi alarm** lagta hai. Alarm bajta hai to agla job chalta hai. Marketing ke 100 jobs hon tab bhi ek waqt par ek hi alarm hota hai.

### Do alag gap kyun

Har vendor ke liye Redis mein do timer hain:

| Timer | Kis ke liye | Gap |
|---|---|---|
| `fast` | OTP aur normal message | 1 se 2 second |
| `slow` | Marketing | 8 se 20 second |

Isse OTP marketing ke lambe gap mein nahi phansta.

### Example (Ali)

```text
10:00:00   OTP#1 gayi. Ab marketing#1 ko 12 second rukna hai.
           Alarm 10:00:12 par laga diya.
10:00:03   Ali ne OTP#2 bheja. OTP ka gap guzar chuka hai.
           OTP#2 foran chali gayi (marketing ka intezar nahi kiya).
10:00:15   Alarm baja. Marketing#1 chali gayi.
```

### 10 vendors

- Har vendor ki apni line hai, apne Redis keys aur apna task.
- Vendor A ka 12 second ka intezar vendor B ko nahi rokta.
- Worker `sleep()` nahi karta. Wo alarm laga kar foran free ho jata hai aur dusre vendors ka kaam kar leta hai.
- Ek vendor ki ek waqt mein sirf ek hi job jati hai. Ye DB ka unique index pakka karta hai.

### Safe kyun hai

- `dispatch_next` do baar chal jaye to koi nuksan nahi. Dusri baar "busy" ya "paced" wapas aata hai.
- Worker seedha plugin ko message nahi bhej sakta. Wo Redis par likhta hai aur API process wahan se plugin tak pohancha deta hai.
- Celery ki settings aisi hain ke worker crash ho to job gum nahi hoti, dobara chalti hai.

### Daily cap alag hai

Marketing ki roz ki limit (jaise 200) ka counter alag Redis key mein hota hai. Wo gap ke timer se alag cheez hai.

---

**User**

yr asa tu nhi agr ma outbound message bhajo aur wo whatsup no pa phala sa save nhi ha to wo usko message na bhaja q ka sb no tu mera pass save nhi honga yaha confusion ha kahi ya fail tu nhi honga ?

---

**Cursor**

## Jawab

**Aap ka dar sahi jagah hai, lekin number save hona zaroori nahi.** WhatsApp mein kisi bhi number ko message bheja ja sakta hai, chahe wo contacts mein save na ho. Jaise wa.me link se kisi bhi number par chat khul jati hai.

### Plan mein ye pehle se soch rakha hai

Plugin ke send steps (section 8.8) mein:

```text
Step 4: Number WhatsApp pe hai? Nahi to NOT_ON_WHATSAPP.
Step 5: Chat dhoondo, nahi hai to banao (customer ne kabhi message nahi kiya).
Step 6: message text bhejo.
```

Yani plugin pehle us number ki chat dhoondta hai. Agar pehle kabhi chat nahi hui (number save nahi, koi purana message nahi), to **naya chat khud banata hai** aur phir message bhejta hai. Save hone ki zaroorat nahi.

### Ye cheez pakki nahi

Ye abhi **verify nahi hui**. Spike (section 11) ka pehla point yehi hai: "Unknown number ka check, **naya chat banana**, send." Plugin WhatsApp ke internal functions use karta hai. Naya chat banane ka internal function kaam karta hai ya nahi, ye asli WhatsApp Web par dekhna hoga.

Is mein ek aur masla bhi hai: **LID** (Spike point 4). WhatsApp kuch chats ko phone number ki jagah `@lid` identifier se dikhata hai. Naye number se chat banate waqt ye masla aa sakta hai.

### Agar internal function kaam na kare

Ye meri suggestion hai, plan mein nahi likhi. Pehle se maujooda tareeqa bhi hai:

- Plugin WhatsApp Web mein `web.whatsapp.com/send?phone=NUMBER` wala link khol sakta hai. Ye wohi link hai jo wa.me se khulta hai. Is se unsaved number ki chat khul jati hai aur phir message bheja ja sakta hai.
- Is ke nuksan: ye page ko reload kar sakta hai (ye bhi verify karna hoga), aur WhatsApp kabhi kabhi "ye number WhatsApp par nahi hai" ka popup deta hai. Plugin ko wo popup padhna hoga.

### To fail hoga ya nahi

| Surat | Natija |
|---|---|
| Internal function naya chat bana le | Message chala jata hai, save hona zaroori nahi |
| Internal function kaam na kare, link wala tareeqa kaam kare | Chalta hai, lekin thoda slow aur page reload ka khatra |
| Dono kaam na karein | Job `failed` ya `unknown` hogi |

Ye sab Spike mein pakka hoga, code likhne se pehle. Phase 2 ka pehla kaam yahi hona chahiye. Agar Spike mein ye cheez kaam na kare, to poora outbound plan (OTP wagera) ka bunyadi hissa hi hil jata hai. Isliye Spike sab se pehle karna zaroori hai.

---

**User**

abhi ka liya for now plan ko implement kar ka dakho ? agr nhi hoa tu kuch socho ?

---

**Cursor**

Abhi main Ask mode mein hoon, is liye code nahi likh sakta aur na asli WhatsApp Web par kuch chala kar dekh sakta hoon. Implement karne ke liye aap ko Agent mode mein jaana hoga.

Pehle ye tay kar lein ke "plan implement kar ke dekho" ka matlab kya hai. Plan bohot bara hai.

**Implement kahan se shuru karun?**

> **Agent mode mein jaane ke baad sab se pehle kya implement karun?**
> - Pehle chhota Spike: plugin se unsaved number ko naya chat bana kar message bhejna (asli WhatsApp Web par), kaam na kare to doosra tareeqa dhoondna (Recommended)
> - Phase 1 backend: FastAPI skeleton, vendors/API keys, /v1/ai-reply, test bot (localhost:9000)
> - Poora Phase 1: backend + admin/Test Lab + plugin changes + reference bot
>
> Answer: ma sir puch raha ho

Theek hai, aap sirf pooch rahe the. Jawab ye hai.

### Kya main implement kar ke dekh sakta hoon?

**Ek hissa kar sakta hoon, ek hissa nahi.**

| Hissa | Main khud kar ke dekh sakta hoon? |
|---|---|
| Backend (FastAPI, API keys, `/v1/ai-reply`, test bot, tests) | **Haan.** Code likh kar, chala kar aur tests pass karwa kar dekh sakta hoon. |
| Plugin ka naya code (popup, API key, config) | **Haan**, code likh sakta hoon. |
| **Unsaved number ko message bhejna asli WhatsApp Web par** | **Nahi poori tarah.** |

### Asli WhatsApp wala hissa kyun nahi

Ye wohi Spike wali cheez hai. Is ke liye asli WhatsApp Web chahiye, jis mein aap ka account login ho. Login ke liye phone se QR scan karna padta hai, jo sirf aap kar sakte hain. Main khud login nahi kar sakta.

Yani main plugin ka code likh sakta hoon, lekin "naya chat bana ke message gaya ya nahi" ka asli natija aap ko apne browser mein dekhna hoga.

### Agar pehla tareeqa kaam na kare

Plan mein ye pehle se hai, aur mera tareeqa ye hoga:

1. **Pehla tareeqa:** WhatsApp ke internal function se naya chat banana.
2. **Doosra tareeqa:** `web.whatsapp.com/send?phone=NUMBER` wala link. Ye wohi cheez hai jo wa.me link karta hai, aur isme number save hona zaroori nahi.
3. **Agar dono na chalein:** job `failed` ho. Phir official WhatsApp Cloud API par sochna padega, jahan ye masla hi nahi hota (lekin template approval aur paise ka alag masla hai).

### Mera suggested raasta

1. Agent mode mein jaa kar **pehle chhota Spike** karein. Maine plugin mein ek chhota test function likhna hai. Aap use apne WhatsApp Web mein chalayein aur mujhe natija batayein ke unsaved number ko message gaya ya nahi.
2. Natija aane ke baad Phase 2 ka plan us ke mutabiq pakka karein.
3. Is dauran Phase 1 (backend aur inbound) parallel mein ban sakta hai, kyunki wo is Spike par depend nahi karta.

Jab aap tayyar hon to Agent mode mein jaa kar bata dein ke pehle Spike chahiye ya Phase 1 backend.

---

**User**

yani pulgin ma fallback ha abhi implementation karta ha agr error aya tu dakhta ha 

---

**Cursor**

## Jawab

**Haan, lagbhag sahi.** Ek zaroori farq hai: plugin mein jo fallback pehle se hai, wo aur hai. Unsaved number ke liye jis fallback ki baat ho rahi hai, wo abhi plan mein nahi likha.

### Jo fallback pehle se hai (maujooda code aur plan)

Maujooda plugin mein send karne ke do raaste hain:
1. WhatsApp ke internal functions se send karna (pehla raasta).
2. Wo fail ho to DOM fallback (`triggerDomComposeFallback`). Yani WhatsApp ke page par type box mein text likh kar send button dabana.

Plan mein bhi likha hai: "internal send fail -> DOM fallback -> `SEND_FAILED`" (section 14.9).

Lekin ye fallback **us chat ke liye hai jo pehle se khuli ya maujood ho**.

### Jo abhi plan mein nahi hai

Unsaved aur naye number ke liye **naya chat banane ka fallback**. Plan mein sirf itna hai:

```text
Chat dhoondo, nahi hai to banao.
```

Ye "banao" kaise hoga, iska backup tareeqa plan mein nahi likha. `web.whatsapp.com/send?phone=NUMBER` wala link meri suggestion thi. Plan mein nahi hai.

### Aap ka tareeqa theek hai

Aap kehna chahte hain: "Pehle implement karte hain, error aaye to us waqt dekhenge." Ye is cheez ke liye munasib hai, kyunki:
- WhatsApp ke internal functions ka behavior asli WhatsApp Web par chal kar hi pata chalta hai.
- Sochne se zyada tezi se pata chal jata hai ke kya chalta hai.

### Do cheezein dhyan mein rakhein

1. **Fallback code mein pehle se dalna behtar hai.** Jab main naya chat banane ka code likhunga, to pehla tareeqa (internal function) aur doosra tareeqa (`/send?phone=` link) dono saath likh dunga. Phir aap ko error aane par dobara code nahi likhwana padega.
2. **Plan update karna hoga.** Jo tareeqa asli WhatsApp par kaam kare, wo section 8.8 aur 11 mein likh dena chahiye. Warna plan aur code mein farq reh jayega.

Ye sab Agent mode mein hoga. Wahan jaa kar bata dein ke Spike (chhota test) se shuru karna hai.

---

**User**

11. Spike (Phase 2 ke shuru mein, code se pehle research)


plan ma ya section ha i think ya hoa ha define

---

**Cursor**

## Jawab

**Haan, aap sahi hain. Spike section plan mein maujood hai aur define bhi hai.** Section 11 mein (line 1126 se 1135).

### Section 11 mein kya likha hai

Ye "Phase 2 ke shuru mein, code se pehle research" ka section hai. Is mein 5 cheezein hain jo abhi pakki nahi pata, aur asli WhatsApp Web par verify karni hongi:

1. Unknown number ka "WhatsApp par hai ya nahi" check, naya chat banana, send.
2. WhatsApp ki connection state ka signal (`connected / offline / logged_out`).
3. Message ack level kaise padhein.
4. LID masla (`@lid` identifier).
5. Interactive buttons ho sakte hain ya nahi.

Aakhir mein likha hai: Spike ka natija ek chhoti report hogi ("ye kaam karta hai / nahi"), aur uske mutabiq section 8.8 adjust hoga.

### Jo section 11 mein define hai aur jo nahi

| | Section 11 mein hai? |
|---|---|
| **Kya check karna hai** (5 cheezein) | Haan, define hai |
| Spike **kab** hoga (Phase 2 ke shuru mein, code se pehle) | Haan |
| Spike ka **natija** kya hoga (ek report) | Haan |
| Naya chat banane ka **fallback** (agar internal function kaam na kare) | **Nahi.** Section 11 sirf "check karo" kehta hai, "agar fail ho to ye karo" nahi likha. |

Section 8.8 ke step 5 mein sirf "chat dhoondo, nahi hai to banao" likha hai. Ye "banao" kaise hoga, wo bhi wahan nahi likha.

### Meri baat aur aap ki baat mein farq

Pichle jawab mein maine kaha tha ke fallback plan mein nahi hai. Wo baat ab bhi sahi hai. Lekin aap ka kehna bhi sahi hai ke Spike ka section already maujood hai. Dono baatein saath saath theek hain: **research define hai, backup tareeqa define nahi.**

### Agar aap chahein

Agar section 11 mein ek chhoti line jod di jaye to plan mukammal ho jata hai, jaise:

```text
Agar internal function se naya chat nahi banta -> web.whatsapp.com/send?phone=NUMBER wala tareeqa try karo.
Dono fail -> job failed, aur Cloud API ka option dekhein.
```

Ye edit Agent mode mein hoga. Abhi sirf plan mein ye ek line ka farq hai, koi bara masla nahi.

---

**User**

plan pora ok ha baas whatsup new chat ka fall back daalo baki kuch change nhi karna aur ak aur baat yr jo testing ka chatbot bi banao ga wo bi fastapi ma step ho node js ka kuch nhi chaye sb professional code ho is ka ilawa plan ma kuch change na ho 

@VENDOR_GATEWAY_PLAN.md  tmna current is directory ka plan ma change kiya na mujhe batao sirf kiya change kiya bhot bara plann ma dobara nhi parho ga lakin mujhe surity do ka tmna baki kuch chnage nhi kiya

---

**Cursor**

Maine `VENDOR_GATEWAY_PLAN.md` mein **abhi tak kuch bhi change nahi kiya**. Us file mein ab tak koi edit nahi hui.

**Is ka saboot:**
- File ka last-modified time **20:10:52** hai. Ye wohi waqt hai jab maine pichli dafa poori file update ki thi (jo maine pehle bataya tha).
- Ab waqt 21:34 hai. Is ke baad file mein koi tabdeeli nahi hui.
- File ke total lines 1917 hi hain (jitni IDE mein dikh rahi thin).

**Maine kya kiya:**
Plan mode mein mujhe files edit karne ki ijazat nahi thi. Isliye maine sirf ek alag chhoti plan file banayi: `~/.cursor/plans/plan_edits_fallback,_fastapi_bot_bcdd1b78.plan.md`. Is mein sirf likha hai ke main kya edit karunga. Aap ki asli plan file ko chhua nahi gaya.

**Jo edits plan mein likhi hain (abhi hui nahi):**
1. **Section 8.8 step 5:** naye chat ka fallback (primary, phir `/send?phone=`, phir fail).
2. **Section 11 (Spike) aur 14.9:** fallback ki ek ek line.
3. **Line ~1376 aur ~1399:** "Python + Node reference bots" ko "reference bot (FastAPI)" karna.
4. **Naya chhota section 7.5:** FastAPI test bot ka folder structure.
5. **Section 3.5 (line ~246) aur 17.C:** `make bot` wali ek line.

Baqi plan mein kuch change nahi hoga. Vendor contract ke Node samples (section 17.A) bhi nahi chhede jayenge. Wo vendors ke liye hain, hamare bot ke liye nahi.

Agar ye list theek hai to Plan ko confirm kar dein ya mujhe bata dein, phir main ye 4-5 edits karunga. Edit ke baad main aap ko exact lines dikha dunga ke kya badla.

---

**User**

is current directory ka plan ma add karo aur mujhe batao ka tmna kiya change kiya ha aur baki sb waasa hi na 

---

**Cursor**

Ab diff nikaal kar check karta hoon ke sirf yehi badla hai.

`VENDOR_GATEWAY_PLAN.md` mein edits ho gayi hain. Maine kaam shuru karne se pehle file ki backup copy bana li thi aur ab dono ka `diff` kiya hai. Sirf neeche likhi tabdeeliyan hui hain. Baqi poori file bilkul pehle jaisi hai.

### Tabdeeliyan

**1. WhatsApp naye chat ka fallback**

| Jagah | Kya badla |
|---|---|
| **Section 8.8, step 5** (line ~927) | Pehle ek line thi "Chat dhoondo, nahi hai to banao". Ab 5a se 5d hain. Chat pehle se ho to kholo. Nahi ho to internal function se naya chat banao. Wo fail ho to `web.whatsapp.com/send?phone=NUMBER` kholo. Dono fail hon to `failed` aur `SEND_FAILED`. Reload ka rule bhi likha hai: `chat_opening` stage mein reload ho to ek baar safe resume, `sending` stage mein ho to `unknown` aur retry nahi. |
| **Section 11 Spike, item 1** (line ~1158) | Fallback ki ek line aur Spike report mein kya likhna hai, wo line add ki. |
| **Section 14.9 tests** (line ~1362) | Unsaved number ka ek test add kiya. |

Maine naya error code nahi banaya. Fallback fail ho to purana `SEND_FAILED` hi use hota hai, isliye vendor contract mein koi tabdeeli nahi hui.

**2. Test chatbot FastAPI mein, Node nahi**

| Jagah | Kya badla |
|---|---|
| **Naya section 7.5** (line ~585) | `reference-bot/` ka professional folder structure: `main.py`, `config.py`, `security.py`, `schemas.py`, `handlers.py`, `tests/`, `pyproject.toml`, `Dockerfile` aur `README`. Saath mein docker-compose service aur `make bot` command. Ye bhi likha hai ke ye backend ke `mock_bot` mode se alag cheez hai. |
| **Section 15, step 6 aur todo list** (lines ~1407 aur ~1430) | "Python + Node reference bots" ki jagah "FastAPI reference bot" likha. |
| **Section 3.5 Step C** (line 246) | Bot chalane wali line mein `make bot` ka zikr jodha. |
| **Section 17.C** (line ~1900) | Vendor ko dikhane ke liye ek line add ki ke hamari FastAPI reference bot ek chalta hua example hai. |

### Jo nahi badla

Baqi poora plan wahi hai. Maine in cheezon ko haath nahi lagaya: phases, outbound design, pacing, admin, magic numbers, fake WhatsApp aur section 17 ka baqi hissa.

Ek cheez maine jaan boojh kar rehne di hai. Vendor contract (17.A) ke Node.js samples aur Python/Node signature tests wahi hain. Wo vendors ke liye hain, hamare test bot ke liye nahi. Agar aap un mein se bhi Node hatana chahte hain to bata dein.

Backup `/tmp/VENDOR_GATEWAY_PLAN.before.md` mein hai. Wahan se aap khud bhi `diff` chala kar dekh sakte hain.

---

**User**

yani current directory wala plan updated ha na 

---

**Cursor**

Haan, current directory wala plan updated hai. File ka path ye hai:

`/home/shakeeb/Desktop/hnh/complete_backup/hnh/upcomming_projects/whatsup-plugin/VENDOR_GATEWAY_PLAN.md`

Pehle is mein 1917 lines thin, ab 1949 hain. Ye 32 lines naye fallback aur FastAPI bot wali edits ki hain. Aap ki IDE mein bhi total lines 1949 dikh rahi hain, yani IDE wahi updated file dikha rahi hai.

Cursor ki alag plan file `~/.cursor/plans/plan_edits_fallback,_fastapi_bot_bcdd1b78.plan.md` sirf edits ki list thi. Asli plan wahi `VENDOR_GATEWAY_PLAN.md` hai, aur wo ab update ho chuki hai.

---

**User**

yr tm current directory ma ai-contract.md ki file bana ka sb us ma mention kar sakta ho kiya ka vendor ko input kasa mila ga aur usko output kasa dana ha sb detail ma easy english ma q ka ya md ma vendor ko donga taka wo is ko parha ka easily implementation kar saka 

---

**Cursor**

`ai-contract.md` bana di hai. Path: `/home/shakeeb/Desktop/hnh/complete_backup/hnh/upcomming_projects/whatsup-plugin/ai-contract.md`

Maine `VENDOR_GATEWAY_PLAN.md` ya kisi aur file ko haath nahi lagaya.

Ye document vendor ko dene ke liye hai. Iska content plan ke section 17.A se liya hai. Usko easy English mein, ek hi file mein, alag se likha hai.

**Is mein kya hai:**

| Section | Kya hai |
|---|---|
| 1, 2 | Poori tasveer: customer, gateway, vendor ka bot. Hum vendor ko kya dete hain aur vendor humein kya deta hai. |
| 3 | **Input:** headers, request body, har field ka matlab. `incomingMessages` ek list hoti hai, is liye 7 second ke andar ke saare messages ek request mein aate hain aur vendor ko ek hi combined jawab dena hai. |
| 4 | **Messages ki 5 qismein:** text, image, voice note, document, sticker, har ek ka JSON example. Saath mein bari file ka case (`base64Data: null`) aur Base64 ki aasan wazahat. |
| 5 | **Security:** signature kaise banti hai aur kaise verify karni hai. Python aur Node ke code, aam ghaltiyan, secret rotation. |
| 6 | **Output:** `{ "replies": [...] }` ka format, sahi aur ghalat misalein. Max 5 bubbles, 4000 characters. `replies: []` ka matlab chup rehna hai. Ghalti ho to kya hota hai. Time limit 25 second (30 par timeout). |
| 7 | Bot ke andar ka recommended flow aur FastAPI ka poora sample. |
| 8 | Go-live se pehle test: ek script jo khud signed request bana kar bot ko bhejti hai, 12 test cases ki table, aur `simulate-incoming` ka zikr. |
| 9, 10, 11 | Limits ki table, go-live checklist, FAQ. |

Isko vendor ko bhejne se pehle in baaton ka khayal rakhein:

- **Node samples:** is vendor document mein Node.js ke samples (signature aur Base64) rakhe hain. Aap ne Node ko hamare test bot ke liye mana kiya tha. Vendor kisi bhi language mein bot bana sakta hai, isliye maine unhe rehne diya. Agar aap chahte hain ke ye bhi nikal dein to bata dein.
- **Sirf `/ai-reply`:** outbound API (OTP, marketing) is file mein nahi hai. Wo alag document hoga. File ke aakhir mein ye likha hai.
- **Wo cheezein jo abhi bani nahi:** `simulate-incoming`, test key aur bot secret abhi sirf plan mein hain. Document mein likha hai "when our system is ready we will give you". Vendor ko bhejte waqt ye baat saaf bata dein.
- **Test script:** section 8.1 ki script `httpx` use karti hai aur maine isko chalaya nahi hai, kyunki abhi hamara reference bot bana hi nahi.

---

**User**

plugin haar request ma X-API-Key bhaja ga na backend ko ? short answer do

---

**Cursor**

Haan. Plugin backend ko jo bhi HTTP request bhejta hai (whoami, `/ai-reply` wagera), us mein `X-API-Key` header hota hai. Isi se backend pehchanta hai ke ye kaun sa vendor hai.

Socket.IO connection (Phase 2) mein header ki jagah **connect ke waqt auth payload** mein key jati hai. Wahan har event ke saath key dobara nahi bheji jati, sirf connect par check hoti hai.

Plugin sirf key bhejta hai. Backend vendor ka bot URL aur secret apne paas se uthata hai, plugin ko nahi batata.

---

**User**

agr X-API-Key onboarding ka wakt thk thi lakin abhi admin na revoke kardi tu backend usko reject karaga aur user ko koi message nhi jaye ga na ?

---

**Cursor**

Haan, plan ke hisaab se aisa hi hoga.

**Inbound (customer ka message):**
1. Plugin `/ai-reply` bhejta hai, backend revoked key dekh kar `401 INVALID_KEY` deta hai.
2. Bot ko request jati hi nahi.
3. Plugin ko `401`/`403` mile to wo **customer ko kuch nahi bhejta**, popup mein "Last error" dikhata hai, aur dobara try nahi karta (plan section 6).

**Socket.IO (Phase 2, outbound):**
- Revoke karte hi chalta hua socket foran band ho jata hai (section 9 aur 14.6).
- Us vendor ki `queued` jobs `cancelled` ho jati hain.
- Naye connect par bhi key reject hoti hai.

**Do baatein dhyan mein rakhein:**
- Jo job pehle se `sending` mein ho (plugin WhatsApp par bhej raha ho), uska natija plan mein alag se define nahi hai. Wo ek chhota edge case hai.
- Plan mein `suspended` vendor ka rule alag hai: `403` aur plugin pause. Key revoke aur vendor suspend dono ka asar milta julta hai, lekin ye alag cheezein hain.

---

**User**

acha vendor outbound call pa hamara gate way sa direct phala db ma jaye gi job then celeery ma na ? 

---

**Cursor**

Haan, bilkul. Pehle DB, phir Celery.

```text
Vendor --> POST /v1/message-to-customer
              |
              1. Key check, Idempotency-Key check, validation, daily cap check
              2. Job Postgres mein likhi (status = queued)   <-- COMMIT
              3. Vendor ko foran 202 { jobId, status: "queued" }
              4. Uske baad Celery task: dispatch_next(vendor_id)
```

**DB pehle kyun:** Postgres hi source of truth hai. Pehle job likhi jati hai, aur commit hone ke baad hi Celery ko bataya jata hai. Isse job kabhi gum nahi hoti.

**Agar commit ke baad Celery ko batane se pehle server crash ho jaye:** job DB mein `queued` rehti hai. Plan ka Celery Beat (har 30 second) queued jobs ko dhoond kar `dispatch_next` dobara chala deta hai (section 8.5). Isi liye ye safe hai.

**Celery job ke saath kya karta hai:**
1. `dispatch_next(vendor_id)` DB se us vendor ki sab se upar wali job dekhta hai (OTP pehle).
2. Redis ke pace keys dekhta hai ke gap guzar gaya ya nahi.
3. Gap theek ho to job claim karta hai (`queued` se `dispatched`).
4. Socket.IO ke zariye vendor ke plugin ko bhejta hai.

Celery ko pura data nahi milta. Wo sirf `vendor_id` leta hai aur baqi sab DB se padhta hai.

---

**User**

then celery process kar ka direct websocket call karta ha ya redis ka through call karta ha ?

---

**Cursor**

Celery direct websocket call nahi karta. Wo **Redis ke zariye** karta hai.

```text
Celery worker --(Redis pe likhta hai)--> Redis --> API process (Socket.IO server) --> plugin
```

**Kyun:**
- Websocket connection API process (FastAPI + Socket.IO server) ke paas hota hai. Celery worker ek alag process hai, us ke paas plugin ka socket nahi hota.
- Isliye plan mein Socket.IO ka **Redis manager** use hota hai (`AsyncRedisManager`). Celery sirf Redis par "is vendor ke room mein ye event bhejo" likhta hai (write-only).
- API process Redis se wo event uthata hai aur `vendor:{id}` room ke zariye sahi plugin ko bhej deta hai.

**Faida:** agar API ke kai processes chal rahe hon, to bhi event sahi process tak pohanch jata hai, jahan us vendor ka plugin connected hai.

**Agar plugin online na ho:** job `dispatched` nahi hoti, `queued` rehti hai. Plugin ke wapas connect hone par agla `dispatch_next` usay utha leta hai.

Plugin job ka `job_ack` aur `job_result` Socket.IO ke zariye wapas API process ko bhejta hai, aur wahan se DB update hota hai.

---

**User**

thora easy kar ka arrow diagran sa batao

---

**Cursor**

```mermaid
flowchart LR
  vendor[Vendor] -->|"1. POST message-to-customer"| api[API process]
  api -->|"2. job likho, status queued"| db[(Postgres)]
  api -->|"3. turant 202 jobId"| vendor
  api -->|"4. dispatch_next vendor_id"| celery[Celery worker]
  celery -->|"5. job padho, claim karo"| db
  celery -->|"6. event likho, Redis ke zariye"| redis[(Redis)]
  redis -->|"7. event uthao"| sio[Socket.IO server]
  sio -->|"8. job bhejo, vendor room"| plugin[Chrome plugin]
  plugin -->|"9. WhatsApp pe send"| wa[WhatsApp Web]
  plugin -->|"10. job_ack aur job_result"| sio
  sio -->|"11. status update"| db
```

**Seedhi baat:**

- **Celery** ke paas plugin ka socket nahi hota, isliye wo seedha plugin se baat nahi karta. Wo sirf **Redis par likhta hai** (step 6).
- **Socket.IO server** (API process ke andar) wo event Redis se uthata hai aur plugin ko bhejta hai (steps 7 aur 8).
- **Postgres** mein job pehle likhi jati hai (step 2). Celery sirf `vendor_id` leta hai aur baqi sab DB se padhta hai (step 5).
- Plugin ka result wapas Socket.IO se aata hai aur DB update hota hai (steps 10 aur 11).

---

**User**

yani ya kind of pub sub ha na ?

---

**Cursor**

Haan, bilkul. Socket.IO ka Redis manager andar se **Redis Pub/Sub** hi use karta hai.

```text
Celery worker  --publish-->  Redis channel  --subscribe-->  Socket.IO server(s)  -->  plugin
(publisher)                  (broker)                        (subscribers)
```

- **Publisher:** Celery worker. Wo Redis par likhta hai ke "vendor:7 ke room ko ye event bhejo".
- **Subscriber:** har API/Socket.IO process. Wo Redis ke channel sunta hai.
- **Delivery:** jis process ke paas us vendor ka plugin connected hai, wahi event ko plugin tak pohancha deta hai. Baqi processes ignore kar dete hain.

**Pub/Sub ki ek hadd:** Redis Pub/Sub **fire-and-forget** hai. Agar us waqt koi subscriber na ho ya plugin offline ho, to event kho jata hai, aur Redis use save nahi rakhta.

Plan isi wajah se safe hai. Asli sach **Postgres** mein hai, Pub/Sub sirf "ghanti bajane" ka kaam karta hai:
- Job DB mein `queued` rehti hai.
- Plugin ko event na mile (offline, ya event kho jaye) to job `dispatched` ke baad lease (30 second) expire hone par wapas `queued` ho jati hai.
- Beat (har 30 second) dobara `dispatch_next` chalata hai.

Isliye Pub/Sub ke kho jane se job gum nahi hoti, sirf thodi der se jati hai.

---

**User**

ya humna isliye kiya na q ka celery jo ha wo alag process pa tha ?

---

**Cursor**

Haan, bilkul. Yehi wajah hai.

**Masla:**
- Plugin ka websocket connection API process (FastAPI + Socket.IO server) ke paas hota hai.
- Celery worker ek **alag process** hai (aksar alag machine par bhi). Us ke paas plugin ka socket hai hi nahi.
- Is liye worker seedha `emit()` nahi kar sakta.

**Hal:** beech mein Redis rakh dein. Worker Redis par likhta hai, aur API process Redis se utha kar plugin ko bhej deta hai. Yehi Redis Pub/Sub wala raasta hai.

Agar Celery aur Socket.IO ek hi process mein hote, to Redis ki is kaam ke liye zaroorat nahi hoti.

**Phir Celery alag kyun rakha?** Kyunke ye kaam API ke andar karna theek nahi:
- Pacing (1 se 2 second ya 8 se 20 second ka gap) aur wake alarm.
- Retries, lease sweeper, Beat ka reconcile.
- Webhooks bhejna (retries ke saath).

Ye sab background kaam hain. Agar ye API ke andar chalte, to API request ko rokte ya API restart hone par kho jate. Alag worker hone se API tez rehti hai aur worker crash ho to job dobara chal jati hai (`acks_late`).

**Ek baat:** Redis yahan do alag kaam karta hai. Ek, Celery ka broker (tasks ki queue). Do, Socket.IO ka Pub/Sub. Dono ke liye ek hi Redis server chal sakta hai, lekin kaam alag hain.

