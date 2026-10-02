from odoo import _, api, fields, models

from odoo.addons.payment_cardpointe_base.services.gateway import CardPointeGatewayClient


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

    def action_test_credentials(self):
        """Validate the Gateway credentials against this MID. Nothing is authorized or charged."""
        self.ensure_one()
        result = CardPointeGatewayClient(self.sudo()).test_credentials(self.mid)
        if result.get('ok'):
            message = _("CardPointe accepted the credentials for MID %(mid)s at %(url)s.",
                        mid=self.mid, url=self.gateway_base_url)
        else:
            message = _("CardPointe did not accept the credentials (HTTP %(status)s): %(detail)s",
                        status=result.get('http_status') or '-',
                        detail=(result.get('message') or result.get('text') or '')[:200])
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("CardPointe Credentials"),
                'message': message,
                'type': 'success' if result.get('ok') else 'danger',
                'sticky': False,
            },
        }
