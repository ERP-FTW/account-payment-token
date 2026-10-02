# CardPointe troubleshooting

| Symptom | Meaning | Action |
| --- | --- | --- |
| "Gateway credentials are not set" | merchant config has no username/password; nothing was sent | authorized operator enters them from the credential reference; run **Test Credentials** |
| Test Credentials fails, HTTP 401 | credentials do not match the MID | check the credential reference and the MID; UAT and production credentials differ |
| Test Credentials: "answered, but not as the CardPointe REST Gateway" | the base URL reaches some other server | base URL must end with `/cardconnect/rest/` |
| Return stops: "CardPointe inquiry …" | the inquiry failed or was malformed; no money moved | retry when the Gateway answers; never force a void |
| Partial return fails with *Txn not settled* | the sale is not settled yet; a partial void is not allowed after capture | repeat after settlement, or ask CardPointe to enable unsettled refunds on the MID |
| Return reports an **unknown** outcome | the void/refund got no answer and may have been applied | do not retry; look the original `retref` up in CardPointe reporting, then record the result |
| Terminal sale **unknown** | authCard got no answer and the order-id inquiry could not run | do not take another payment on that line; press Send again once the Gateway is reachable (it inquires, never charges) |
| AccessError for a cashier | not possible after this branch for start/auth/cancel/refund; report it | check the user is in Point of Sale / User and works in the method's company |

Logs: search `CardPointe gateway`, `CardPointe request`, `CardPointe authCard`, and the terminal
order id or `retref`. Card numbers and CVV are never logged.
