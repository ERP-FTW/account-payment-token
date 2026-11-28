from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from odoo import fields, models

ACCOUNT_CLASSES = {
    'consumer_checking': ('Consumer checking', 'ECHK', 'WEB'),
    'consumer_savings': ('Consumer savings', 'ESAV', 'WEB'),
    'business_checking': ('Business checking', 'ECHK', 'CCD'),
    'business_savings': ('Business savings', 'ESAV', 'CCD'),
}
CONSENT_VERSION = 'one-time-ach-v1'


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    # Retain historical configuration on existing databases. Checkout uses the chosen account type.
    cp_ach_agreement_ref = fields.Char(string='Signed company agreement evidence reference (CCD)', groups='base.group_system')
    cp_ach_consumer_checking = fields.Boolean(string='Enable consumer checking (WEB)')
    cp_ach_consumer_savings = fields.Boolean(string='Enable consumer savings (WEB)')
    cp_ach_business_checking = fields.Boolean(string='Enable business checking (CCD)')
    cp_ach_business_savings = fields.Boolean(string='Enable business savings (CCD)')

    def _cp_ach_options(self):
        self.ensure_one()
        return [(key, info[0]) for key, info in ACCOUNT_CLASSES.items()]

    def _cp_ach_consent(self, amount, currency):
        return ('I authorize %s to initiate a one-time ACH debit of %.2f %s from the bank account entered in the secure Fiserv form. This authorization applies only to this payment. Gateway acceptance does not mean bank funding is complete.' % (self.company_id.name, amount, currency.name))

    def _cp_tokenizer_url(self, method_code):
        url = super()._cp_tokenizer_url(method_code)
        if method_code != 'cardpointe_ach':
            return url
        parts = urlsplit(url)
        params = dict(parse_qsl(parts.query))
        params.update(fullmobilekeyboard='true', cardnumbernumericonly='false', useexpiry='false', usecvv='false', cardlabel='Routing number / Bank account number')
        return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(params),''))
