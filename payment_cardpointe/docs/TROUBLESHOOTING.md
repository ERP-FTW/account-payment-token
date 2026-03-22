# CardPointe Ecommerce Troubleshooting

## Tokenizer problems

- **Iframe blocked**: check Content Security Policy and mixed-content issues.
- **postMessage origin mismatch**: ensure the tokenizer URL origin matches the expected origin.
- **Token not returned**: verify the tokenizer configuration and confirm the iframe loads.

## Gateway problems

- **Auth failure**: check API username/password and MID.
- **Decline**: verify test card behavior and account settings in the UAT portal.
- **Timeout**: increase connect/read timeouts or verify network access.

## Logs and correlation

Check `[CARDPOINTE]` logs for `correlation_id` and the transaction reference to match client and
server activity.
