from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountPaymentMethodLine(models.Model):
    _inherit = 'account.payment.method.line'

    markup_mode = fields.Selection([
        ('disabled', 'Disabled'), ('percentage', 'Percentage'), ('fixed', 'Fixed amount'),
    ], default='disabled', required=True, string='Manual payment fee')
    markup_percent = fields.Float(string='Fee percentage', digits=(16, 4))
    markup_fixed_amount = fields.Float(string='Fixed fee in payment currency', digits=(16, 4))
    markup_tax_included = fields.Boolean(string='Fee includes tax')
    markup_product_id = fields.Many2one('product.product', string='Fee service product',
                                       domain=[('type', '=', 'service')], check_company=True)

    def _markup_is_configured(self):
        self.ensure_one()
        return bool(self.payment_type == 'inbound' and self.markup_mode != 'disabled'
                    and self.markup_product_id)

    @api.constrains('markup_mode', 'markup_percent', 'markup_fixed_amount', 'markup_product_id', 'payment_method_id')
    def _check_manual_fee(self):
        for method in self:
            if not 0 <= method.markup_percent <= 100 or method.markup_fixed_amount < 0:
                raise ValidationError('Fee percentage must be 0–100 and fixed fee must be nonnegative.')
            if method.markup_mode == 'disabled':
                continue
            product = method.markup_product_id
            if method.payment_type != 'inbound':
                raise ValidationError('Manual fees are only supported on inbound methods.')
            if not product or product.type != 'service':
                raise ValidationError('Configure a service product for the manual fee.')
            if not (product.property_account_income_id or product.categ_id.property_account_income_categ_id):
                raise ValidationError('Configure the fee product income account.')
