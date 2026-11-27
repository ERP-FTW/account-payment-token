from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PaymentMethodFee(models.Model):
    _inherit = 'payment.method.fee'

    use_cardpointe_program = fields.Boolean(string='Use CardPointe surcharge')
    provider_code = fields.Selection(related='provider_id.code')
    method_code = fields.Char(related='method_id.code')

    @api.onchange('use_cardpointe_program')
    def _onchange_program(self):
        if self.use_cardpointe_program:
            self.markup_percent = 0

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([
            dict(vals, markup_percent=0) if vals.get('use_cardpointe_program') else vals
            for vals in vals_list
        ])

    def write(self, vals):
        if vals.get('use_cardpointe_program'):
            vals = dict(vals, markup_percent=0)
        return super().write(vals)

    @api.constrains('use_cardpointe_program', 'markup_percent', 'method_id', 'provider_id')
    def _check_program(self):
        for fee in self.filtered('use_cardpointe_program'):
            if fee.provider_code != 'cardpointe' or fee.method_code != 'card' or fee.markup_percent:
                raise ValidationError('Use the CardPointe program on a CardPointe card method with no manual rate.')


class PaymentMethod(models.Model):
    _inherit = 'payment.method'

    def _uses_cardpointe_program(self, provider):
        return self.code == 'card' and provider.code == 'cardpointe' and self._markup_fee(provider).use_cardpointe_program
