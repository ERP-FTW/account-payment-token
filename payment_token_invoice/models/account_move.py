from odoo import api, fields, models, _

class AccountMove(models.Model):
    _inherit = "account.move"

    def action_open_token_payment_wizard(self):
        """Open the token payment wizard for this invoice.

        We keep this method tiny and delegate all logic to the wizard.
        """
        self.ensure_one()
        # Only makes sense on posted customer invoices, but we keep the
        # check light and let the wizard validate in depth.
        return {
            "type": "ir.actions.act_window",
            "name": _("Charge with Saved Card"),
            "res_model": "account.invoice.token.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_invoice_id": self.id,
            },
        }
