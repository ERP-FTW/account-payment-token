# Manual fees with CardPointe Online (Odoo 18)

This LGPL-3 bridge auto-installs only when `account_payment_markup` and
`payment_cardpointe` are both installed. Accounting-only manual fees do not
require a gateway or POS. Use the companion online adapter revision providing
`_cp_document_amount`, `_cp_manual_markup_conflict` and `_cp_checkout_amount`
(PR #24 commit `720171919378682002dcb3cacb93ef45825df1df` or a descendant).
Bridge version: `18.0.1.0.0`. Refresh Apps, install the two dependencies normally,
and verify this bridge is installed; deployments with a restricted addon inventory
must expose it explicitly. The ACH inline template derives visible consent and its
submitted amount from the same method-specific disclosure.

The configured provider percentage remains a **manual ACH fee** even when the
merchant's card surcharge program is enabled. Program cards do not receive a
local fallback fee; CardPointe's quote/approval agreement owns their program fee.
The legacy program switch also remains fail-closed for unverified cards.

For a posted invoice, the bridge validates the separate frozen fee receivable
before allowing the original invoice ceiling to be checked against principal.
The authorization, stored submission and ACH consent still use the full disclosed
total. It does not bypass original-document locks, pending-attempt guards,
company/currency checks or original-amount ceilings. A changed amount, fee
invoice or tax/account fingerprint is refused before authorization.

Odoo 18's native `_create_payment` posts and links the fee invoice before
reconciliation. Repeated processing does not create another payment or fee.
Definite declines cancel their draft fee; unknown outcomes retain it for inquiry.

Regression suite: `account_payment_markup.tests.test_online_manual_fee`, executed
with both online and ACH addons plus this bridge installed. All transport is
fenced with synthetic responses; passing lab tests are not merchant certification.
