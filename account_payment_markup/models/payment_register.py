from odoo import api, fields, models

from ..services.calculation import calculate_markup, calculate_taxed_markup


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    markup_provider_id = fields.Many2one(
        'payment.provider', string='Markup provider',
        domain="[('company_id', '=', company_id), ('markup_enabled', '=', True)]",
    )
    markup_amount = fields.Monetary(
        string='Payment markup', currency_field='currency_id',
        compute='_compute_markup_disclosure', readonly=True,
    )
    markup_total_amount = fields.Monetary(
        string='Total including markup', currency_field='currency_id',
        compute='_compute_markup_disclosure', readonly=True,
    )
    markup_net_amount = fields.Monetary(
        string='Fee net', currency_field='currency_id',
        compute='_compute_markup_disclosure', readonly=True,
    )
    markup_tax_amount = fields.Monetary(
        string='Fee tax', currency_field='currency_id',
        compute='_compute_markup_disclosure', readonly=True,
    )
    markup_gross_amount = fields.Monetary(
        string='Fee gross', currency_field='currency_id',
        compute='_compute_markup_disclosure', readonly=True,
    )

    @api.depends('amount', 'markup_provider_id', 'currency_id', 'payment_method_line_id')
    def _compute_markup_disclosure(self):
        for wizard in self:
            provider = wizard._markup_configuration()
            if not provider or not provider._markup_is_configured():
                wizard.markup_amount = 0.0
                wizard.markup_net_amount = 0.0
                wizard.markup_tax_amount = 0.0
                wizard.markup_gross_amount = 0.0
                wizard.markup_total_amount = wizard.amount
                continue
            details = wizard._markup_payment_values()
            wizard.markup_amount = details['gross_amount']
            wizard.markup_net_amount = details['net_amount']
            wizard.markup_tax_amount = details['tax_amount']
            wizard.markup_gross_amount = details['gross_amount']
            wizard.markup_total_amount = details['total']

    def _markup_configuration(self):
        self.ensure_one()
        method = self.payment_method_line_id
        return method if method and method.markup_mode != 'disabled' else self.markup_provider_id

    def _markup_payment_values(self, amount=None):
        """Return the server-computed disclosure consumed by payment adapters.

        The wizard never trusts a browser-submitted fee, rate or total.  The
        eventual payment adapter calls this method again from the persisted
        provider configuration before creating its fee document/reconciliation.
        """
        self.ensure_one()
        amount = self.amount if amount is None else amount
        provider = self._markup_configuration()
        eligible = self._markup_payment_eligible(provider)
        details = calculate_markup(
            amount, provider.markup_percent if eligible else 0.0,
            self.currency_id.round, eligible=eligible,
        )
        product = provider.markup_product_id if eligible else False
        net_amount = details['amount']
        if eligible and provider._name == 'account.payment.method.line' and provider.markup_mode == 'fixed':
            net_amount = self.currency_id.round(provider.markup_fixed_amount) if amount > 0 else 0.0
        gross_amount = net_amount
        tax_amount = 0.0
        if product and product.taxes_id and net_amount:
            tax_values = calculate_taxed_markup(
                net_amount, product.taxes_id, self.currency_id,
                product=product, partner=self.partner_id,
                rate_included=provider.markup_tax_included,
            )
            net_amount = tax_values['net_amount']
            tax_amount = tax_values['tax_amount']
            gross_amount = tax_values['gross_amount']
        return {
            'base_amount': amount,
            'markup_amount': net_amount,
            'net_amount': net_amount,
            'gross_amount': gross_amount,
            'tax_amount': tax_amount,
            'total': self.currency_id.round(amount + gross_amount),
            'amount': net_amount,
            'provider_id': self.markup_provider_id.id if provider._name == 'payment.provider' else False,
        }

    def _markup_payment_eligible(self, provider=None):
        self.ensure_one()
        provider = provider or self._markup_configuration()
        return bool(provider and provider._markup_is_configured()
                    and provider.company_id == self.company_id
                    and self.payment_type == 'inbound'
                    and self.journal_id)

    def _markup_writeoff_lines(self, details, provider):
        """Credit fee income/tax so the receivable remains the invoice amount."""
        product = provider.markup_product_id
        account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
        if not account or not details['gross_amount']:
            return []
        tax_data = {'taxes': []}
        if product.taxes_id and details['gross_amount']:
            # Reproduce the native fee-invoice tax computation for the actual
            # posted fee line.  The surcharge decision may be gross (included
            # rate), but that does not make the product tax base gross when a
            # product tax is excluded.  For mixed/grouped taxes, solve the
            # native price unit that yields the frozen customer-facing gross.
            taxes = product.taxes_id
            effective_taxes = taxes.flatten_taxes_hierarchy()
            if effective_taxes and all(tax.price_include for tax in effective_taxes):
                tax_price_unit = details['gross_amount']
            elif effective_taxes and any(tax.price_include for tax in effective_taxes):
                low, high = 0.0, details['gross_amount']
                for _i in range(48):
                    middle = (low + high) / 2.0
                    computed = taxes.compute_all(
                        middle, currency=self.currency_id, quantity=1.0,
                        product=product, partner=self.partner_id,
                    )
                    if self.currency_id.round(computed['total_included']) > details['gross_amount']:
                        high = middle
                    else:
                        low = middle
                tax_price_unit = self.currency_id.round(low)
            else:
                tax_price_unit = details['net_amount']
            tax_data = taxes.compute_all(
                tax_price_unit, currency=self.currency_id, quantity=1.0,
                product=product, partner=self.partner_id,
            )
        lines = [{
            'name': 'Payment markup %s' % provider.display_name,
            'account_id': account.id,
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'amount_currency': -details['net_amount'],
            'balance': -self.currency_id._convert(details['net_amount'], self.company_id.currency_id,
                                                  self.company_id, self.payment_date),
            # Match the base line produced for a normal taxed invoice so the
            # payment write-off remains visible to tax reporting.
            'tax_ids': [(6, 0, product.taxes_id.ids)],
            'tax_tag_ids': [(6, 0, tax_data.get('base_tags', []))],
            'markup_decision_source': 'manual',
        }]
        tax_lines = [tax for tax in tax_data.get('taxes', [])
                     if tax.get('amount')]
        computed_tax = sum(tax['amount'] for tax in tax_lines)
        target_tax = self.currency_id.round(details.get('tax_amount', 0.0))
        remaining_tax = target_tax
        for index, tax in enumerate(tax_lines):
            if index == len(tax_lines) - 1:
                amount = remaining_tax
            elif computed_tax:
                amount = self.currency_id.round(
                    target_tax * tax['amount'] / computed_tax)
                remaining_tax = self.currency_id.round(remaining_tax - amount)
            else:
                amount = 0.0
            if not amount:
                continue
            # account.move.line._compute_all_tax stores the tax base in
            # company currency as ``tax['base'] / rate`` (and applies the
            # same tag-inversion sign).  These write-off lines are created
            # directly, so provide that native metadata explicitly instead
            # of leaving tax_base_amount at its field default of zero.
            tax_base = tax.get('base', 0.0)
            base_rate = 1.0
            converted_net = self.currency_id._convert(
                details['net_amount'], self.company_id.currency_id,
                self.company_id, self.payment_date,
            )
            if converted_net:
                base_rate = details['net_amount'] / converted_net
            repartition_line = self.env['account.tax.repartition.line'].browse(
                tax['tax_repartition_line_id'],
            )
            tax_tag_invert = repartition_line.tax_id.type_tax_use == 'sale'
            lines.append({
                'name': tax.get('name') or 'Payment markup tax',
                # Odoo's native tax-line generator falls back to the base-line
                # account when the repartition account is unset.  Keeping that
                # fallback avoids collecting tax that has no balancing entry.
                'account_id': tax.get('account_id') or account.id,
                'partner_id': self.partner_id.id,
                'currency_id': self.currency_id.id,
                'amount_currency': -amount,
                'balance': -self.company_id.currency_id.round(
                    self.currency_id._convert(amount, self.company_id.currency_id,
                                              self.company_id, self.payment_date)),
                'tax_base_amount': self.company_id.currency_id.round(
                    tax_base / base_rate * (-1 if not tax_tag_invert else 1)),
                'tax_repartition_line_id': tax['tax_repartition_line_id'],
                'tax_ids': [(6, 0, tax.get('tax_ids', []))],
                'tax_tag_ids': [(6, 0, tax.get('tag_ids', []))],
                'markup_decision_source': 'manual',
            })
        return lines

    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        if self.markup_provider_id and not self._markup_payment_eligible():
            return vals
        details = self._markup_payment_values(vals['amount'])
        if not details['markup_amount']:
            return vals
        vals['amount'] = details['total']
        vals.update({
            'markup_provider_id': self.markup_provider_id.id,
            'markup_rate_applied': self._markup_configuration().markup_percent,
            'markup_tax_included': self._markup_configuration().markup_tax_included,
            'markup_amount_source': 'manual',
            'markup_base_amount': details['base_amount'],
            'markup_net_amount': details['net_amount'],
            'markup_tax_amount': details['tax_amount'],
            'markup_gross_amount': details['gross_amount'],
            'markup_total_amount': details['total'],
        })
        # The surcharge is a server-computed write-off on the configured
        # service income account.  This keeps Odoo's normal batch allocation,
        # partial-payment and multi-invoice reconciliation path intact.
        vals.setdefault('write_off_line_vals', []).extend(
            self._markup_writeoff_lines(details, self._markup_configuration()))
        return vals

    def _create_payment_vals_from_batch(self, batch_result):
        vals = super()._create_payment_vals_from_batch(batch_result)
        if self.markup_provider_id and not self._markup_payment_eligible():
            return vals
        details = self._markup_payment_values(vals['amount'])
        if not details['markup_amount']:
            return vals
        vals['amount'] = self.currency_id.round(
            vals['amount'] + details['gross_amount'])
        vals.update({
            'markup_provider_id': self.markup_provider_id.id,
            'markup_rate_applied': self._markup_configuration().markup_percent,
            'markup_tax_included': self._markup_configuration().markup_tax_included,
            'markup_amount_source': 'manual',
            'markup_base_amount': details['base_amount'],
            'markup_net_amount': details['net_amount'],
            'markup_tax_amount': details['tax_amount'],
            'markup_gross_amount': details['gross_amount'],
            'markup_total_amount': vals['amount'],
        })
        vals.setdefault('write_off_line_vals', []).extend(
            self._markup_writeoff_lines(details, self._markup_configuration()))
        return vals
