# POS CardPointe POC Tipping — Odoo 18

Adds CardPointe terminal tip selection to the current Odoo 18 `pos_cardpointe_poc` module.

## Behavior

1. Odoo verifies that native POS tipping and a Tip Product are configured.
2. CardPointe displays the tip prompt.
3. CardPointe authorizes the original payment amount plus the selected tip.
4. The POS calls Odoo's native `set_tip()` method, creating/updating the configured tip-product line.
5. The order is marked `after_payment_tipping_set = true`, including when the customer chooses zero tip.
6. Tip and base amounts are saved on `pos.payment` for reporting.

## Compatibility changes from the earlier v18 draft

- Uses config-backed active request state from the current v18 base module.
- Accepts the current `start()` and `auth()` route arguments.
- Patches the exported `CardPointePOC` class instead of replacing the payment interface.
- Preserves manual entry, refunds, cancel handling, diagnostics, signatures, and current CardPointe fields.
- Restores the missing `pos_order` model import so tip values persist.
- Uses the Odoo native tip product rather than a parallel accounting mechanism.

## Required configuration

- Restaurant/POS tipping enabled.
- A Tip Product configured on the POS.
- CardPointe Terminal Config with **Enable Tips** selected.

Manual iframe entry intentionally does not prompt for terminal tips.
