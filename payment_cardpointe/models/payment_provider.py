# Part of Odoo. See LICENSE file for full copyright and licensing details.
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from odoo import models
from odoo.exceptions import ValidationError


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    def _get_default_payment_method_codes(self):
        return ['card'] if self.code == 'cardpointe' else super()._get_default_payment_method_codes()

    def _cp_tokenizer_url(self, method_code):
        self.ensure_one()
        cfg = self.cardpointe_merchant_config_id
        url = cfg.tokenizer_url if cfg else self.cardpointe_tokenizer_url
        parts = urlsplit(url or '')
        if parts.scheme != 'https' or not parts.hostname or not parts.hostname.endswith('.cardconnect.com') or parts.username or parts.password:
            raise ValidationError('Configure the HTTPS Fiserv hosted tokenizer before online checkout.')
        params = dict(parse_qsl(parts.query))
        params.update(enhancedresponse='true', tokenizewheninactive='true', inactivityto='1000')
        return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(params),''))

    def _cp_online_request(self, method, endpoint, payload=None):
        self.ensure_one()
        return self.with_context(cardpointe_online_private=True)._cardpointe_request(method, endpoint, payload=payload)
