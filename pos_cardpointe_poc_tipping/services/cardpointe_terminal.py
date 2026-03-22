from decimal import Decimal, ROUND_HALF_UP

from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import (
    CardPointeTerminalClient,
    dollars_to_implied_cents,
)


def _parse_tip_amount_dollars(data):
    payload = data or {}
    raw = payload.get('tip') or payload.get('tipAmount') or payload.get('tipamount')
    if raw in (None, ''):
        return Decimal('0.00')

    s = str(raw).strip()
    if '.' in s:
        try:
            return Decimal(s).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except Exception:
            return Decimal('0.00')

    try:
        cents = Decimal(int(s))
    except Exception:
        return Decimal('0.00')

    return (cents / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def tip_with_session(self, session_key, amount_dollars, prompt=None):
    implied_amount = dollars_to_implied_cents(amount_dollars)
    tip_prompt = (prompt or '').strip() or 'Select tip amount'

    payload = {
        'merchantId': self.config.merchant_id,
        'hsn': self.config.device_serial,
        'amount': implied_amount,
        'prompt': tip_prompt,
        'includeAmountDisplay': True,
        'includeCustomTipAmount': bool(self.config.tip_allow_custom),
        'tipPercentPresets': [
            str(int(self.config.tip_percent_1 or 10)),
            str(int(self.config.tip_percent_2 or 15)),
            str(int(self.config.tip_percent_3 or 20)),
        ],
    }

    result = self._request(
        'POST',
        '/v3/tip',
        payload=payload,
        session_key=session_key,
        timeout=max(20, self.config.request_timeout_seconds or 120),
    )
    if not result.get('ok'):
        return result

    data = dict(result.get('data') or {})
    tip_amount = self._parse_tip_amount_dollars({'tip': data.get('tip')})

    message = data.get('resptext') or data.get('errorMessage') or data.get('message') or ''
    error_code = str(data.get('errorCode') or '')
    status_text = str(data.get('status') or '').lower()

    if error_code == '8' or 'cancel' in message.lower() or status_text in {'cancelled', 'canceled'}:
        return {
            'ok': False,
            'status': 'cancelled',
            'message': message or 'Tip cancelled.',
            'tip_amount': float(tip_amount),
            'raw': data,
        }

    if result.get('http_status') != 200:
        return {
            'ok': False,
            'status': 'error',
            'message': message or 'Tip failed.',
            'tip_amount': float(tip_amount),
            'raw': data,
        }

    return {
        'ok': True,
        'status': 'ok',
        'tip_amount': float(tip_amount),
        'raw': data,
    }


CardPointeTerminalClient._parse_tip_amount_dollars = staticmethod(_parse_tip_amount_dollars)
CardPointeTerminalClient.tip_with_session = tip_with_session
