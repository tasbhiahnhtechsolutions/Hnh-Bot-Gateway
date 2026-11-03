# Manual checklist (ek staging WhatsApp number pe, release se pehle)

Ye wo cheezein hain jo automatic tests **nahi** parakh sakte, kyun ke un mein nakli WhatsApp
(`extension/e2e/fake-whatsapp.js`) hai. Pehle `spike/README.md` chalayein (asli test account pe),
phir ye list. Har item ke saamne likhein: pass / fail / tareekh / kisne.

**Asli WhatsApp pe kabhi apna personal number mat use karein. Alag test SIM, kam messages,
sirf apne doosre phone par bhejein.** Zyada messages ya anjaan numbers par bhejne se number ban ho sakta hai.

## A. Spike (asli WhatsApp Web ke internals, sab abhi UNVERIFIED)

Ye `spike/SPIKE_REPORT.md` mein likhna hai. Jab tak report mein "kaam karta hai" na likha ho, code mein ye
sab "andaza" hain (`extension/injected.js`, section "Outbound", har cheez UNVERIFIED ke nishan ke saath).

- [ ] Save na kiye hue number ko bhejna: **primary** tareeqa (internal chat banana, `WAWebFindChatAction`).
- [ ] Save na kiye hue number ko bhejna: **fallback** (`web.whatsapp.com/send?phone=NUMBER`): page reload hota hai? chat khulta hai?
- [ ] Number WhatsApp pe nahi hai: kya "exists" function sahi `false` deta hai? Fallback mein popup ka text/selector kya hai (`invalidNumberPopupShown`)?
- [ ] Connection state ka signal (`WAWebConnModel`): `connected / connecting / offline / logged_out` asli build mein kya naam/values hain?
- [ ] Ack level: bhejne ke baad `msg.ack` kitni der mein >= 1 hota hai? Hamari 15 second ki hadd theek hai?
- [ ] `@lid`: save na kiye hue number ka chat id `@c.us` rehta hai ya `@lid` ban jata hai? Bhejne par farq padta hai?
- [ ] `chrome.alarms` 30 s: Chrome MV3 mein service worker asal mein utni der baad jaagta hai?
- [ ] Playwright `context.setOffline` extension ke service worker ko rokta hai? (e2e test mein `skip` ho jata hai agar nahi)

## B. Connection

- [ ] WiFi 5 minute band, phir on: popup "Reconnecting..." phir "Connected"; intezar karti hui job **ek** baar jaye.
- [ ] Laptop sleep 10 minute, phir jagana: line wapas aaye, queue ke message jayein (OTP jo TTL ke andar ho).
- [ ] Airplane mode on/off.
- [ ] Chrome poora band karke dobara kholna: extension line wapas khole.
- [ ] Do computer par ek hi key: doosra "Another computer is using this key" dikhaye, pehla chalta rahe.
- [ ] Phone ka internet band (multi-device mein bhi bhejna chalna chahiye): job `sent` ho ya saaf `failed`, kabhi jhoota `sent` nahi.
- [ ] Phone se "Linked devices" mein plugin wala device hata do: popup "Logged out: scan the QR code", job `WA_NOT_READY`, kuch nahi bheja.

## C. WhatsApp tab

- [ ] WA tab band karo: popup "Open WhatsApp Web"; job intezar kare; tab wapas kholne par job jaye.
- [ ] WA tab reload (bhejte waqt nahi): sab theek.
- [ ] Chrome "Memory Saver" tab ko discard kare: tab wapas khulne par kaam jaari.
- [ ] Bhejte hue (`sending` stage) tab reload: job `unknown` ho, **dobara nahi bheja** jaye.

## D. Duplicate aur tez rafter

- [ ] Do dafa jaldi OTP bhejo (alag `Idempotency-Key`): dono jayein, ek ke baad ek, 1-2 s gap.
- [ ] Ek hi `Idempotency-Key` se 5 dafa call: sirf ek message jaye, baqi 4 ko wahi `jobId` mile.
- [ ] Marketing 10 messages: gap 8-20 s ke beech aaye; beech mein OTP bhejo: OTP foran (1-2 s) jaye.

## E. Content

- [ ] Non-WhatsApp number: `failed` + `NOT_ON_WHATSAPP`.
- [ ] Bohot lamba message (limit se thora kam aur thora zyada): sahi `MESSAGE_TOO_LONG`.
- [ ] Emoji aur Urdu text bilkul sahi dikhe (RTL, line breaks).

## F. Admin

- [ ] Vendor key revoke jab plugin chal raha ho: plugin foran band, popup "Refused", queue ke jobs `cancelled`.
- [ ] Vendor suspend: wahi, aur vendor ki API 403 `VENDOR_SUSPENDED`.
- [ ] Devices page 5 s mein update ho (online/offline, WhatsApp state).
- [ ] Admin 2FA: authenticator app (Google Authenticator / Authy) se enroll, login, ghalat code, band karna.
- [ ] Marketing daily cap badlo: nayi hadd foran lagu.

## Natija

| Tareekh | Kisne | Build / commit | Spike report | Natija |
|---|---|---|---|---|
| | | | | |
