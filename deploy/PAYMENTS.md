# Phase 3 — Payments (hybrid: Telegram Stars + manual card transfer)

## Strategy

- **International users → Telegram Stars (XTR).** Fully automated: invoice →
  pre-checkout → `successful_payment` → fulfillment.
- **Uzbekistan users → manual card transfer.** The bot shows your card number and
  the exact UZS amount; the user pays and uploads a receipt photo; you Approve or
  Reject in Telegram; Approve runs the **same** fulfillment as Stars.

Click and Payme code remains in the repo (`bot/payments/click.py`, `payme.py`)
but is **dormant** — no UI points to it and no credentials are required. If you
ever get merchant accounts, re-enabling is just adding the buttons back.

```
                         buy mod / subscription
                 ┌───────────────┴────────────────┐
         ⭐ Telegram Stars                   💳 Card transfer (UZS)
         invoice → success                  show card + amount
                 │                          user uploads receipt photo
                 │                          status = pending_manual_verification
                 │                          receipt forwarded to ADMIN_ID
                 │                             ┌──────────┴──────────┐
                 │                         ✅ Approve            ❌ Reject
                 │                         status=paid           status=rejected
                 └──────────────┬──────────────┘                 notify user
                                ▼
                 core.fulfill  (idempotent, exactly once)
                 mod → DRM bundle if mod has a key, else plain file
                 sub → activate 30 days
```

## Payment states (`payments.status`)

`pending` → (card) `pending_manual_verification` → `paid` | `rejected`
Stars/approve flip to `paid` atomically; fulfillment is guarded by a `fulfilled`
flag so it runs **exactly once** even on retries or a double Approve tap.

## DRM-aware fulfillment (same path for Stars and card approval)

`core._fulfill_mod` checks the mod's `mod_key` (new nullable column on `mods`):

- **key set + source in `DRM_MODS_DIR/<key>` + signing key present** → issues a
  license and sends a **per-buyer obfuscated bundle** (Phase 2 bundler).
- otherwise → sends the plain `file_id`.

So a Stars purchase and an approved card transfer deliver identically.

Mark a mod as DRM-protected (admin, in the bot):
```
/setmodkey <mod_id> <mod_key>      # mod_key = the folder name under DRM_MODS_DIR
```

## Admin approval

When a receipt arrives you get a photo in your admin chat with the buyer, the
product, the amount, and two buttons:

- **✅ Approve** → `paid` + fulfillment (DRM bundle or file / subscription).
- **❌ Reject** → `rejected`, and the user is told.

Already-handled payments are guarded (a second tap says "Allaqachon: paid").

## Config (`.env`)

```
CARD_NUMBER=8600 0000 0000 0000      # your card, shown to buyers
CARD_HOLDER=Ism Familiya             # optional, shown under the number
STARS_UZS_RATE=170                   # UZS per 1 Star (tune to the Stars→USD→UZS rate)
# Click/Payme left blank — dormant
```

## Endpoints

- `POST /api/pay/start` `{ init_data, intent, provider }` — provider is `stars`
  or `card`. Stars sends the invoice in chat; card creates the payment and DMs
  the card details. `intent` = `mod:<id>` or `sub:<tier>`; the amount comes from
  the server's price list, never the client.
- `/payment/payme`, `/payment/click/*` — dormant (still signature/auth-verified
  if ever called).

## Tests

```
python tests/payments/test_payments.py    # 27 checks: Stars + (dormant) Click/Payme logic
python tests/payments/test_manual.py       # 20 checks: card flow, approve/reject, DRM delivery
```

`test_manual.py` builds a **real** DRM bundle on approval and asserts the per-buyer
zip (not a plain file) is sent and a license row is issued.

## Ready for you to test

The Admin Approval flow is ready to try end to end:

1. Set `BOT_TOKEN`, `ADMIN_ID`, and `CARD_NUMBER` in `.env`, deploy, start the bot.
2. From a second Telegram account, `/upgrade` → **Karta orqali (UZS)** (or open a
   priced mod in the Mini App → **Karta orqali**).
3. Send any photo as the "receipt" to the bot.
4. On your admin account you'll get the receipt with Approve / Reject.
5. **Approve** → the buyer receives the mod/subscription. For a DRM mod (one with
   a `mod_key`), they get their personal bundle.

### Still needs you
- **Real card number** in `.env` (placeholder `8600 0000 0000 0000` is set).
- **Live Stars test**: one real Stars purchase to confirm end to end.
- **Decide refunds**: a rejected/cancelled payment does not claw back an
  already-delivered mod or downgrade a subscription — tell me if you want that.
