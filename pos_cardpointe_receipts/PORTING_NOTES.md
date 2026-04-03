# POS CardPointe Receipts (v18) Porting Notes

- Receipt reprint uses `POST /v3/printReceipt` with `orderId`. The API lookup key is the POS order identifier, not `retref`.
- POS references may be stored as values like `Order 00010-001-0002`; this must be normalized to `00010-001-0002` before calling `printReceipt`.
- The connect response can include session header metadata (for example `;expires=...`). The session key is normalized by stripping any suffix before reusing it on subsequent requests.
