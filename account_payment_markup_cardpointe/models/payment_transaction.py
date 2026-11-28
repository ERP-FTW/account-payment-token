import re
from urllib.parse import urlencode

from odoo import fields, models
from odoo.addons.payment import utils
from odoo.exceptions import ValidationError

from ..services.surcharge import money, quote_fee, UnusableSurcharge


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    cp_quote_id = fields.Char(readonly=True, copy=False)
    cp_quote = fields.Json(readonly=True, copy=False)
    cp_auth_evidence = fields.Json(readonly=True, copy=False)
    cp_receipt = fields.Json(readonly=True, copy=False)

    def _cp_quote_key(self, token, postal, fee):
        # Keyed digest, not a stored gateway token or a reversible postal record.
        return utils.generate_access_token(self.id, token, postal, self.amount,
            self.currency_id.id, self.payment_method_id.id, self.provider_id.id,
            self.provider_id.cardpointe_merchant_config_id.mid or self.provider_id.cardpointe_mid, fee)

    def _cp_prepare_quote(self, token, postal, quote_id):
        p = self.provider_id
        if not isinstance(postal, str) or not re.fullmatch(r'\d{5}(?:-\d{4})?', postal):
            raise ValidationError('Enter the cardholder postal code (12345 or 12345-6789).')
        if self.currency_id.name != 'USD':
            raise ValidationError('The surcharge integration requires USD.')
        if quote_id and self.cp_quote and quote_id == self.cp_quote_id:
            if quote_id == self._cp_quote_key(token, postal, self.cp_quote['fee']):
                return None
        # A changed card, postal code or total gets a fresh quote in this same checkout.
        self.write({'cp_quote_id': False, 'cp_quote': False})
        try:
            response = p._cp_online_request('GET', 'surcharge', {
                'merchid': p.cardpointe_merchant_config_id.mid or p.cardpointe_mid, 'token': token,
                'postal': postal, 'amount': '%.2f' % self.amount})
            if not isinstance(response, dict) or not response.get('ok'):
                raise UnusableSurcharge('No usable gateway quote.')
            quote = quote_fee(response.get('data'))
        except Exception:
            return {'state': 'quote_error', 'message': 'Gateway quote unavailable. No authorization was sent.'}
        quote.update(subtotal='%.2f' % self.amount,
                     total=str(money('%.2f' % self.amount) + money(quote['fee'])), currency=self.currency_id.name)
        self.write({'cp_quote_id': self._cp_quote_key(token, postal, quote['fee']), 'cp_quote': quote})
        return {'state': 'quote', 'quote_id': self.cp_quote_id, 'quote': quote}

    def _cp_uses_program(self):
        return self.payment_method_id._uses_cardpointe_program(self.provider_id)

    def _cp_prepare_authorization(self, token, options):
        result, payload = super()._cp_prepare_authorization(token, options)
        if self.markup_amount or not (self.cp_quote or self._cp_uses_program()):
            return result, payload
        postal = options.get('postal')
        result = self._cp_prepare_quote(token, postal, options.get('quote_id'))
        return result, dict(payload, postal=postal)

    def _extract_amount_data(self, payment_data):
        amount_data = super()._extract_amount_data(payment_data)
        if self.provider_code == 'cardpointe' and self.cp_quote and amount_data:
            # CardPointe collects the program fee. Odoo's receivable is the subtotal.
            try:
                total = money(payment_data.get('amount'))
                fee = money(payment_data.get('fee_amount', '0.00'))
                amount_data['amount'] = float(total - fee)
            except UnusableSurcharge:
                amount_data['amount'] = 0
        return amount_data

    def _apply_updates(self, payment_data):
        super()._apply_updates(payment_data)
        if self.provider_code == 'cardpointe' and self.cp_quote and self.state == 'done':
            self.cp_receipt = {
                'subtotal': '%.2f' % self.amount,
                'fee': str(money(payment_data.get('fee_amount', '0.00'))),
                'total': str(money(payment_data['amount'])),
                'currency': self.currency_id.name, 'reference': self.provider_reference,
                'order_id': self.cp_order_id,
            }

    def _cp_process(self, *args, **kwargs):
        result = super()._cp_process(*args, **kwargs)
        if self.cp_receipt:
            access_token = utils.generate_access_token(
                self.reference, self.partner_id.id, self.amount, self.currency_id.id, self.provider_id.id)
            result['redirect_url'] = '/payment/cardpointe/receipt?' + urlencode({
                'reference': self.reference, 'access_token': access_token})
        return result
