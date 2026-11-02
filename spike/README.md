# Phase 2 Spike: asli WhatsApp Web pe kya chalta hai?

Plan section 11. Outbound (customer ko message bhejna) abhi **nahi bana**, kyunke WhatsApp ke internal functions
hum ne asli build pe check nahi kiye. Ye kit aap (insaan) ko chalani hai, phir report wapas dena hai. Us ke baad hi
section 8.8 adjust hoga aur Phase 2 aage chalega.

## Pehle ye parh lein (safety)

- **Sirf TEST account aur TEST number use karein.** Asli business number ya asli customers nahi.
  WhatsApp Web ko script se chalana unke terms ke khilaf ho sakta hai; **account ban hone ka khatra hai.**
- Script sirf `window.WASpike` banati hai. Ismein **koi network code nahi** (kuch bahar nahi jata).
  Padhne wali sab cheezein read-only hain. **Sirf `send()` aur `tryInteractive()` asli message bhejte hain**, aur
  wo `{confirm:'SEND'}` ke baghair chalte hi nahi. Ek page load mein maximum **5** sends.
- Report mein numbers sirf aakhri 4 digits (`*******4567`) dikhte hain; message text aur chat ke naam kabhi nahi aate.
  Phir bhi report paste karne se pehle ek dafa khud parh lein.
- Is folder ka koi secret, API key ya OTP kahin paste na karein.

## Zaroorat

- Chrome/Chromium mein `https://web.whatsapp.com` pe **test account logged in**.
- Ek doosra **test number** jo WhatsApp pe hai (aap ka apna dusra phone), aur ek number jo WhatsApp pe **nahi** hai.
- Number format: country code ke saath, sirf digits, jaise `923001234567` (`+` ya space nahi).
- Koi aur WhatsApp Web tab kholi hui na ho. Gateway ka plugin extension is browser mein **install na ho** (warna dono ek doosre ko hook karenge).

## Chalane ka tareeqa

1. web.whatsapp.com kholen, chats load hone dein.
2. DevTools console kholen (`F12` > Console). Pehli dafa paste ke liye Chrome `allow pasting` likhwa sakta hai, likh dein.
3. `spike/spike.js` ki poori file copy karke console mein paste karein, Enter. `[Spike] loaded` dikhna chahiye.
4. Neeche ke steps **is tarteeb** mein. Har step ka jawab (`console` ka output ya `WASpike.report()`) report mein jata hai.
   Jo step fail ho, usay **fail likhein, chhupayein nahi**; ye khud ek nateeja hai.

### Step A: sab se pehle, kuch bhejay baghair

```js
await WASpike.runSafe('92300XXXXXXX')   // test number
```

Ye dekhta hai: kaun se internal modules is build mein hain, apna number, connection ka snapshot, `exists`, `@lid` chats.

### Step B: connection state (plan item 2)

```js
await WASpike.watchConnection(120)
```

Ye 120 second tak state ke badlav log karta hai. Is dauran **ek ek karke** karein (har baar 15 se 20 second ruk kar):
1. Laptop ka internet band, phir chalu.
2. Phone ka internet band (WhatsApp Web "Phone not connected" / "Computer not connected" dikhaye), phir chalu.
3. (Alag run mein) Phone se Linked devices > is browser ko **log out** karein. Pehle `watchConnection(60)` chala dein.

Report mein likhein ke har haalat mein `socketState`, `streamMode` etc. kya badle.

### Step C: number WhatsApp pe hai ya nahi (item 1)

```js
await WASpike.exists('92300XXXXXXX')   // wo number jo WhatsApp pe hai
await WASpike.exists('92300YYYYYYY')   // wo number jo WhatsApp pe nahi
```

### Step D: naya chat kholna, baghair bheje

```js
await WASpike.openChat('92300XXXXXXX')                 // chat object banata hai, UI nahi kholta
await WASpike.openChat('92300XXXXXXX', { open: true }) // UI mein chat kholta hai
```

Report mein: **kya page reload hua?** (`window.__spikeMarker` gayab ho jaye to reload hua.)

### Step E: asli send + ack (item 1 aur 3), test number pe

```js
await WASpike.send('92300XXXXXXX', 'spike test 1', { confirm: 'SEND', ackSeconds: 30 })
```

Ye ek asli message bhejta hai aur 30 second tak ack ki timeline likhta hai (`1` = bheja, `2` = server/receiver tak,
`3` = delivered, `4` = read; asli maani report mein aap tasdeeq karein: phone pe tick dekh kar match karein).
Do chizein alag alag test karein: (a) pehle se maujood chat, (b) bilkul naya number jis se kabhi chat nahi hui.
Dono mein result likhein, aur ye bhi ke message phone pe sach mein pohcha ya nahi.

### Step F: fallback tareeqa (item 1)

Agar Step E (b) fail ho (naye number pe send nahi hota), same tab mein ye URL kholein (**page reload hoga**, script phir paste karni hogi):

`https://web.whatsapp.com/send?phone=92300XXXXXXX`

Chat khulne ke baad `WASpike.report()` ke saath dekhein ke send mumkin hai. Phir **WhatsApp pe na honay wale number** ke
liye wohi URL kholein, popup aaye to console mein:

```js
WASpike.detectInvalidPopup()
```

Ye popup ka text (nahi, sirf hai/nahi) aur uske DOM ki shakal (`role`, `data-*`, class ke naam) batata hai. Report mein
likhein: kaun sa tareeqa chalta hai, reload hua ya nahi, aur popup pehchanne ka tareeqa.

### Step G: `@lid` (item 4)

```js
await WASpike.lid('92300XXXXXXX')
```

Report mein: kitne chats `@lid` ke hain, kitno ka asli number nikal saka, aur number se chat dhoondne mein masla aya ya nahi.
Agar `@lid` chat wale kisi dost se message mangwa sakein to inbound ka sender number bhi dekhein.

### Step H: buttons/polls (item 5, experimental, Phase 3 ke liye)

```js
await WASpike.interactive()   // kya modules maujood hain (read-only)
await WASpike.tryInteractive('92300XXXXXXX', 'poll', { confirm: 'SEND' })
```

Phone pe dekh kar likhein ke wo tappable buttons/poll ki tarah dikha ya nahi, aur kya wahan bhi ye cheez receiver ko
dikhi. Ye step chhor bhi sakte hain; Phase 2 isse nahi rukta.

## Report

```js
WASpike.copyReport()    // clipboard mein JSON (numbers masked)
```

`spike/SPIKE_REPORT.md` ka template bhar kar wapas dein: har item ke saamne **kaam karta hai / nahi / aadha**, aur
upar wala JSON neeche paste karein. Phir hum plan 8.8 adjust karenge.

## Is kit ke apne tests

```
make spike-test
```

Ye sirf kit ki hifazati wadon ka test hai (bina confirm send nahi, 5 ki hadd, numbers masked, koi network code nahi).
Ye **WhatsApp pe kuch sabit nahi karta**; wo sirf asli account pe chal kar pata chalega.
