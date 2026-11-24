from decimal import Decimal, ROUND_FLOOR

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    markup_refunded_amount = fields.Monetary(
        copy=False, readonly=True, currency_field='currency_id',
    )
    markup_refunded_net_amount = fields.Monetary(
        copy=False, readonly=True, currency_field='currency_id',
    )
    markup_refunded_tax_amount = fields.Monetary(
        copy=False, readonly=True, currency_field='currency_id',
    )

    def action_refund_markup(self, amount=None):
        """Create a manual, evidence-linked credit note for this payment fee.

        This is deliberately an accounting-only path: it never creates a
        payment.transaction and never calls a processor.  The original
        posted payment owns the cumulative gross-fee cap.
        """
        self.ensure_one()
        if self.state != 'posted' or self.markup_amount_source != 'manual':
            raise UserError(_('Only a posted manual markup payment can be refunded.'))
        # Serialize the cap check and update on the payment row.  Without the
        # lock, two concurrent refund requests can both observe the same
        # remaining amount and over-refund the persisted fee evidence.
        self.env.cr.execute(
            'SELECT id FROM account_payment WHERE id = %s FOR UPDATE', [self.id]
        )
        self.invalidate_recordset([
            'markup_refunded_amount', 'markup_refunded_net_amount',
            'markup_refunded_tax_amount',
        ])
        requested = self.currency_id.round(amount if amount is not None else self.markup_gross_amount)
        if requested <= 0:
            raise ValidationError(_('The refund amount must be positive.'))
        remaining = self.currency_id.round(
            max(self.markup_gross_amount - self.markup_refunded_amount, 0.0)
        )
        if requested > remaining:
            raise ValidationError(
                _('The requested markup refund exceeds the remaining original-fee cap.')
            )
        gross = requested
        # Allocate from cumulative rounded targets, rather than rounding net
        # and tax independently for each request.  Independent rounding can
        # exceed a valid small gross refund (e.g. 0.03 -> 0.03 + 0.01).
        cumulative_gross = self.currency_id.round(
            self.markup_refunded_amount + gross
        )

        # The posted payment move is the immutable accounting evidence.  Do
        # not resolve the provider's product, accounts, or taxes again: those
        # settings may legitimately change after the payment was posted.
        original_lines = self.move_id.line_ids.filtered(
            lambda line: line.markup_decision_source == 'manual'
        )
        base_lines = original_lines.filtered(lambda line: not line.tax_repartition_line_id)
        tax_lines = original_lines.filtered(lambda line: line.tax_repartition_line_id)
        ratio = cumulative_gross / self.markup_gross_amount if self.markup_gross_amount else 0.0
        prior_tax_lines = self.env['account.move.line'].search([
            ('markup_source_payment_id', '=', self.id),
            ('markup_decision_source', '=', 'refund'),
            ('tax_repartition_line_id', '!=', False),
        ])
        prior_by_bucket = {}
        prior_company_by_bucket = {}
        for prior_line in self.env['account.move.line'].search([
            ('markup_source_payment_id', '=', self.id),
            ('markup_decision_source', '=', 'refund'),
        ]):
            key = (prior_line.account_id.id, prior_line.tax_repartition_line_id.id or False)
            prior_company_by_bucket[key] = (
                prior_company_by_bucket.get(key, 0.0) + abs(prior_line.balance)
            )
        for prior_line in prior_tax_lines:
            key = (prior_line.account_id.id, prior_line.tax_repartition_line_id.id)
            bucket = prior_by_bucket.setdefault(key, [0.0, 0.0])
            bucket[0] += abs(prior_line.amount_currency)
            bucket[1] += abs(prior_line.tax_base_amount)
        source_by_bucket = {}
        signed_source_by_bucket = {}
        for source_line in tax_lines:
            key = (source_line.account_id.id, source_line.tax_repartition_line_id.id)
            bucket = source_by_bucket.setdefault(key, [0.0, 0.0])
            bucket[0] += abs(source_line.amount_currency)
            bucket[1] += abs(source_line.tax_base_amount)
            signed = signed_source_by_bucket.setdefault(key, [0.0, 0.0, 0.0])
            signed[0] += source_line.amount_currency
            signed[1] += source_line.tax_base_amount
            signed[2] += source_line.balance
        for key, signed in signed_source_by_bucket.items():
            if signed[2] > 0:
                prior = prior_by_bucket.get(key)
                if prior:
                    prior[0] = -prior[0]
                    prior[1] = -prior[1]
                if key in prior_company_by_bucket:
                    prior_company_by_bucket[key] = -prior_company_by_bucket[key]
        # Allocate cumulative tax in currency units while retaining every
        # previously posted bucket as a lower bound.  A fresh largest-remainder
        # allocation can move a cent from one tax account to another as the
        # cumulative gross grows (for example [0.01, 0.01] -> [0.00, 0.02]).
        # The resulting negative delta is clamped below, but its counterpart
        # is still emitted and makes the refund move unbalanced.  Monotonic
        # targets keep every incremental move balanced and still converge to
        # the original tax buckets at a full refund.
        rounding = Decimal(str(self.currency_id.rounding))
        bucket_keys = list(source_by_bucket)
        raw_targets = [Decimal(str(source_by_bucket[key][0])) * Decimal(str(ratio))
                       / rounding for key in bucket_keys]
        target_total = self.currency_id.round(
            float(sum(raw_targets, Decimal('0')) * rounding)
        )
        target_units = int(
            (Decimal(str(target_total)) / rounding).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        prior_units = [
            int((Decimal(str(prior_by_bucket.get(key, (0.0, 0.0))[0])) / rounding)
                .to_integral_value(rounding=ROUND_FLOOR))
            for key in bucket_keys
        ]
        source_units = [
            int((Decimal(str(source_by_bucket[key][0])) / rounding)
                .to_integral_value(rounding=ROUND_FLOOR))
            for key in bucket_keys
        ]
        bucket_units = list(prior_units)
        # Add the remaining cents one at a time to the bucket furthest below
        # its ideal cumulative target.  Ties use source order for stability.
        for _step in range(max(target_units - sum(bucket_units), 0)):
            candidates = [
                index for index, current in enumerate(bucket_units)
                if current < source_units[index]
            ]
            if not candidates:
                raise UserError(_('The original markup tax evidence is inconsistent.'))
            index = max(
                candidates,
                key=lambda item: (raw_targets[item] - bucket_units[item], -item),
            )
            bucket_units[index] += 1
        target_by_bucket = {
            key: (
                float(units * rounding)
                if signed_source_by_bucket[key][2] <= 0 else -float(units * rounding)
            )
            for key, units in zip(bucket_keys, bucket_units)
        }
        cumulative_tax = self.currency_id.round(sum(target_by_bucket.values()))
        cumulative_net = self.currency_id.round(cumulative_gross - cumulative_tax)
        net = self.currency_id.round(cumulative_net - self.markup_refunded_net_amount)
        tax_amount = self.currency_id.round(cumulative_tax - self.markup_refunded_tax_amount)
        account = base_lines[:1].account_id
        if not account:
            raise UserError(_('The original markup accounting evidence is unavailable.'))
        refund_lines = []
        base_source_total = sum(abs(line.amount_currency) for line in base_lines)
        remaining_net = net
        company_currency = self.company_id.currency_id
        refund_date = fields.Date.context_today(self)

        company_debit_total = 0.0
        base_source_by_bucket = {}
        for source_line in base_lines:
            key = (source_line.account_id.id, False)
            base_source_by_bucket[key] = (
                base_source_by_bucket.get(key, 0.0) + abs(source_line.balance)
            )
        for index, source_line in enumerate(base_lines):
            if index == len(base_lines) - 1:
                line_net = remaining_net
            elif base_source_total:
                line_net = self.currency_id.round(
                    net * abs(source_line.amount_currency) / base_source_total
                )
                remaining_net = self.currency_id.round(remaining_net - line_net)
            else:
                line_net = 0.0
            # Refunds reverse the original posted company balance for each
            # evidence line, while amount_currency remains in payment currency.
            # This preserves the original FX valuation and avoids revaluing a
            # refund from a possibly changed provider/currency rate.
            key = (source_line.account_id.id, False)
            source_company_total = base_source_by_bucket[key]
            target_company = company_currency.round(source_company_total * ratio)
            prior_company = prior_company_by_bucket.get(key, 0.0)
            bucket_seen = sum(
                1 for line in base_lines[:index]
                if (line.account_id.id, False) == key
            )
            bucket_count = sum(
                1 for line in base_lines
                if (line.account_id.id, False) == key
            )
            company_net = 0.0
            if bucket_seen == bucket_count - 1:
                company_net = company_currency.round(
                    max(target_company - prior_company, 0.0)
                )
            company_debit_total += company_net
            refund_lines.append({
                'name': _('Payment markup refund for %s') % self.name,
                'account_id': source_line.account_id.id,
                'partner_id': self.partner_id.id,
                'currency_id': self.currency_id.id,
                'debit': company_net,
                'amount_currency': line_net,
                'tax_ids': [(6, 0, source_line.tax_ids.ids)],
                'tax_tag_ids': [(6, 0, source_line.tax_tag_ids.ids)],
                'markup_source_payment_id': self.id,
                'markup_decision_source': 'refund',
            })
        # Reconcile each original tax/account/repartition bucket against its
        # cumulative target.  Allocating only this request's tax amount loses
        # rounding residues when several small refunds are posted.
        for index, source_line in enumerate(tax_lines):
            key = (source_line.account_id.id or account.id, source_line.tax_repartition_line_id.id)
            original_tax, original_base = source_by_bucket[key]
            prior_tax, prior_base = prior_by_bucket.get(key, (0.0, 0.0))
            target_tax = target_by_bucket.get(key, 0.0)
            # tax_base_amount is stored in company currency (Odoo 16's
            # account.move.line field uses company_currency_id), not in the
            # payment currency.  Keep the reporting base at company precision
            # even when the payment currency has coarser/finer rounding.
            sign = -1.0 if signed_source_by_bucket[key][2] > 0 else 1.0
            target_base = company_currency.round(original_base * ratio) * sign
            line_tax = self.currency_id.round((target_tax - prior_tax) if sign < 0 else max(target_tax - prior_tax, 0.0))
            line_base = company_currency.round((target_base - prior_base) if sign < 0 else max(target_base - prior_base, 0.0))
            # If a bucket has multiple source lines, emit its remaining
            # cumulative target on the last line only; this keeps the bucket
            # exact while retaining the original line/account mapping.
            bucket_seen = sum(
                1 for line in tax_lines[:index]
                if (line.account_id.id or account.id, line.tax_repartition_line_id.id) == key
            )
            bucket_count = sum(
                1 for line in tax_lines
                if (line.account_id.id or account.id, line.tax_repartition_line_id.id) == key
            )
            if bucket_seen < bucket_count - 1:
                line_tax = 0.0
                line_base = 0.0
            elif bucket_count > 1:
                line_tax = self.currency_id.round((target_tax - prior_tax) if sign < 0 else max(target_tax - prior_tax, 0.0))
                line_base = self.currency_id.round((target_base - prior_base) if sign < 0 else max(target_base - prior_base, 0.0))
            company_target = company_currency.round(
                sum(
                    abs(line.balance) for line in tax_lines
                    if (line.account_id.id or account.id,
                        line.tax_repartition_line_id.id) == key
                ) * ratio
            ) * sign
            prior_company = prior_company_by_bucket.get(key, 0.0)
            company_tax = 0.0
            if bucket_seen == bucket_count - 1:
                company_tax = company_currency.round(
                    (company_target - prior_company) if sign < 0 else max(company_target - prior_company, 0.0)
                )
            company_debit_total += company_tax
            refund_lines.append({
                'name': _('Payment markup refund tax for %s') % self.name,
                'account_id': source_line.account_id.id or account.id,
                'partner_id': self.partner_id.id,
                'currency_id': self.currency_id.id,
                'debit': company_tax,
                'amount_currency': line_tax,
                'tax_ids': [(6, 0, source_line.tax_ids.ids)],
                'tax_tag_ids': [(6, 0, source_line.tax_tag_ids.ids)],
                'tax_repartition_line_id': source_line.tax_repartition_line_id.id,
                'tax_base_amount': line_base,
                'markup_source_payment_id': self.id,
                'markup_decision_source': 'refund',
            })
        if not refund_lines:
            raise UserError(_('The original markup accounting evidence is unavailable.'))
        refund_lines.append({
            'name': _('Payment markup refund receivable for %s') % self.name,
            'account_id': self.partner_id.property_account_receivable_id.id,
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'credit': company_currency.round(company_debit_total),
            'amount_currency': -gross,
            'markup_source_payment_id': self.id,
            'markup_decision_source': 'refund',
        })
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'journal_id': self.journal_id.id,
            'date': refund_date,
            'ref': _('Markup refund for %s') % self.name,
            'markup_source_payment_id': self.id,
            'markup_refund_amount': gross,
            'markup_provider_id': self.markup_provider_id.id,
            'markup_rate_applied': self.markup_rate_applied,
            'markup_tax_included': self.markup_tax_included,
            'markup_amount_source': 'manual',
            'markup_net_amount': net,
            'markup_tax_amount': tax_amount,
            'markup_gross_amount': gross,
            'markup_total_amount': gross,
            'line_ids': [(0, 0, line) for line in refund_lines],
        })
        move.action_post()
        self.write({
            'markup_refunded_amount': cumulative_gross,
            'markup_refunded_net_amount': self.currency_id.round(
                self.markup_refunded_net_amount + net
            ),
            'markup_refunded_tax_amount': self.currency_id.round(
                self.markup_refunded_tax_amount + tax_amount
            ),
        })
        return move

    def action_void_markup(self):
        """Void a draft manual payment without creating a processor record."""
        self.ensure_one()
        if self.state != 'draft' or self.markup_amount_source != 'manual':
            raise UserError(_('Only a draft manual markup payment can be voided.'))
        return self.action_cancel()