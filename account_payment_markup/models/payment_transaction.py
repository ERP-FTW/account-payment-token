from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.calculation import UNSET, calculate_markup, calculate_taxed_markup


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    markup_base_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_rate_applied = fields.Float(copy=False, digits=(16, 4))
    # Customer-facing gross fee; the explicit net fee is persisted separately
    # so retries do not infer tax semantics from changed configuration.
    markup_net_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_tax_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_gross_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_total_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_amount_source = fields.Selection(
        [('local', 'Local calculation'), ('processor', 'Processor authoritative amount'),
         ('none', 'Not applied')], copy=False,
    )
    markup_payment_reference = fields.Char(copy=False, index=True)
    markup_fee_move_id = fields.Many2one('account.move', copy=False, ondelete='set null')
    markup_invoice_line_id = fields.Many2one('account.move.line', copy=False, ondelete='set null')
    markup_refunded_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_refund_pending_amount = fields.Monetary(copy=False, currency_field='currency_id')
    markup_refund_source_id = fields.Many2one('payment.transaction', copy=False, ondelete='set null')
    markup_charge_key = fields.Char(copy=False, index=True)
    markup_charge_started = fields.Boolean(copy=False)
    markup_accounting_fingerprint = fields.Char(copy=False)

    def _markup_current_accounting_fingerprint(self):
        self.ensure_one()
        product = self.provider_id.markup_product_id
        if not product:
            return 'none'
        def ids(records):
            return ','.join(str(value) for value in records.ids)

        def tax_fingerprint(tax, seen=()):
            """Capture effective recursive tax semantics, not only child IDs."""
            if tax.id in seen:
                return 'cycle:%s' % tax.id
            repartition = '|'.join(
                '%s:%s:%s:%s' % (
                    line.id, line.factor_percent, line.repartition_type,
                    line.account_id.id or 0,
                )
                for line in tax.invoice_repartition_line_ids.sorted('id')
            )
            children = ','.join(
                tax_fingerprint(child, seen + (tax.id,))
                for child in tax.children_tax_ids.sorted('id')
            )
            return '%s:%s:%s:%s:%s:%s:%s:%s:%s' % (
                tax.id, tax.amount, tax.amount_type, int(tax.price_include),
                int(tax.include_base_amount), int(tax.is_base_affected),
                tax.tax_group_id.id or 0, repartition, children,
            )

        taxes = [tax_fingerprint(tax) for tax in product.taxes_id.sorted('id')]
        income_account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
        return 'product=%s;income=%s;category=%s;taxes=%s' % (
            product.id, income_account.id or 0, product.categ_id.id,
            ','.join(taxes),
        )

    def _markup_details(self, base_amount=None, card_type=None, processor_amount=UNSET,
                        evidence_verified=False, surcharge_present=None,
                        evidence_trusted=False):
        self.ensure_one()
        provider = self.provider_id
        base = base_amount if base_amount is not None else self.amount
        eligible = provider._markup_is_configured() and provider._markup_card_eligible(
            card_type, evidence_verified=evidence_verified,
            processor_surcharge_present=surcharge_present,
            evidence_trusted=evidence_trusted,
        )
        details = calculate_markup(
            base, provider.markup_percent, self.currency_id.round,
            eligible=eligible, processor_amount=processor_amount,
        )
        # Keep the fee decision net of tax.  The processor charge uses the
        # gross fee, while the fee invoice line uses the net price and Odoo
        # applies the configured product taxes exactly once.
        product = provider.markup_product_id
        details['net_amount'] = details['amount']
        details['tax_amount'] = 0.0
        details['gross_amount'] = details['amount']
        if details['amount'] and product and product.taxes_id:
            tax_values = calculate_taxed_markup(
                details['amount'], product.taxes_id, self.currency_id,
                product=product, partner=self.partner_id,
                rate_included=provider.markup_tax_included,
            )
            details['net_amount'] = tax_values['net_amount']
            details['tax_amount'] = tax_values['tax_amount']
            details['gross_amount'] = tax_values['gross_amount']
            details['total'] = self.currency_id.round(
                details['base_amount'] + details['gross_amount'])
            details['amount'] = details['gross_amount']
        # ``amount`` is the customer-facing surcharge.  Accounting consumes
        # the explicit net amount; do not overload the persisted decision with
        # an implicit tax interpretation.
        details['amount'] = details.get('gross_amount', details['amount'])
        return details

    def _markup_create_fee_move(self, details):
        """Create one draft fee invoice for posted invoice payments only."""
        self.ensure_one()
        if not details['amount'] or not self.provider_id.markup_product_id:
            return self.markup_fee_move_id
        if self.markup_fee_move_id and self.markup_fee_move_id.state != 'cancel':
            return self.markup_fee_move_id
        if self.markup_fee_move_id and self.markup_fee_move_id.state == 'cancel':
            # A declined attempt owns the canceled document. Never reuse its
            # identity on a later approved retry; retain the canceled move for
            # audit but clear the transaction link before creating a fresh one.
            self.write({'markup_fee_move_id': False, 'markup_invoice_line_id': False})
        if 'invoice_ids' not in self._fields or not self.invoice_ids:
            return False
        invoices = self.invoice_ids
        invoice = invoices[0]
        provider_company = self.provider_id.company_id
        if (not invoice.partner_id or invoice.company_id != provider_company or
                invoice.commercial_partner_id != self.partner_id.commercial_partner_id or
                invoice.currency_id != self.currency_id or any(
                    inv.company_id != invoice.company_id or
                    inv.currency_id != invoice.currency_id or
                    inv.partner_id != invoice.partner_id for inv in invoices)):
            return False
        product = self.provider_id.markup_product_id
        account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
        if not account:
            return False
        net_amount = details.get('net_amount', details['amount'])
        gross_amount = details.get('gross_amount', net_amount)
        taxes = product.taxes_id
        # Odoo computes grouped taxes from their effective leaf taxes.  A group
        # record commonly has price_include=False even when one of its children
        # is included, so inspect the flattened hierarchy for the unit-selection
        # decision while retaining the original recordset for compute_all().
        effective_taxes = taxes.flatten_taxes_hierarchy() if taxes else taxes
        # The fee decision is authoritative, but account.move recomputes the
        # line with the product's native tax semantics.  For mixed taxes, a
        # price unit is neither the net nor the gross decision: included taxes
        # are extracted first and excluded taxes are then added.  Solve for the
        # unit that makes Odoo's own compute_all() reproduce the frozen gross.
        # This also keeps the posted tax amount equal to the persisted decision
        # instead of charging excluded tax a second time.
        if effective_taxes and any(tax.price_include for tax in effective_taxes) and not all(
                tax.price_include for tax in effective_taxes):
            low, high = 0.0, gross_amount
            for _i in range(48):
                middle = (low + high) / 2.0
                computed = taxes.compute_all(
                    middle, currency=self.currency_id, quantity=1.0,
                    product=product, partner=self.partner_id,
                )
                if self.currency_id.round(computed['total_included']) > gross_amount:
                    high = middle
                else:
                    low = middle
            price_unit = self.currency_id.round(low)
        elif effective_taxes and all(tax.price_include for tax in effective_taxes):
            price_unit = gross_amount
        else:
            price_unit = net_amount
        if invoice.state == 'draft':
            # Keep draft checkout disclosure on the originating invoice; the
            # line is uniquely identified by this transaction reference.
            existing = invoice.invoice_line_ids.filtered(
                lambda line: line.name == 'Payment markup %s' % self.reference)
            if not existing:
                invoice.write({'invoice_line_ids': [(0, 0, {
                    'product_id': product.id, 'name': 'Payment markup %s' % self.reference,
                    'quantity': 1.0, 'price_unit': price_unit,
                    'account_id': account.id, 'tax_ids': [(6, 0, product.taxes_id.ids)],
                })]})
                existing = invoice.invoice_line_ids.filtered(
                    lambda line: line.name == 'Payment markup %s' % self.reference)
            if existing:
                self.markup_invoice_line_id = existing[-1].id
            return invoice
        if invoice.state != 'posted':
            return False
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': invoice.partner_id.id,
            'company_id': invoice.company_id.id, 'currency_id': invoice.currency_id.id,
            'invoice_origin': self.reference,
            'ref': 'Payment markup %s' % self.reference,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id, 'name': product.display_name,
                'quantity': 1.0, 'price_unit': price_unit,
                'account_id': account.id, 'tax_ids': [(6, 0, product.taxes_id.ids)],
            })],
        })
        self.markup_fee_move_id = move.id
        return move

    def _markup_prepare(self, base_amount=None, card_type=None, processor_amount=UNSET,
                        evidence_verified=False, surcharge_present=None,
                        evidence_trusted=False):
        self.ensure_one()
        # A retry/callback must reuse the original decision, never recalculate
        # using a changed provider rate or overwrite its evidence.
        if self.markup_amount_source in ('local', 'processor'):
            current_fingerprint = self._markup_current_accounting_fingerprint()
            if (self.markup_accounting_fingerprint and
                    self.markup_accounting_fingerprint != current_fingerprint):
                raise ValidationError(
                    'The payment markup accounting configuration changed after disclosure; '
                    'the payment must be restarted.'
                )
            details = {
                'base_amount': self.markup_base_amount,
                'rate': self.markup_rate_applied,
                'amount': self.markup_amount,
                'net_amount': self.markup_net_amount or self.markup_amount,
                'tax_amount': self.markup_tax_amount,
                'gross_amount': self.markup_total_amount - self.markup_base_amount,
                'total': self.markup_total_amount or self.amount,
                'source': self.markup_amount_source,
            }
            # A canceled fee document belongs only to the failed attempt. A
            # later approved retry reuses the persisted decision but gets one
            # fresh linked document rather than an orphaned canceled move.
            if details['amount'] and self.invoice_ids and not self._markup_create_fee_move(details):
                # Never send a fee-bearing gateway request when the configured
                # product/account/invoice boundary cannot create its matching
                # receivable.  The payment remains the original base amount.
                details = {
                    'base_amount': details['base_amount'], 'rate': 0.0,
                    'amount': 0.0, 'net_amount': 0.0, 'gross_amount': 0.0,
                    'total': details['base_amount'], 'source': 'none',
                }
                self.write({
                    'markup_amount': 0.0, 'markup_net_amount': 0.0, 'markup_gross_amount': 0.0,
                    'markup_total_amount': details['base_amount'],
                    'markup_amount_source': 'none',
                })
            return details
        details = self._markup_details(
            base_amount, card_type, processor_amount,
            evidence_verified=evidence_verified, surcharge_present=surcharge_present,
            evidence_trusted=evidence_trusted,
        )
        self.write({
            'markup_base_amount': details['base_amount'],
            'markup_rate_applied': details['rate'],
            'markup_net_amount': details.get('net_amount', details['amount']),
            'markup_tax_amount': details.get('tax_amount', 0.0),
            'markup_amount': details['amount'],
            'markup_gross_amount': details.get('gross_amount', details['amount']),
            'markup_total_amount': details['total'], 'markup_amount_source': details['source'],
            'markup_payment_reference': self.reference,
            'markup_accounting_fingerprint': self._markup_current_accounting_fingerprint(),
        })
        if details['amount'] and not self._markup_create_fee_move(details):
            details = {
                'base_amount': details['base_amount'], 'rate': 0.0,
                'amount': 0.0, 'net_amount': 0.0, 'gross_amount': 0.0,
                'total': details['base_amount'], 'source': 'none',
            }
        self.write({
            'markup_base_amount': details['base_amount'],
            'markup_rate_applied': details['rate'],
            'markup_net_amount': details.get('net_amount', details['amount']),
            'markup_tax_amount': details.get('tax_amount', 0.0),
            'markup_amount': details['amount'],
            'markup_gross_amount': details.get('gross_amount', details['amount']),
            'markup_total_amount': details['total'], 'markup_amount_source': details['source'],
            'markup_payment_reference': self.reference,
            'markup_accounting_fingerprint': self._markup_current_accounting_fingerprint(),
        })
        return details

    def _markup_processing_values(self, processing_values):
        metadata = processing_values.get('markup_evidence') or {}
        processor_amount = (
            processing_values['processor_markup_amount']
            if 'processor_markup_amount' in processing_values else UNSET
        )
        return self._markup_prepare(
            base_amount=processing_values.get('amount', self.amount),
            card_type=metadata.get('card_type'),
            processor_amount=processor_amount,
            evidence_verified=bool(metadata.get('verified')),
            surcharge_present=(metadata['surcharge_present'] if 'surcharge_present' in metadata else None),
            evidence_trusted=bool(metadata.get('server_verified')),
        )

    def _get_processing_values(self):
        # Provider redirect adapters read self.amount while super() renders
        # their form. Persist the authoritative total before that call.
        details = self._markup_prepare(base_amount=self.amount)
        if details['source'] in ('local', 'processor'):
            self.amount = details['total']
        values = super()._get_processing_values()
        details = self._markup_processing_values(dict(values, amount=self.amount))
        # The provider receives and discloses one server-authoritative amount.
        # Client-supplied base/rate values never replace this decision.
        values.update({
            'amount': details['total'],
            'markup_base_amount': details['base_amount'],
            'markup_amount': details['amount'],
            'markup_total_amount': details['total'],
            'markup_amount_source': details['source'],
        })
        return values

    def _get_specific_processing_values(self, processing_values):
        values = super()._get_specific_processing_values(processing_values)
        details = self._markup_processing_values(processing_values)
        values.update({
            'amount': details['total'],
            'markup_base_amount': details['base_amount'], 'markup_amount': details['amount'],
            'markup_total_amount': details['total'], 'markup_amount_source': details['source'],
        })
        return values

    def _send_refund_request(self, amount_to_refund=None):
        self.ensure_one()
        requested = amount_to_refund or abs(self.amount)
        fee_cap = self._markup_refund_cap(requested)
        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)
        if refund_tx and fee_cap:
            fee_cap = self._markup_reserve_refund(fee_cap)
        if refund_tx and fee_cap:
            refund_tx.write({
                'markup_base_amount': self.currency_id.round(requested - fee_cap),
                'markup_net_amount': -fee_cap,
                'markup_amount': -fee_cap,
                'markup_gross_amount': -fee_cap,
                'markup_total_amount': -requested,
                'markup_amount_source': self.markup_amount_source,
                'markup_payment_reference': self.reference,
                'markup_refund_source_id': self.id,
                'markup_refund_pending_amount': fee_cap,
            })
        return refund_tx

    def _set_done(self, state_message=None):
        result = super()._set_done(state_message)
        for refund in self.filtered('markup_refund_source_id'):
            source = refund.markup_refund_source_id
            pending = refund.markup_refund_pending_amount
            if source and pending:
                source._markup_refund_cap(pending, confirmed=True)
                refund.write({'markup_refund_pending_amount': 0.0})
        return result

    def _set_error(self, state_message):
        result = super()._set_error(state_message)
        # An error is terminal for this attempt. Release its reservation only
        # after the core transition succeeds; repeated callbacks are harmless.
        for refund in self.filtered('markup_refund_source_id'):
            pending = refund.markup_refund_pending_amount
            if pending:
                refund.markup_refund_source_id._markup_release_refund_reservation(pending)
                refund.write({'markup_refund_pending_amount': 0.0})
        return result

    def _reconcile_after_done(self):
        """Include the linked fee receivable in normal account.payment flow."""
        for tx in self:
            if tx.markup_amount_source in ('local', 'processor') and tx.markup_amount:
                details = {
                    'base_amount': tx.markup_base_amount,
                    'rate': tx.markup_rate_applied,
                    'amount': tx.markup_amount,
                    'net_amount': tx.markup_net_amount or tx.markup_amount,
                    'tax_amount': tx.markup_tax_amount,
                    'gross_amount': tx.markup_gross_amount or tx.markup_amount,
                    'total': tx.markup_total_amount or tx.amount,
                    'source': tx.markup_amount_source,
                }
                tx._markup_create_fee_move(details)
            if tx.markup_fee_move_id and tx.markup_fee_move_id.state == 'draft':
                tx.markup_fee_move_id.action_post()
            if tx.markup_fee_move_id and tx.markup_fee_move_id not in tx.invoice_ids:
                tx.invoice_ids = [(4, tx.markup_fee_move_id.id)]
            if tx.markup_amount_source in ('local', 'processor') and tx.amount != tx.markup_total_amount:
                tx.amount = tx.markup_total_amount
        return super()._reconcile_after_done()

    def _set_canceled(self, state_message=None):
        for tx in self:
            if tx.markup_refund_source_id:
                pending = tx.markup_refund_pending_amount
                if pending:
                    tx.markup_refund_source_id._markup_release_refund_reservation(pending)
                    tx.markup_refund_pending_amount = 0.0
            if tx.markup_fee_move_id and tx.markup_fee_move_id.state == 'draft':
                tx.markup_fee_move_id.button_cancel()
            invoice_line = getattr(tx, 'markup_invoice_line_id', False)
            if invoice_line and invoice_line.move_id.state == 'draft':
                invoice_line.unlink()
            # Keep the canceled move linked for audit. A later approved retry
            # sees the canceled state and creates one fresh fee move.
            tx.write({'markup_invoice_line_id': False})
        return super()._set_canceled(state_message)

    def _cardpointe_charge_from_token(self, token, meta=None, flow=None, save_payment_method=False,
                                      capture=None, amount=None, success_message=None):
        # An explicit caller amount, including 0.00, is authoritative. This is
        # required by CardPointe tokenization and by Odoo's amount validation.
        if self.provider_code != 'cardpointe' or amount is not None:
            return super()._cardpointe_charge_from_token(
                token, meta=meta, flow=flow, save_payment_method=save_payment_method,
                capture=capture, amount=amount, success_message=success_message)
        meta = meta if isinstance(meta, dict) else {}
        evidence = meta.get('markup_evidence') or {}
        details = self._markup_prepare(
            base_amount=self.amount, card_type=evidence.get('card_type'),
            evidence_verified=bool(evidence.get('verified')),
            surcharge_present=(evidence['surcharge_present'] if 'surcharge_present' in evidence else None),
            evidence_trusted=bool(evidence.get('server_verified')),
        )
        charge_amount = details['total']
        return super()._cardpointe_charge_from_token(
            token, meta=meta, flow=flow, save_payment_method=save_payment_method,
            capture=capture, amount=charge_amount, success_message=success_message)

    def _cardpointe_fail(self, message, code=None):
        """A declined attempt must not leave a fee receivable behind."""
        for tx in self:
            move = tx.markup_fee_move_id
            if move and move.state == 'draft':
                move.button_cancel()
            invoice_line = getattr(tx, 'markup_invoice_line_id', False)
            if invoice_line and invoice_line.move_id.state == 'draft':
                invoice_line.unlink()
            # Retain the canceled move so a later approved retry can replace it
            # without losing the failed-attempt audit trail.
            tx.write({'markup_invoice_line_id': False})
        return super()._cardpointe_fail(message, code)

    def _send_payment_request(self):
        # The CardPointe parent implementation charges saved tokens before a
        # post-super hook can alter the amount. Handle that path here instead.
        non_card = self.filtered(lambda tx: tx.provider_code != 'cardpointe' or not tx.token_id)
        if non_card:
            for tx in non_card:
                details = tx._markup_prepare(base_amount=tx.amount)
                if details['source'] in ('local', 'processor'):
                    # Odoo 16 provider adapters read self.amount when building
                    # their request; make the persisted, server decision the
                    # amount charged rather than only adding display metadata.
                    tx.amount = details['total']
            result = super(PaymentTransaction, non_card)._send_payment_request()
        else:
            result = True
        for tx in self - non_card:
            # A retry after an unknown gateway outcome must be resolved by an
            # inquiry/reconciliation flow, not by issuing a second saved-token
            # charge.  A completed transaction is likewise idempotent.
            if tx.state in ('done', 'cancel') or tx.markup_charge_started:
                continue
            tx._cardpointe_set_default_stored_credential_semantics(
                initiator='customer', schedule='unscheduled')
            details = tx._markup_prepare(base_amount=tx.amount)
            if details['source'] in ('local', 'processor'):
                tx.amount = details['total']
            tx.write({'markup_charge_started': True, 'markup_charge_key': tx.reference})
            tx._cardpointe_charge_from_payment_token(tx.token_id)
        return result

    def _markup_refund_cap(self, requested_amount, confirmed=False):
        self.ensure_one()
        if requested_amount <= 0:
            return 0.0
        if confirmed:
            # A confirmed child already owns a reservation. Do not subtract
            # pending capacity when measuring the refundable amount, or a
            # full reservation appears to have no remaining fee to confirm.
            self.env.cr.execute(
                "SELECT id FROM payment_transaction WHERE id = %s FOR UPDATE",
                (self.id,),
            )
            self.invalidate_recordset()
            remaining = max(
                (self.markup_gross_amount or self.markup_amount or 0.0)
                - (self.markup_refunded_amount or 0.0), 0.0,
            )
            amount = min(self.currency_id.round(requested_amount), remaining)
            pending = min(self.markup_refund_pending_amount or 0.0, amount)
            if amount:
                self.write({
                    'markup_refunded_amount': (self.markup_refunded_amount or 0.0) + amount,
                    'markup_refund_pending_amount': max(
                        (self.markup_refund_pending_amount or 0.0) - pending, 0.0,
                    ),
                })
            return amount
        remaining = max((self.markup_gross_amount or self.markup_amount or 0.0) -
                        (self.markup_refunded_amount or 0.0) -
                        (self.markup_refund_pending_amount or 0.0), 0.0)
        return min(self.currency_id.round(requested_amount), remaining)

    def _markup_reserve_refund(self, amount):
        self.ensure_one()
        if amount <= 0:
            return 0.0
        self.env.cr.execute(
            "SELECT id FROM payment_transaction WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset()
        remaining = max((self.markup_gross_amount or self.markup_amount or 0.0) -
                        (self.markup_refunded_amount or 0.0) -
                        (self.markup_refund_pending_amount or 0.0), 0.0)
        amount = min(self.currency_id.round(amount), remaining)
        if amount:
            self.write({'markup_refund_pending_amount':
                        (self.markup_refund_pending_amount or 0.0) + amount})
        return amount

    def _markup_release_refund_reservation(self, amount):
        self.ensure_one()
        if amount <= 0:
            return
        self.env.cr.execute(
            "SELECT id FROM payment_transaction WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset()
        self.write({
            'markup_refund_pending_amount': max(
                (self.markup_refund_pending_amount or 0.0) - amount, 0.0,
            ),
        })
