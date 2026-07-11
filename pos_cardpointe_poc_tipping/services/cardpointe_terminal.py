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


def _tip_percent_presets(config):
    presets = [int(config.tip_percent_1 or 0), int(config.tip_percent_2 or 0)]
    if config.tip_presets_count in ('3', '4'):
        presets.append(int(config.tip_percent_3 or 0))
    if config.tip_presets_count == '4':
        presets.append(int(config.tip_percent_4 or 0))

    if config.tip_allow_custom:
        # CardPointe tip endpoint supports at most 3 presets when custom amount is enabled.
        presets = presets[:3]

    return [str(preset) for preset in presets]


def build_tip_payload(config, amount_dollars, prompt=None):
    return {
        'merchantId': config.merchant_id,
        'hsn': config.device_serial,
        'amount': dollars_to_implied_cents(amount_dollars),
        'prompt': (prompt or config.tip_prompt or '').strip() or 'Select tip amount',
        'includeAmountDisplay': bool(config.tip_include_amount_display),
        'includeCustomTipAmount': bool(config.tip_allow_custom),
        'tipPercentPresets': _tip_percent_presets(config),
    }


def tip_with_session(self, session_key, amount_dollars, prompt=None):
    payload = build_tip_payload(self.config, amount_dollars, prompt=prompt)

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
