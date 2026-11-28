# Online CardPointe surcharge

For a card method using the merchant program, checkout requests a quote from the gateway’s `surcharge` endpoint using the merchant ID, token, postal code, and principal. The customer sees the fee and total before confirming. An explicitly waived fee is zero.

The authorization sends the original principal. CardPointe applies the fee. Odoo records the subtotal, actual fee, total, and reference from the processor receipt. If the authorization outcome is uncertain, checkout looks up the existing order before another charge can be attempted.
