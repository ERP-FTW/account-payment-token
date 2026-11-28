from odoo import Command, api, fields, models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    markup_amount = fields.Monetary(string='Payment fee', currency_field='currency_id', copy=False)
    markup_product_id = fields.Many2one('product.product', copy=False, ondelete='restrict')
    markup_price_unit = fields.Float(digits='Product Price', copy=False)
    markup_tax_ids = fields.Many2many('account.tax', copy=False)
    markup_fee_move_id = fields.Many2one('account.move', string='Fee invoice', copy=False)
    markup_invoice_line_id = fields.Many2one('account.move.line', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        transactions = super().create(vals_list)
        for tx in transactions:
            if tx.operation not in ('online_direct', 'online_redirect', 'online_token') or tx.source_transaction_id:
                continue
            document = tx.invoice_ids[:1] or tx._markup_sale_order()
            fee = tx.payment_method_id._checkout_markup(
                tx.provider_id, tx.amount, tx.currency_id, tx.partner_id, document)
            if fee:
                tx.write({'amount': fee['total'], 'markup_amount': fee['fee'],
                          'markup_product_id': fee['product'].id, 'markup_price_unit': fee['unit'],
                          'markup_tax_ids': [Command.set(fee['taxes'].ids)]})
        return transactions

    def _markup_sale_order(self):
        return self.sale_order_ids[:1] if 'sale_order_ids' in self._fields else False

    def _set_done(self, *, state_message=None, extra_allowed_states=()):
        done = super()._set_done(state_message=state_message, extra_allowed_states=extra_allowed_states)
        for tx in done.filtered('markup_amount'):
            tx.with_company(tx.company_id)._add_markup_product()
        return done

    def _add_markup_product(self):
        # Upgraded transactions may already have their fee line or draft fee invoice.
        if self.markup_fee_move_id:
            self.invoice_ids = [Command.link(self.markup_fee_move_id.id)]
            return
        if self.markup_invoice_line_id or not self.markup_product_id:
            return
        line = {'product_id': self.markup_product_id.id, 'price_unit': self.markup_price_unit,
                'tax_ids': [Command.set(self.markup_tax_ids.ids)], 'discount': 0}
        invoice = self.invoice_ids[:1]
        order = self._markup_sale_order()
        if order and not invoice:
            order.write({'order_line': [Command.create(dict(line, product_uom_qty=1))]})
        elif invoice and invoice.state == 'draft':
            self.markup_invoice_line_id = self.env['account.move.line'].create(
                dict(line, move_id=invoice.id, quantity=1))
        else:
            # A posted invoice cannot take another product line. Link its fee invoice
            # before native payment post-processing posts and reconciles both.
            fee_invoice = self.env['account.move'].create({
                'move_type': 'out_invoice', 'partner_id': (invoice.partner_id or self.partner_id).id,
                'company_id': self.company_id.id, 'currency_id': self.currency_id.id,
                'invoice_origin': invoice.name or self.reference, 'ref': self.reference,
                'invoice_line_ids': [Command.create(dict(line, quantity=1))],
            })
            self.write({'markup_fee_move_id': fee_invoice.id, 'invoice_ids': [Command.link(fee_invoice.id)]})
