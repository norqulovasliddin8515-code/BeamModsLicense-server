"""
payment.py — RETIRED (Phase 3).

The payment logic moved to the unified, tested `bot.payments` package
(core / click / payme / stars). The old skeleton here had no signature check on
Click complete, skipped Payme auth when the header was absent, and was not
idempotent. It is intentionally left empty so a stale import fails loudly rather
than running insecure code.
"""
raise ImportError("bot.handlers.payment is retired — use bot.payments.{core,click,payme,stars}")
