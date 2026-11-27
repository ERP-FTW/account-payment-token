from odoo import fields, models
from odoo.exceptions import ValidationError


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

    def _get_payment_principal(self):
        self.ensure_one()
        if not self.markup_amount:
            return self.amount
        currency = self.currency_id
        if currency.compare_amounts(self.amount, self.markup_base_amount + self.markup_amount):
            raise ValidationError('The disclosed payment amount changed. Restart checkout.')
        if self.invoice_ids:
            fee = self.markup_fee_move_id
            if (not fee or fee.state != 'draft' or fee.ref != 'Payment markup %s' % self.reference
                    or fee.company_id != self.company_id or fee.currency_id != currency
                    or fee.commercial_partner_id != self.partner_id.commercial_partner_id
                    or currency.compare_amounts(fee.amount_total, self.markup_amount)):
                raise ValidationError('The disclosed fee has changed. Restart checkout.')
        return self.markup_base_amount

    def _markup_fee(self):
        if not self.payment_method_id:
            return self.env['payment.method.fee']
        return self.payment_method_id._markup_fee(self.provider_id)

    def _markup_create_fee_move(self, details):
        """Create one draft fee invoice for posted invoice payments only."""
        self.ensure_one()
        if self.markup_fee_move_id and self.markup_fee_move_id.state != 'cancel':
            return self.markup_fee_move_id
        if not details['amount'] or not self._markup_fee().markup_product_id:
            return False
        if self.markup_fee_move_id and self.markup_fee_move_id.state == 'cancel':
            # A declined attempt owns the canceled document. Never reuse its
            # identity on a later approved retry; retain the canceled move for
            # audit but clear the transaction link before creating a fresh one.
            self.write({'markup_fee_move_id': False, 'markup_invoice_line_id': False})
        invoices = self.invoice_ids
        invoice = invoices[:1]
        provider_company = self.provider_id.company_id
        if invoice and (not invoice.partner_id or invoice.company_id != provider_company or
                invoice.commercial_partner_id != self.partner_id.commercial_partner_id or
                invoice.currency_id != self.currency_id or any(
                    inv.company_id != invoice.company_id or
                    inv.currency_id != invoice.currency_id or
                    inv.partner_id != invoice.partner_id for inv in invoices)):
            return False
        product = self._markup_fee().markup_product_id
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
        if invoice and invoice.state != 'posted':
            return False
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': (invoice.partner_id or self.partner_id).id,
            'company_id': self.company_id.id, 'currency_id': self.currency_id.id,
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

    def _markup_prepare(self, base_amount=None):
        self.ensure_one()
        # A retry/callback must reuse the original decision, never recalculate
        # using a changed provider rate or overwrite its evidence.
        if self.markup_amount_source in ('local', 'processor'):
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
            if self.state == 'draft' and details['amount'] and not self._markup_create_fee_move(details):
                raise ValidationError('Configure an income account for the payment fee product.')
            return details
        fee = self._markup_fee()
        details = fee._markup_details(self.amount if base_amount is None else base_amount,
                                      self.currency_id, self.partner_id)
        if not details['amount']:
            return details
        if not self._markup_create_fee_move(details):
            raise ValidationError('Configure an income account for the payment fee product.')
        self.write({
            'markup_base_amount': details['base_amount'], 'markup_rate_applied': details['rate'],
            'markup_net_amount': details['net_amount'], 'markup_tax_amount': details['tax_amount'],
            'markup_amount': details['gross_amount'], 'markup_gross_amount': details['gross_amount'],
            'markup_total_amount': details['total'], 'markup_amount_source': details['source'],
            'markup_payment_reference': self.reference,
        })
        return details

    def _get_processing_values(self):
        if self.operation in ('refund', 'validation'):
            return super()._get_processing_values()
        details = self._markup_prepare()
        if details['amount']:
            self.amount = details['total']
        values = super()._get_processing_values()
        values.update(amount=details['total'], markup_base_amount=details['base_amount'],
                      markup_amount=details['amount'], markup_total_amount=details['total'],
                      markup_amount_source=details['source'])
        return values

    def _create_child_transaction(self, amount, is_refund=False, **custom_create_values):
        # Odoo 19 dispatches refunds on the child, after creating it here.
        # Freeze its fee evidence before the adapter can complete or reject it.
        child = super()._create_child_transaction(
            amount, is_refund=is_refund, **custom_create_values)
        if not is_refund:
            return child
        requested = abs(amount)
        fee_cap = self._markup_refund_cap(requested)
        fee_cap = self._markup_reserve_refund(fee_cap)
        if fee_cap:
            child.write({
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
        return child

    def _set_done(self, *, state_message=None, extra_allowed_states=()):
        result = super()._set_done(
            state_message=state_message, extra_allowed_states=extra_allowed_states)
        for refund in result.filtered('markup_refund_source_id'):
            source = refund.markup_refund_source_id
            pending = refund.markup_refund_pending_amount
            if source and pending:
                source._markup_refund_cap(pending, confirmed=True)
                refund.write({'markup_refund_pending_amount': 0.0})
        return result

    def _set_error(self, state_message, extra_allowed_states=()):
        result = super()._set_error(
            state_message, extra_allowed_states=extra_allowed_states)
        # The Odoo 18 online adapter reports definite declines through this
        # native transition, not a provider-specific hook. Unknown
        # responses stay pending and must retain their frozen fee document.
        for tx in result:
            if tx.markup_fee_move_id.state == 'draft':
                tx.markup_fee_move_id.button_cancel()
        # An error is terminal for this attempt. Release its reservation only
        # after the core transition succeeds; repeated callbacks are harmless.
        for refund in result.filtered('markup_refund_source_id'):
            pending = refund.markup_refund_pending_amount
            if pending:
                refund.markup_refund_source_id._markup_release_refund_reservation(pending)
                refund.write({'markup_refund_pending_amount': 0.0})
        return result

    def _create_payment(self, **extra_create_values):
        """Include the fee receivable before Odoo 18 creates/reconciles payment."""
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
        return super()._create_payment(**extra_create_values)

    def _set_canceled(self, state_message=None, extra_allowed_states=()):
        result = super()._set_canceled(
            state_message, extra_allowed_states=extra_allowed_states)
        for tx in result:
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
        return result

    def _send_payment_request(self):
        if self.state == 'draft' and self.operation != 'validation':
            details = self._markup_prepare()
            if details['amount']:
                self.amount = details['total']
        return super()._send_payment_request()

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
