import re
import uuid
from urllib.parse import urlencode

from odoo import fields, models
from odoo.addons.payment import utils
from odoo.exceptions import ValidationError

from ..services.surcharge import agreement, money, quote_fee, UnusableSurcharge


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    cp_quote_id = fields.Char(readonly=True, copy=False)
    cp_quote_at = fields.Datetime(readonly=True, copy=False)
    cp_quote_binding = fields.Char(readonly=True, copy=False, groups='base.group_system')
    cp_quote = fields.Json(readonly=True, copy=False)
    cp_auth_evidence = fields.Json(readonly=True, copy=False)
    cp_receipt = fields.Json(readonly=True, copy=False)
    cp_fee_hold = fields.Boolean(readonly=True, copy=False)

    def _cp_quote_fingerprint(self, token, postal):
        # Keyed digest, not a stored gateway token or a reversible postal record.
        return utils.generate_access_token(self.id, token, postal, self.amount,
            self.currency_id.id, self.payment_method_id.id, self._cp_current_gateway_identity(),
            self.provider_id.cardpointe_merchant_config_id.mid, self._cp_uses_program())

    def _cp_prepare_quote(self, token, postal, quote_id):
        p = self.provider_id
        if not isinstance(postal, str) or not re.fullmatch(r'\d{5}(?:-\d{4})?', postal):
            raise ValidationError('Enter the cardholder postal code (12345 or 12345-6789).')
        if self.currency_id.name != 'USD':
            raise ValidationError('The surcharge integration requires USD.')
        fingerprint = self._cp_quote_fingerprint(token, postal)
        if quote_id:
            age = (fields.Datetime.now() - self.cp_quote_at).total_seconds() if self.cp_quote_at else -1
            if (quote_id != self.cp_quote_id or fingerprint != self.cp_quote_binding
                    or not 0 <= age <= 300 or not self.cp_quote):
                self.write({'cp_quote_id': False, 'cp_quote_binding': False, 'cp_quote': False})
                return {'state': 'quote_error', 'message': 'Quote changed or expired. Request a new quote before paying.'}
            return None
        self.write({'cp_quote_id': False, 'cp_quote_binding': False, 'cp_quote': False})
        try:
            response = p._cp_online_request('GET', 'surcharge', {
                'merchid': p.cardpointe_merchant_config_id.mid, 'token': token,
                'postal': postal, 'amount': '%.2f' % self.amount})
            if not isinstance(response, dict) or not response.get('ok'):
                raise UnusableSurcharge('No usable gateway quote.')
            quote = quote_fee(response.get('data'))
        except Exception:
            return {'state': 'quote_error', 'message': 'Gateway quote unavailable. No authorization was sent.'}
        quote.update(subtotal='%.2f' % self.amount,
                     total=str(money('%.2f' % self.amount) + money(quote['fee'])), currency=self.currency_id.name)
        self.write({'cp_quote_id': uuid.uuid4().hex, 'cp_quote_at': fields.Datetime.now(),
                    'cp_quote_binding': fingerprint, 'cp_quote': quote})
        return {'state': 'quote', 'quote_id': self.cp_quote_id, 'quote': quote}

    def _cp_uses_program(self):
        return self.payment_method_id._uses_cardpointe_program(self.provider_id)

    def _cp_prepare_authorization(self, token, options):
        result, payload = super()._cp_prepare_authorization(token, options)
        if not self._cp_uses_program():
            if self.cp_quote:
                raise ValidationError('The payment method changed after the quote. Restart checkout.')
            return result, payload
        if self.markup_amount:
            raise ValidationError('The payment method changed after disclosure. Restart checkout.')
        postal = options.get('postal')
        result = self._cp_prepare_quote(token, postal, options.get('quote_id'))
        return result, dict(payload, postal=postal)

    def _cp_apply_response(self, response):
        self.env.cr.execute('SELECT id FROM payment_transaction WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.cp_fee_hold:
            return
        data = response.get('data') if isinstance(response, dict) and response.get('ok') else None
        if self.cp_quote and isinstance(data, dict) and self.state not in ('done', 'error', 'cancel', 'authorized'):
            evidence = {'approved': data.get('respstat') == 'A',
                        'identity_matches': str(data.get('merchid')) == self.cp_merchant_id and
                                            data.get('orderid') == self.cp_order_id}
            for key in ('amount', 'fee_amount'):
                try:
                    evidence[key] = str(money(data.get(key)))
                except UnusableSurcharge:
                    evidence[key] = None
            self.cp_auth_evidence = evidence
        super()._cp_apply_response(response)

    def _cp_accept_amount(self, data):
        if not self.cp_quote and not any(key.startswith('fee_') for key in data):
            return super()._cp_accept_amount(data)
        try:
            receipt = agreement(data, '%.2f' % self.cp_submitted_amount,
                                quoted_fee=self.cp_quote['fee'] if self.cp_quote else None,
                                required=bool(self.cp_quote))
            if not self.cp_quote and money(receipt['fee']):
                raise UnusableSurcharge('Undisclosed fee.')
        except UnusableSurcharge:
            self.cp_fee_hold = True
            return False
        if self.cp_quote:
            self.cp_receipt = dict(receipt, reference=data['retref'], order_id=self.cp_order_id,
                                   currency=self.cp_currency_id.name, agreement=True)
        return True

    def _cp_process(self, *args, **kwargs):
        result = super()._cp_process(*args, **kwargs)
        if self.cp_receipt:
            access_token = utils.generate_access_token(
                self.reference, self.partner_id.id, self.amount, self.currency_id.id, self.provider_id.id)
            result['redirect_url'] = '/payment/cardpointe/receipt?' + urlencode({
                'reference': self.reference, 'access_token': access_token})
        return result
