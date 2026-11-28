from odoo import fields, models


class PaymentMethod(models.Model):
    _inherit = 'payment.method'

    use_cardpointe_program = fields.Boolean('Use CardPointe surcharge', company_dependent=True)

    def _uses_cardpointe_program(self, provider):
        return (self.code == 'card' and provider.code == 'cardpointe'
                and self.sudo().with_company(provider.company_id).use_cardpointe_program)

    def _checkout_markup(self, provider, amount, currency, partner, document=None):
        if self._uses_cardpointe_program(provider):
            return {}
        return super()._checkout_markup(provider, amount, currency, partner, document)
