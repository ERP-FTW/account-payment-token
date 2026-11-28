from odoo import fields, models


class PaymentMethod(models.Model):
    _inherit = 'payment.method'

    markup_percent = fields.Float('Markup (%)', company_dependent=True, digits=(16, 4))
    markup_product_id = fields.Many2one(
        'product.product', string='Fee product', company_dependent=True, check_company=True,
        domain=[('type', '=', 'service')],
    )

    def _checkout_markup(self, provider, amount, currency, partner, document=None):
        """Use the fee product's normal Odoo taxes for both disclosure and collection."""
        method = self.sudo().with_company(provider.company_id)
        product = method.markup_product_id
        if not product or method.markup_percent <= 0 or amount <= 0:
            return {}
        unit = currency.round(amount * method.markup_percent / 100)
        if not unit:
            return {}
        position = (document.sudo().fiscal_position_id if document else
                    method.env['account.fiscal.position']._get_fiscal_position(partner))
        taxes = position.map_tax(product.taxes_id._filter_taxes_by_company(provider.company_id))
        fee = taxes.compute_all(unit, currency=currency, product=product, partner=partner)['total_included']
        return {'product': product, 'unit': unit, 'taxes': taxes,
                'fee': fee, 'total': currency.round(amount + fee)}
