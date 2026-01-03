# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountInvoiceTokenWizard(models.TransientModel):
    _name = 'account.invoice.token.wizard'
    _description = 'Charge Invoice with Saved Payment Token'

    invoice_id = fields.Many2one('account.move', string='Invoice', required=True, readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, readonly=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True, readonly=True)

    token_id = fields.Many2one('payment.token', string='Saved Payment Method', required=True,
                              domain="[('partner_id', '=', partner_id)]")

    amount = fields.Monetary(string='Amount to Charge', currency_field='currency_id', required=True)
    reference = fields.Char(string='Reference', help='Optional override; defaults to invoice name.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id and 'invoice_id' in fields_list:
            invoice = self.env['account.move'].browse(active_id)
            res.update({
                'invoice_id': invoice.id,
                'partner_id': invoice.partner_id.id,
                'company_id': invoice.company_id.id,
                'currency_id': invoice.currency_id.id,
                'amount': invoice.amount_residual,
            })
        return res

    def action_charge(self):
        self.ensure_one()

        invoice = self.invoice_id.sudo()
        token = self.token_id.sudo()

        if invoice.state != 'posted':
            raise UserError(_("The invoice must be posted before it can be charged."))

        if invoice.move_type not in ('out_invoice', 'out_refund'):
            raise UserError(_("Only customer invoices/credit notes can be charged."))

        if token.partner_id.id != invoice.partner_id.id:
            raise UserError(_("The selected token does not belong to this customer."))

        if self.amount <= 0:
            raise UserError(_("Amount must be greater than 0."))

        if self.amount > invoice.amount_residual + 0.00001:
            raise UserError(_("You cannot charge more than the remaining amount due."))

        provider = getattr(token, 'provider_id', False) or getattr(token, 'acquirer_id', False)
        if not provider:
            raise UserError(_("The selected token is missing its provider/acquirer."))

        reference = self.reference or invoice.name or invoice.ref or f"INV-{invoice.id}"

        _logger.info(
            "[payment_token_invoice] Charging invoice=%s (%s) partner=%s token=%s provider=%s amount=%s %s",
            invoice.id, invoice.name, invoice.partner_id.id, token.id, provider.id, self.amount, invoice.currency_id.name,
        )

        Tx = self.env['payment.transaction'].sudo()
        tx_vals = {
            'reference': reference,
            'amount': float(self.amount),
            'currency_id': invoice.currency_id.id,
            'partner_id': invoice.partner_id.id,
            'token_id': token.id,
            'operation': 'online_direct',
            'is_validation': False,
            # company_id exists on tx in some versions; set if present
        }

        # Provider/acquirer field name differs depending on version
        if 'provider_id' in Tx._fields:
            tx_vals['provider_id'] = provider.id
        elif 'acquirer_id' in Tx._fields:
            tx_vals['acquirer_id'] = provider.id

        # Some versions require payment_method_id for direct operations
        if 'payment_method_id' in Tx._fields:
            payment_method = getattr(token, 'payment_method_id', False) or getattr(provider, 'payment_method_id', False)
            if payment_method:
                tx_vals['payment_method_id'] = payment_method.id

        if 'company_id' in Tx._fields:
            tx_vals['company_id'] = invoice.company_id.id

        # Link to invoice when field exists
        if 'invoice_ids' in Tx._fields:
            tx_vals['invoice_ids'] = [(6, 0, [invoice.id])]
        elif 'invoice_id' in Tx._fields:
            tx_vals['invoice_id'] = invoice.id

        tx = Tx.create(tx_vals)

        # Also link from invoice side if supported
        if 'transaction_ids' in invoice._fields:
            try:
                invoice.write({'transaction_ids': [(4, tx.id)]})
            except Exception:
                _logger.exception("[payment_token_invoice] Failed to link tx to invoice.transaction_ids")

        _logger.info("[payment_token_invoice] Created transaction %s ref=%s", tx.id, tx.reference)

        try:
            tx._send_payment_request()
        except Exception as e:
            _logger.exception("[payment_token_invoice] _send_payment_request failed for tx %s", tx.id)
            raise UserError(_("Payment processing failed: %s") % (str(e),))

        # Refresh
        tx.flush_recordset()
        tx.invalidate_recordset()

        _logger.info("[payment_token_invoice] Transaction %s state=%s", tx.id, tx.state)

        if tx.state not in ('done', 'authorized', 'pending'):
            raise UserError(_("Payment processing failed (state: %s).") % (tx.state,))

        # If done, invoice may auto-reconcile depending on provider/journal setup.
        return {'type': 'ir.actions.act_window_close'}
