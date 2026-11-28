"""Validate processor-owned fees; never calculate a surcharge from a rate.

Online eligibility and terminal authorization are different protocols. See
docs/surcharge-program.md for the authoritative field map.
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
