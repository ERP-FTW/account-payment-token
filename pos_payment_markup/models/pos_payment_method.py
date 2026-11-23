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

    markup_mode = fields.Selection([
        ('disabled', 'Disabled'), ('percentage', 'Percentage'), ('fixed', 'Fixed amount'),
    ], default='disabled', required=True, string='Manual payment fee')
    manual_markup_percent = fields.Float(string='Manual fee percentage', digits=(16, 4))
    markup_fixed_amount = fields.Float(string='Fixed fee in order currency', digits=(16, 4))
    manual_markup_product_id = fields.Many2one('product.product', string='Manual fee product',
                                              domain=[('type', '=', 'service')], check_company=True)
    manual_markup_tax_included = fields.Boolean(string='Manual fee includes tax')

    @api.model
    def _load_pos_data_fields(self, config_id):
        return super()._load_pos_data_fields(config_id) + [
            'markup_mode', 'markup_fixed_amount', 'markup_enabled', 'markup_percent',
            'markup_product_id', 'markup_tax_included',
        ]

    @api.constrains('markup_mode', 'manual_markup_percent', 'markup_fixed_amount', 'manual_markup_product_id')
    def _check_manual_markup(self):
        from odoo.exceptions import ValidationError
        for method in self:
            if not 0 <= method.manual_markup_percent <= 100 or method.markup_fixed_amount < 0:
                raise ValidationError('Fee percentage must be 0–100 and fixed amount nonnegative.')
            if method.markup_mode != 'disabled' and (
                not method.manual_markup_product_id or method.manual_markup_product_id.type != 'service'
            ):
                raise ValidationError('Manual fees require a service product.')

    markup_card_type = fields.Selection([
        ('credit', 'Credit card'), ('unknown', 'Unknown / not eligible'),
    ], default='unknown', required=True,
        help='Explicit manual eligibility. Unknown methods remain fee-free.')
    markup_enabled = fields.Boolean(compute='_compute_markup_provider_values')
    markup_percent = fields.Float(compute='_compute_markup_provider_values', digits=(16, 4))
    markup_product_id = fields.Many2one('product.product', compute='_compute_markup_provider_values')
    markup_tax_included = fields.Boolean(compute='_compute_markup_provider_values')

    @api.depends('markup_mode', 'manual_markup_percent', 'manual_markup_product_id',
                 'manual_markup_tax_included', 'markup_provider_id', 'markup_card_type',
                 'journal_id', 'markup_provider_id.markup_enabled', 'markup_provider_id.markup_percent',
                 'markup_provider_id.markup_product_id', 'markup_provider_id.markup_tax_included',
                 'markup_provider_id.markup_use_cardpointe_program')
    def _compute_markup_provider_values(self):
        for method in self:
            if method.markup_mode != 'disabled':
                method.markup_enabled = bool(method.manual_markup_product_id)
                method.markup_percent = method.manual_markup_percent
                method.markup_product_id = method.manual_markup_product_id
                method.markup_tax_included = method.manual_markup_tax_included
                continue
            provider = method._markup_provider()
            method.markup_enabled = bool(provider and provider._markup_is_configured() and not provider.markup_use_cardpointe_program)
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
                        evidence_trusted=False, processor_amount=UNSET, fiscal_position=None):
        self.ensure_one()
        provider = self._markup_provider()
        currency = currency or self.company_id.currency_id
        manual = self.markup_mode != 'disabled'
        eligible = bool(self.markup_enabled) if manual else bool(provider and provider._markup_is_configured() and
                        provider._markup_card_eligible(
                            card_type, evidence_verified=evidence_verified,
                            processor_surcharge_present=processor_surcharge_present,
                            evidence_trusted=evidence_trusted))
        details = calculate_markup(
            base_amount, self.markup_percent,
            currency.round, eligible=eligible,
            processor_amount=processor_amount if processor_amount is not None else UNSET)
        if manual and self.markup_mode == 'fixed' and base_amount > 0:
            details['amount'] = currency.round(self.markup_fixed_amount)
            details['total'] = currency.round(base_amount + details['amount'])
        taxes = self.markup_product_id.taxes_id.filtered(lambda tax: tax.company_id == self.company_id)
        if fiscal_position:
            taxes = fiscal_position.map_tax(taxes)
        if details['amount'] and taxes:
            tax_values = calculate_taxed_markup(
                details['amount'], taxes, currency,
                product=self.markup_product_id,
                rate_included=self.markup_tax_included,
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
