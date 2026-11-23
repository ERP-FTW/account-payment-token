# POS Payment Markup — Odoo 18

LGPL-3; original module attribution is retained in the manifest. Depends on `point_of_sale` and `account_payment_markup`, not a gateway.

## Configuration and cashier use

On a POS payment method, select the manual fee mode (Disabled, Percentage, Fixed amount), its service product, amount/rate and tax interpretation. A manual method needs no provider or MID. An unconfigured cash method remains fee-free. The legacy explicit credit-provider mapping remains a separate fallback; clear that mapping when disabling all fees. Manual configuration takes precedence over the legacy mapping. Program-enabled providers cannot trigger unverified local fallback.

Enter the **principal allocation** for each tender. For an untaxed sale of 100, cash 40 followed by a 3% configured tender creates a single fee line of 1.80 and a tender of 61.80, total 101.80. A fixed 2 fee on a single 100 tender totals 102. Removing the tender removes its generated fee; re-adding creates one new fee, not a compounded fee. The service fee is a native order line and is visible on the standard receipt.

Odoo 18 POS uses the related-model `PosPayment` and `PosOrder` APIs and the `point_of_sale._assets_pos` bundle. Fee products are included by the target product-loader hook even when not available as cashier-sale products. Backend finalization checks linked tender/fee totals. Finalized fee evidence cannot be edited; refund caps include all refund lines referring to the same original.

## Refunds

Use the native Paid Orders refund workflow and select the original merchandise and original fee lines. Their original prices/taxes are retained even if the current fee is changed or disabled. Fractional original fee quantities may be refunded, subject to the cumulative original quantity cap. Missing original fee evidence never licenses a newly invented fee. The original fee-line reference is the refund audit linkage; refund tender fields are not a replacement for that line evidence.

## Tests and limits

Run `/pos_payment_markup` on Odoo 18 after its independent installation. Native regressions cover manual modes, taxes/rounding, ordinary cashier access, original evidence and duplicate refund caps. The legacy standalone `tests/review_current.js` harness targets the original Odoo 16 source and is not Odoo 18 qualification; target-native tests and browser scenarios are required.

Synthetic cashier browser qualification covers split tender, fixed/disabled switching, removal/re-addition and an original-order refund after a settings change. Real gateway calls, merchant surcharge certification, client business UAT and deployment are outside this port's test authority.
