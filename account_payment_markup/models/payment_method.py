from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.calculation import calculate_markup, calculate_taxed_markup


class PaymentMethod(models.Model):
    _inherit = 'payment.method'

    markup_fee_ids = fields.One2many('payment.method.fee', 'method_id', string='Payment fees')

    def _markup_fee(self, provider):
        self.ensure_one()
        return self.env['payment.method.fee'].sudo().with_company(provider.company_id).search([
            ('provider_id', '=', provider.id), ('method_id', '=', self.id)], limit=1)


class PaymentMethodFee(models.Model):
    _name = 'payment.method.fee'
    _description = 'Online Payment Method Fee'
    _rec_name = 'provider_id'

    method_id = fields.Many2one('payment.method', required=True, ondelete='cascade', index=True)
    provider_id = fields.Many2one('payment.provider', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='provider_id.company_id', store=True)
    markup_percent = fields.Float(string='Manual rate (%)', digits=(16, 4))
    markup_product_id = fields.Many2one(
        'product.product', string='Fee product', domain=[('type', '=', 'service')], check_company=True,
        default=lambda self: self.env.ref('account_payment_markup.product_payment_fee'))
    markup_tax_included = fields.Boolean(string='Rate includes tax')
    _method_provider_unique = models.Constraint(
        'unique(method_id, provider_id)', 'This provider already has a fee setting for this method.')

    @api.constrains('markup_percent', 'markup_product_id')
    def _check_manual_rate(self):
        for fee in self:
            if not 0 <= fee.markup_percent <= 100:
                raise ValidationError('Enter a manual fee between 0 and 100 percent.')
            if fee.markup_percent and (not fee.markup_product_id or fee.markup_product_id.type != 'service'):
                raise ValidationError('Select a service product for the fee.')

    def _markup_details(self, amount, currency, partner):
        rate = self.markup_percent if self else 0.0
        details = calculate_markup(amount, rate, currency.round, eligible=bool(rate))
        net = gross = details['amount']
        tax = 0.0
        if gross and self.markup_product_id.taxes_id:
            product = self.markup_product_id
            taxes = calculate_taxed_markup(net, product.taxes_id, currency, product=product,
                                           partner=partner, rate_included=self.markup_tax_included)
            net, tax, gross = taxes['net_amount'], taxes['tax_amount'], taxes['gross_amount']
        details.update(net_amount=net, tax_amount=tax, gross_amount=gross, amount=gross,
                       total=currency.round(details['base_amount'] + gross))
        return details
