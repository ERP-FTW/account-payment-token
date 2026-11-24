from odoo import api, fields, models

from odoo.addons.account_payment_markup.services.calculation import (
    UNSET, calculate_markup, calculate_taxed_markup,
)


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    markup_provider_id = fields.Many2one(
        'payment.provider', string='Markup provider',
        # Company isolation is enforced by _markup_provider; company_id is
        # restricted in Odoo 16 views and cannot be referenced by this domain.
        domain="[('markup_enabled', '=', True)]",
        help='Explicit provider used for this POS tender. An empty mapping is fee-free.',
    )

    markup_card_type = fields.Selection([
        ('credit', 'Credit card'), ('unknown', 'Unknown / not eligible'),
    ], default='unknown', required=True,
        help='Explicit manual eligibility. Unknown methods remain fee-free.')
    markup_enabled = fields.Boolean(compute='_compute_markup_provider_values')
    markup_percent = fields.Float(compute='_compute_markup_provider_values', digits=(16, 4))
    markup_product_id = fields.Many2one('product.product', compute='_compute_markup_provider_values')
    markup_tax_included = fields.Boolean(compute='_compute_markup_provider_values')

    def _compute_markup_provider_values(self):
        for method in self:
            provider = method._markup_provider()
            method.markup_enabled = bool(provider and provider._markup_is_configured())
            method.markup_percent = provider.markup_percent if provider else 0.0
            method.markup_product_id = provider.markup_product_id if provider else False
            method.markup_tax_included = provider.markup_tax_included if provider else False

    def _markup_provider(self):
        self.ensure_one()
        # Cashiers cannot read payment providers (which may hold secrets).
        # Project only this mapped provider's fee configuration, retaining the
        # company/eligibility checks below; do not grant provider ACL access.
        provider = self.markup_provider_id.sudo()
        if (not provider or provider.company_id != self.company_id or
                not provider._markup_is_configured() or
                self.markup_card_type != 'credit' or
                self.is_cash_count):
            return self.env['payment.provider']
        return provider

    def _markup_preview(self, base_amount, currency=None, evidence_verified=False,
                        card_type=None, processor_surcharge_present=None,
                        evidence_trusted=False, processor_amount=UNSET):
        self.ensure_one()
        provider = self._markup_provider()
        currency = currency or self.company_id.currency_id
        eligible = bool(provider and provider._markup_is_configured() and
                        provider._markup_card_eligible(
                            card_type, evidence_verified=evidence_verified,
                            processor_surcharge_present=processor_surcharge_present,
                            evidence_trusted=evidence_trusted))
        details = calculate_markup(
            base_amount, provider.markup_percent if provider else 0.0,
            currency.round, eligible=eligible,
            processor_amount=processor_amount if processor_amount is not None else UNSET)
        if details['amount'] and provider.markup_product_id and provider.markup_product_id.taxes_id:
            tax_values = calculate_taxed_markup(
                details['amount'], provider.markup_product_id.taxes_id, currency,
                product=provider.markup_product_id,
                rate_included=provider.markup_tax_included,
            )
            details.update({
                'amount': tax_values['net_amount'],
                'net_amount': tax_values['net_amount'],
                'gross_amount': tax_values['gross_amount'],
                'tax_amount': tax_values['tax_amount'],
                'total': currency.round(details['base_amount'] + tax_values['gross_amount']),
            })
        details['total_amount'] = details['total']
        return dict(details, eligible=eligible)
