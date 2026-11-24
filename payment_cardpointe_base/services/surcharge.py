"""Validate processor-owned fees; never calculate a surcharge from a rate.

Online eligibility and terminal authorization are different protocols. See
payment_cardpointe/docs/surcharge-program.md for the authoritative field map.
"""
import re
from decimal import Decimal


class UnusableSurcharge(ValueError):
    pass


def money(value):
    # Fee amounts are documented in major units, unlike terminal request amounts.
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,11}(?:\.\d{1,2})?', str(value)):
        raise UnusableSurcharge('Missing or malformed monetary evidence.')
    return Decimal(str(value)).quantize(Decimal('0.01'))


def quote_fee(data):
    if not isinstance(data, dict) or data.get('messages') not in (None, [], {}):
        raise UnusableSurcharge('Gateway quote is unusable.')
    card, postal = data.get('token'), data.get('postal')
    if card not in ('CREDIT', 'DEBIT') or postal not in ('RESTRICTED', 'NOT_RESTRICTED'):
        raise UnusableSurcharge('Gateway eligibility is unknown.')
    ineligible = card == 'DEBIT' or postal == 'RESTRICTED'
    # Documented ineligibility is authoritative; fee_amount is only returned
    # for CREDIT + NOT_RESTRICTED. Missing fee on an eligible card is NOT zero.
    fee = money(data['fee_amount']) if 'fee_amount' in data else (Decimal('0.00') if ineligible else None)
    if fee is None or (ineligible and fee != 0):
        raise UnusableSurcharge('Missing or contradictory gateway fee.')
    return {'fee': str(fee), 'card_type': card, 'postal_status': postal}


def agreement(data, subtotal, quoted_fee=None, required=True):
    """Return a sanitized receipt only for internally consistent gateway data."""
    if not isinstance(data, dict):
        raise UnusableSurcharge('Missing authorization evidence.')
    subtotal, total = money(subtotal), money(data.get('amount'))
    if not required and not any(k.startswith('fee_') for k in data):
        fee, kind = Decimal('0.00'), ''
    else:
        fee, kind = money(data.get('fee_amount')), data.get('fee_type')
        if kind not in ('SURCHRG', 'SURCHRG_WAIVED'):
            raise UnusableSurcharge('Unknown fee type.')
        if kind == 'SURCHRG_WAIVED' and fee != 0:
            raise UnusableSurcharge('Waived fee is nonzero.')
        if 'fee_format' in data and data['fee_format'] != ('percent' if kind == 'SURCHRG' else ''):
            raise UnusableSurcharge('Contradictory fee format.')
        if 'fee_value' in data:
            rate = str(data['fee_value'])
            if not re.fullmatch(r'\d{1,5}', rate) or (kind == 'SURCHRG_WAIVED' and int(rate) != 0) or (fee > 0 and int(rate) == 0):
                raise UnusableSurcharge('Contradictory fee rate evidence.')
        if data.get('fee_retref') or data.get('fee_merchid'):
            raise UnusableSurcharge('Separate fee transaction is not a surcharge.')
        if data.get('fee_authcode') and data.get('fee_authcode') != data.get('authcode'):
            raise UnusableSurcharge('Fee authorization differs.')
    if total != subtotal + fee or (quoted_fee is not None and fee != money(quoted_fee)):
        raise UnusableSurcharge('Quote, authorization and receipt do not agree.')
    return {'subtotal': str(subtotal), 'fee': str(fee), 'total': str(total), 'fee_type': kind}
