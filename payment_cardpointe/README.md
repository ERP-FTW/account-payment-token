# CardPointe Payment (payment_cardpointe)

This module adds the minimum e-commerce checkout flow for CardPointe using the hosted iFrame
Tokenizer. It depends on `payment_cardpointe_base` for configuration and API helpers.

## Configuration

1. Install and configure **CardPointe Base** fields on the provider.
2. Set the **Hosted iFrame Tokenizer URL**.
3. Enable the provider for website checkout.

## Testing a checkout

- Add a product to cart and proceed to checkout.
- Select **CardPointe** as the payment method.
- Complete the secure iframe card form, then click **Pay**.

## Logs and diagnostics

- Tokenization errors happen in the browser and prevent token creation.
- Gateway errors are logged with `[CARDPOINTE]` and include a `correlation_id`.

## Distinguishing failures

- **Tokenizer failure**: no token received; UI shows a tokenization error.
- **Gateway failure**: server returns error and transaction state is set to error.

## Developer Mode

Example `curl` call to the process route (requires a valid transaction reference and access token):

```bash
curl -X POST https://your-odoo.test/payment/cardpointe/process \
  -H "Content-Type: application/json" \
  -d '{"reference": "SO123-1", "partner_id": 12, "access_token": "...", "token": "tok_..."}'
```

Tail logs for correlation IDs:

```bash
tail -f odoo.log | rg "\[CARDPOINTE\]"
```


## Sanity checklist

- Module dependency: install `payment_cardpointe_base` before/with this addon.
- Gateway requests (`inquire`, `void`, `refund`, `auth`) continue using Basic auth user/password.
- Endpoint semantics and payload shapes remain unchanged from existing gateway flow.
- Sensitive headers remain redacted in debug logs.
