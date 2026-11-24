from odoo import models
from odoo.exceptions import ValidationError


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _cp_document_amount(self):
        """Check original invoice ceilings against principal, not a separate fee.

        The optional CardPointe adapter still locks/checks the original documents
        and competing attempts. Only this transaction's matching, frozen fee
        receivable can be excluded; consent and authorization keep the full total.
        """
        self.ensure_one()
        if self.markup_amount_source != 'local' or not self.markup_amount:
            return super()._cp_document_amount()
        fee = self.markup_fee_move_id
        if fee:
            # Serialize edits to the separate fee document as well as the
            # original documents locked by the adapter.
            self.env.cr.execute('UPDATE account_move SET write_date=write_date WHERE id=%s', [fee.id])
            fee.invalidate_recordset()
        currency = self.currency_id
        if (self.provider_code != 'cardpointe'
                or self.payment_method_id.code not in ('card', 'cardpointe_ach')
                or (self.payment_method_id.code != 'cardpointe_ach'
                    and self.provider_id._markup_program_enabled())
                or not self.invoice_ids or not fee or fee in self.invoice_ids
                or fee.state != 'draft' or fee.move_type != 'out_invoice'
                or fee.company_id != self.company_id or fee.currency_id != currency
                or fee.partner_id != self.invoice_ids[0].partner_id
                or fee.commercial_partner_id != self.partner_id.commercial_partner_id
                or fee.ref != 'Payment markup %s' % self.reference
                or fee.invoice_origin != self.reference
                or self.markup_payment_reference != self.reference
                or self.markup_base_amount <= 0 or self.markup_gross_amount <= 0
                or currency.compare_amounts(self.amount, self.markup_total_amount)
                or currency.compare_amounts(self.amount, self.markup_base_amount + self.markup_gross_amount)
                or currency.compare_amounts(fee.amount_total, self.markup_gross_amount)
                or currency.compare_amounts(self.markup_amount, self.markup_gross_amount)
                or self.markup_accounting_fingerprint != self._markup_current_accounting_fingerprint()):
            raise ValidationError('Manual payment fee changed after disclosure. Restart payment.')
        return self.markup_base_amount

