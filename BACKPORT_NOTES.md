# Backport notes: Odoo 18 ➜ 17 ➜ 16

This repository contains two Odoo 18 modules:

- `payment_token_invoice` (invoice payment by saved token).
- `payment_token_partner_form` (internal tokenization flow from Contacts).

The notes below summarize version deltas that affect these modules and list the
minimal adjustments needed to run on Odoo 17 and Odoo 16.

## 1) High‑impact framework changes (v18 vs v16)

### Payment framework model renames (v17+)
Odoo 17+ renamed payment models and fields:

| Odoo 18 | Odoo 16 | Impact in these modules |
| --- | --- | --- |
| `payment.provider` | `payment.acquirer` | Controller uses `_get_compatible_providers` on `payment.provider`; must map to `_get_compatible_acquirers` on `payment.acquirer` in v16. |
| `provider_id` | `acquirer_id` | Wizard uses `token.provider_id` and creates `payment.transaction` with `provider_id`. Must use `acquirer_id` in v16. |
| `payment.method` | (no direct equivalent, or payment method is inferred) | Wizard requires `token.payment_method_id` and sets `payment_method_id` on `payment.transaction`. In v16 this field may be absent or optional. |

### Tokenization UI / controller utilities
- The controller uses `payment_utils.generate_access_token` and the
  `payment.payment_methods` QWeb template. These exist in v18, but the template
  name and helper signatures differ in v16. The flow is close to `/my/payment_method`
  in v16, but you must confirm the matching template and expected context keys.

### Transaction helpers
- The wizard uses `payment.transaction._send_payment_request()` and links
  invoices via `invoice_ids` with `Command.set`. In v16, those fields/methods
  may differ (e.g., older helper name or invoice linkage). Verify in v16 and
  adjust accordingly.

## 2) Module‑level backport changes

### Common changes for v17 and v16
These apply to **both** modules when backporting:

1. **Manifest versions**
   - Change `version` to `17.0.x.y.z` or `16.0.x.y.z`.
2. **Dependencies**
   - Ensure `payment` (and `website` if tokenization UI is kept) is available.
   - For v16, `portal` and `website` are still needed for the payment form.
3. **View targets**
   - Confirm view XML IDs (`account.view_move_form`, `base.view_partner_form`) are
     still valid in v16/17. They usually are, but verify.

### `payment_token_invoice` (wizard)
**Files involved**: `payment_token_invoice/models/account_invoice_token_wizard.py` and `payment_token_invoice/views/account_invoice_token_views.xml`.

Minimum changes for **v17**:
- **Keep** `payment.provider`, `payment.method`, and `provider_id` usage.
- Ensure `payment_method_id` is still required; in v17 it is required for token
  transactions as in v18.
- Confirm the `payment.transaction` `invoice_ids` field still exists and accepts
  `Command.set`.

Additional changes for **v16**:
- **Replace** `provider_id` with `acquirer_id` and map `token.provider_id` to
  `token.acquirer_id`.
- **Drop** or conditionalize `payment_method_id` (it may not exist in v16).
  If not supported, remove the field from `tx_vals` and from precondition checks.
- **Check** `payment.transaction` invoice linkage. If `invoice_ids` is absent in
  v16, you may need to set `reference` or use `account.payment` flows instead.
- **Confirm** method name for sending a payment request. If
  `_send_payment_request` is unavailable, use the v16 equivalent.

### `payment_token_partner_form` (controller + wizard)
**Files involved**: `payment_token_partner_form/controllers/internal_tokenize.py`,
`payment_token_partner_form/wizards/tokenize_partner_payment_method.py`, and
`payment_token_partner_form/views/tokenize_partner_payment_method_view.xml`.

Minimum changes for **v17**:
- `payment.provider` / `payment.method` APIs should remain valid, but confirm the
  signature of `_get_compatible_providers` and `_get_compatible_payment_methods`.
- Validate `payment.payment_methods` template and context keys: any change to
  template name or expected variables will break the flow.

Additional changes for **v16**:
- Replace `payment.provider` with `payment.acquirer` and map `_get_compatible_providers`
  to `_get_compatible_acquirers`.
- Replace `payment.method` selection logic: in v16, payment methods are derived
  from the acquirer; you may need to skip `_get_compatible_payment_methods` and
  pass acquirers only.
- Confirm `payment_utils.generate_access_token` signature. If it requires an
  amount/currency, pass reasonable defaults (e.g., `0.0` and company currency).
- Confirm template: in v16, the portal tokenization page is typically
  `payment.payment_acquirer_list` or similar; adjust render target and context.

## 3) Suggested minimal backport strategy

1. **Keep logic intact** and only adapt model/field/method names.
2. **Backport in layers**:
   - First move to **v17** (model renames already done in v18 so changes are small).
   - Then adapt for **v16** where renames and payment method differences are larger.
3. **Use feature checks** where feasible (e.g., `hasattr`) to avoid branching
   if you want a single codebase for 16/17/18.

## 4) Outcome evaluation checklist

For each target version:

- **Install** modules without errors (`apps` install).
- **Invoice wizard**: open invoice, launch wizard, create transaction.
- **Tokenization**: open partner form ➜ “Add Card”, ensure tokenization UI loads.
- **Regression**: ensure access control works (`base.group_user` check).
- **Logging**: confirm transaction logs and chatter posts.

If any step fails, map the stack trace back to the affected area above
(provider/acquirer, payment method, or template routing).
