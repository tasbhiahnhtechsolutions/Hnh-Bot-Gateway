# Spike report

Tarikh: ____  Browser/version: ____  WhatsApp Web build (`WASpike.env().waVersion`): ____
Account: TEST (haan / nahi)   Kisne chalaya: ____

Jawab sirf teen mein se: **kaam karta hai** / **nahi karta** / **aadha** (aur kyun). Jo nahi chala wo wesa hi likhein.

| # | Sawal | Nateeja | Tafseel / kaise pata chala |
|---|-------|---------|----------------------------|
| 1a | Number WhatsApp pe hai ya nahi (`exists`) | | |
| 1b | Naya chat banana baghair bheje (`openChat`) | | |
| 1c | Naye (kabhi chat na hui) number ko internal function se send | | Phone pe pohcha? Page reload hua? |
| 1d | Maujooda chat mein send | | |
| 1e | Fallback `/send?phone=` se send | | Page reload hua? |
| 1f | Invalid number popup ki pehchan | | Selector / role / text pattern |
| 2 | Connection state signal | | Kaun si property, kaun si values: laptop offline / phone offline / logged out ke waqt |
| 3 | Message ack level | | Ack timeline (ms) aur har number ka matlab |
| 4 | `@lid` mapping | | kitne chats `@lid`, kitno ka number mila, number se chat dhoondne mein masla |
| 5 | Buttons / polls | | Receiver ko tappable dikha? (Phase 3) |
| 6 | Playwright `context.setOffline` extension service worker pe asar karta hai? | | (baad mein dev khud check karega; spike mein na likhein) |

## Kya chala jo hum ne socha nahi tha

(khali chhor sakte hain)

## `WASpike.report()` JSON

```json
```
