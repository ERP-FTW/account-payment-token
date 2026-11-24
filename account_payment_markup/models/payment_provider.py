from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.calculation import calculate_markup, calculate_taxed_markup


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    markup_enabled = fields.Boolean(string='Enable card payment markup', default=False)
    markup_percent = fields.Float(string='Markup percentage', digits=(16, 4), default=0.0)
    markup_tax_included = fields.Boolean(string='Markup rate is tax included', default=False)
    markup_product_id = fields.Many2one(
        'product.product', string='Markup service product',
        domain=[('detailed_type', '=', 'service')],
    )

    def _markup_portal_disclosure(self, amount, currency, partner):
        """Return the server-authoritative fee breakdown for checkout."""
        self.ensure_one()
        empty = {
            'enabled': False, 'base_amount': currency.round(amount or 0.0),
            'net_amount': 0.0, 'tax_amount': 0.0, 'gross_amount': 0.0,
            'total_amount': currency.round(amount or 0.0),
        }
        if not self._markup_is_configured():
            return empty
        details = calculate_markup(
            amount, self.markup_percent, currency.round, eligible=True,
        )
        fee_amount = details['amount']
        net_amount = fee_amount
        tax_amount = 0.0
        gross_amount = fee_amount
        product = self.markup_product_id
        if product and product.taxes_id and fee_amount:
            tax_values = calculate_taxed_markup(
                fee_amount, product.taxes_id, currency, product=product,
                partner=partner, rate_included=self.markup_tax_included,
            )
            net_amount = tax_values['net_amount']
            tax_amount = tax_values['tax_amount']
            gross_amount = tax_values['gross_amount']
        return {
            'enabled': True, 'base_amount': currency.round(amount or 0.0),
            'net_amount': net_amount, 'tax_amount': tax_amount,
            'gross_amount': gross_amount,
            'total_amount': currency.round((amount or 0.0) + gross_amount),
        }

    @api.constrains('markup_enabled', 'markup_percent', 'markup_product_id')
    def _check_markup_configuration(self):
        for provider in self:
            product = provider.markup_product_id
            if provider.markup_percent < 0 or provider.markup_percent > 100:
                raise ValidationError('Markup percentage must be between 0 and 100.')
            if not provider.markup_enabled:
                continue
            if not product or product.detailed_type != 'service':
                raise ValidationError('A service product is required when markup is enabled.')
            if product.company_id and product.company_id != provider.company_id:
                raise ValidationError('Markup product and provider must belong to the same company.')
            if not product.property_account_income_id and not product.categ_id.property_account_income_categ_id:
                raise ValidationError('Configure an income account on the markup product or its category.')

    def _markup_is_configured(self):
        self.ensure_one()
        return bool(self.markup_enabled and self.markup_percent >= 0 and self.markup_product_id)

    def _markup_card_eligible(self, card_type=None, evidence_verified=False,
                              processor_surcharge_present=None, evidence_trusted=False):
        """Accept configured manual fees or verified processor evidence.

        Selecting a configured provider is the explicit eligibility decision
        for the manual flow.  If an adapter supplies processor evidence, keep
        the stricter server-trust contract and never fall back to the manual
        decision for incomplete or caller-controlled evidence.
        """
        if (card_type is None and not evidence_verified and
                processor_surcharge_present is None and not evidence_trusted):
            return True
        server_evidence = self.env.context.get('server_trusted_markup_evidence') is True
        if (not server_evidence or not evidence_trusted or not evidence_verified or
                processor_surcharge_present is not False):
            return False
        return str(card_type or '').strip().lower() in ('credit', 'credit_card')
