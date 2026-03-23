from odoo import api, fields, models


class CardPointeMerchantConfig(models.Model):
    _name = 'cardpointe.merchant.config'
    _description = 'CardPointe Merchant Configuration'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    mid = fields.Char(required=True, string='Merchant ID (MID)')
    gateway_base_url = fields.Char(required=True, default='https://fts-uat.cardconnect.com/cardconnect/rest/')
    gateway_username = fields.Char(required=True)
    gateway_password = fields.Char(required=True, groups='base.group_system')
    tokenizer_url = fields.Char()
    debug_logging = fields.Boolean()
    timeout_connect = fields.Integer(default=10)
    timeout_read = fields.Integer(default=30)

    def _normalize_base_url(self, base_url):
        base_url = (base_url or '').strip()
        if base_url and not base_url.endswith('/'):
            base_url = f"{base_url}/"
        return base_url

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('gateway_base_url'):
                vals['gateway_base_url'] = self._normalize_base_url(vals['gateway_base_url'])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('gateway_base_url'):
            vals['gateway_base_url'] = self._normalize_base_url(vals['gateway_base_url'])
        return super().write(vals)
