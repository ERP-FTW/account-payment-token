import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountInvoiceTokenWizard(models.TransientModel):
    _name = "account.invoice.token.wizard"
    _description = "Charge Invoice with Saved Payment Token"

    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        required=True,
        readonly=True,
        domain=[("move_type", "=", "out_invoice")],
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        related="invoice_id.partner_id",
        store=False,
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        related="invoice_id.company_id",
        store=False,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        related="invoice_id.currency_id",
        store=False,
        readonly=True,
    )
    amount = fields.Monetary(
        string="Amount to Charge",
        required=True,
        currency_field="currency_id",
    )
    token_id = fields.Many2one(
        "payment.token",
        string="Payment Method (Token)",
        required=True,
        domain="[('partner_id', '=', partner_id), ('company_id', 'in', (company_id, False))]",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        invoice_id = self.env.context.get("default_invoice_id")
        if invoice_id:
            invoice = self.env["account.move"].browse(invoice_id).exists()
            if invoice and "amount" in fields_list:
                res.setdefault("amount", invoice.amount_residual)
        return res

    def _check_preconditions(self):
        """Basic sanity checks before attempting a charge.

        Keep this small and readable; we'll extend it later if needed.
        """
        self.ensure_one()
        invoice = self.invoice_id
        if not invoice:
            raise UserError(_("No invoice selected."))

        if invoice.state != "posted":
            raise UserError(_("Only posted invoices can be charged."))

        if invoice.move_type != "out_invoice":
            raise UserError(_("Only customer invoices can be charged with a token."))

        if invoice.amount_residual <= 0:
            raise UserError(_("The invoice has no outstanding amount to charge."))

        if self.amount <= 0:
            raise UserError(_("The amount to charge must be strictly positive."))

        # Don't allow charging more than residual, with a tiny tolerance
        if self.amount - invoice.amount_residual > 1e-6:
            msg = _(
                "You cannot charge more than the remaining amount on the invoice.\n"
                "Amount to charge: %(charge).2f, Residual: %(residual).2f"
            ) % {"charge": self.amount, "residual": invoice.amount_residual}
            raise UserError(msg)

        if not self.token_id:
            raise UserError(_("Please select a saved payment method (token)."))

        if self.token_id.partner_id != invoice.partner_id.commercial_partner_id:
            raise UserError(
                _(
                    "The selected token belongs to a different customer than the invoice."
                )
            )

    def action_charge_with_token(self):
        """Create a payment.transaction and send a token payment request.

        We rely entirely on the standard payment framework and provider
        implementation. This wizard only wires the invoice + token into it,
        and logs everything for traceability.
        """
        self.ensure_one()
        self._check_preconditions()

        invoice = self.invoice_id
        token = self.token_id
        provider = token.provider_id if hasattr(token, "provider_id") else token.acquirer_id

        if not provider:
            raise UserError(_("The selected token is not linked to a payment provider."))

        payment_method = None
        if hasattr(token, "payment_method_id"):
            payment_method = token.payment_method_id
            if not payment_method:
                raise UserError(_(
                    "The selected token has no Payment Method set, so Odoo cannot create a transaction.\n"
                    "Recreate the token via an online payment flow with 'Save payment method'."
                ))

        # Log intent before creating the transaction
        _logger.info(
            "Attempting backend token charge for invoice %s (id=%s) using token %s (id=%s) "
            "via provider %s (id=%s)%s for amount %s %s",
            invoice.display_name,
            invoice.id,
            token.display_name,
            token.id,
            provider.display_name,
            provider.id,
            (
                ", payment_method=%s (id=%s)"
                % (payment_method.display_name, payment_method.id)
                if payment_method
                else ""
            ),
            self.amount,
            invoice.currency_id.name,
        )

        payment_method_label = (
            _("payment method: %s") % payment_method.display_name
            if payment_method
            else _("payment method: N/A")
        )
        invoice.message_post(
            body=_(
                "Attempting token charge of %(amount).2f %(currency)s using saved payment method "
                "'%(token)s' (provider: %(provider)s, %(pm)s)."
            )
                 % {
                     "amount": self.amount,
                     "currency": invoice.currency_id.name,
                     "token": token.display_name,
                     "provider": provider.display_name,
                     "pm": payment_method_label,
                 }
        )

        tx_vals = {
            "token_id": token.id,
            "partner_id": invoice.partner_id.commercial_partner_id.id,
            "amount": self.amount,
            "currency_id": invoice.currency_id.id,
            "operation": "online_token",
            "company_id": invoice.company_id.id,
        }
        tx_model = self.env["payment.transaction"]
        if "provider_id" in tx_model._fields:
            tx_vals["provider_id"] = provider.id
        else:
            tx_vals["acquirer_id"] = provider.id
        if payment_method and "payment_method_id" in tx_model._fields:
            tx_vals["payment_method_id"] = payment_method.id
        if "invoice_ids" in tx_model._fields:
            tx_vals["invoice_ids"] = [(6, 0, [invoice.id])]
        elif "invoice_id" in tx_model._fields:
            tx_vals["invoice_id"] = invoice.id

        tx = self.env["payment.transaction"].create(tx_vals)

        # We log the transaction reference early for easier tracing
        _logger.info(
            "Created payment.transaction %s (id=%s) for invoice %s (id=%s)",
            tx.reference,
            tx.id,
            invoice.display_name,
            invoice.id,
        )

        invoice.message_post(
            body=_(
                "Created payment transaction %(tx)s (ID: %(tx_id)s) for backend token charge."
            )
                 % {"tx": tx.reference, "tx_id": tx.id}
        )

        # Request provider to perform the token payment.
        if hasattr(tx, "_send_payment_request"):
            tx._send_payment_request()
        elif hasattr(tx, "_process_payment"):
            tx._process_payment()
        else:
            raise UserError(_("Payment request method not available for this Odoo version."))

        # Provider may update state sync/async; we record current state for visibility
        invoice.message_post(
            body=_(
                "Sent payment request for transaction %(tx)s. Current state: %(state)s."
            )
                 % {"tx": tx.reference, "state": tx.state}
        )

        _logger.info(
            "Sent payment request for transaction %s (id=%s); state=%s",
            tx.reference,
            tx.id,
            tx.state,
        )

        # Close wizard and return to invoice
        action = invoice.action_view_invoice() if hasattr(invoice, "action_view_invoice") else None
        return action or {"type": "ir.actions.act_window_close"}
