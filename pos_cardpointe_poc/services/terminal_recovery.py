"""Resolve a terminal payment whose outcome is unknown.

When the terminal `authCard` call gets no answer (timeout, dropped connection, server error) the
card may or may not have been charged. CardPointe documents `inquireByOrderid` on the Gateway for
exactly this case: it returns the transaction created for the order id sent with the
authorization, if one exists. This module turns that answer into one of:

- ``approved``  -- a charge stands for this order id; record it, never charge again;
- ``declined``  -- the authorization was declined; a new attempt is a new payment;
- ``voided``    -- a charge was created and then voided (the terminal service voids an
                   authorization it could not confirm); nothing stands;
- ``not_found`` -- the Gateway has no transaction for this order id; nothing was charged;
- ``unknown``   -- the inquiry itself failed or was ambiguous; nothing may be retried yet.
"""
import secrets
import string
from decimal import Decimal, InvalidOperation

_NOT_FOUND_CODES = {'29'}
_ORDER_ID_ALPHABET = string.ascii_uppercase + string.digits
# The Integrated Terminal API documents orderId as alphanumeric, at most 19 characters, unique.
TERMINAL_ORDER_ID_LENGTH = 19


def new_terminal_order_id():
    """Return a fresh order id that satisfies the terminal API's documented format."""
    return ''.join(secrets.choice(_ORDER_ID_ALPHABET) for _i in range(TERMINAL_ORDER_ID_LENGTH))


def parse_gateway_amount(value):
    """Return a Decimal in major units from a Gateway amount.

    CardPointe documents Gateway amounts as either a decimal ("10.50") or an integer in minor
    units ("1050"). Anything else is None.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    if '.' not in text:
        parsed = parsed / Decimal('100')
    return parsed.quantize(Decimal('0.01'))


def _is_not_found(data):
    code = str(data.get('respcode') or '').strip()
    text = str(data.get('resptext') or '').strip().lower()
    return code in _NOT_FOUND_CODES or 'not found' in text


def _candidates(data):
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and data:
        return [data]
    return []


def resolve_terminal_outcome(gateway_client, merchid, order_id, requested_amount=None):
    """Ask the Gateway what happened to the authorization sent with ``order_id``."""
    base = {'order_id': order_id}
    try:
        response = gateway_client.inquire_by_orderid(order_id, merchid)
    except Exception as exc:  # noqa: BLE001 - any failure leaves the outcome unknown
        return dict(base, status='unknown', message=f'CardPointe inquiry failed: {exc}')

    if not isinstance(response, dict):
        return dict(base, status='unknown', message='CardPointe inquiry returned no answer.')
    data = response.get('data')
    rows = _candidates(data)

    if response.get('ok') is False:
        if response.get('http_status') and any(_is_not_found(row) for row in rows):
            return dict(base, status='not_found', message='No CardPointe transaction exists for this payment.')
        return dict(base, status='unknown', message=response.get('message') or 'CardPointe inquiry failed.')

    if not rows:
        return dict(base, status='unknown', message='CardPointe inquiry returned no transaction data.')
    if all(_is_not_found(row) for row in rows):
        return dict(base, status='not_found', message='No CardPointe transaction exists for this payment.')

    standing = [
        row for row in rows
        if str(row.get('respstat') or '').upper() == 'A'
        and str(row.get('setlstat') or '').strip().lower() != 'voided'
    ]
    if len(standing) > 1:
        return dict(base, status='unknown', message='Several CardPointe transactions carry this order id.')
    if standing:
        row = standing[0]
        amount = parse_gateway_amount(row.get('amount'))
        result = dict(
            base,
            status='approved',
            retref=row.get('retref'),
            authcode=row.get('authcode'),
            respcode=row.get('respcode'),
            resptext=row.get('resptext'),
            amount=f"{amount:.2f}" if amount is not None else None,
            token=row.get('token'),
            entrymode=row.get('entrymode'),
            recovered=True,
        )
        if not row.get('retref') or amount is None:
            return dict(base, status='unknown', message='CardPointe reports an approval without a reference or amount.')
        # Odoo's requested amount is always in major units.
        try:
            expected = Decimal(str(requested_amount)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            expected = None
        result['amount_matches'] = expected is None or amount == expected
        return result

    if any(str(row.get('setlstat') or '').strip().lower() == 'voided' for row in rows):
        return dict(base, status='voided', message='The authorization was voided; no charge stands.')
    if any(str(row.get('respstat') or '').upper() in ('B', 'C') for row in rows):
        row = rows[0]
        return dict(
            base, status='declined', respcode=row.get('respcode'), resptext=row.get('resptext'),
            message=row.get('resptext') or 'Declined.',
        )
    return dict(base, status='unknown', message='CardPointe inquiry returned an unrecognised status.')
