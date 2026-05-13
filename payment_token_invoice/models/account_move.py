from odoo import api, fields, models, _


class AccountMove(models.Model):
    _inherit = "account.move"

    has_saved_payment_token = fields.Boolean(
        string="Has Saved Payment Token",
        compute="_compute_has_saved_payment_token",
    )

    @api.depends("partner_id", "company_id", "move_type")
    def _compute_has_saved_payment_token(self):
        PaymentToken = self.env["payment.token"]

        for move in self:
            move.has_saved_payment_token = False

            if not move.partner_id or move.move_type != "out_invoice":
                continue

            partner = move.partner_id.commercial_partner_id
            domain = [
                ("partner_id", "=", partner.id),
                ("company_id", "in", [move.company_id.id, False]),
            ]

            if "active" in PaymentToken._fields:
                domain.append(("active", "=", True))

            move.has_saved_payment_token = bool(PaymentToken.search_count(domain))

    def action_open_token_payment_wizard(self):
        """Open the token payment wizard for this invoice."""
        self.ensure_one()
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