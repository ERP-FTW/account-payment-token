# -*- coding: utf-8 -*-
import logging
from odoo import fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PartnerInternalTokenizeWizard(models.TransientModel):
    _name = "partner.internal.tokenize.wizard"
    _description = "Internal Tokenization Wizard (Partner)"

    partner_id = fields.Many2one("res.partner", required=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )

    def action_open_tokenization_page(self):
        self.ensure_one()

        if not self.env.user.has_group("base.group_user"):
            raise UserError(_("Only internal users can tokenize payment methods."))

        _logger.info(
            "[partner_internal_payment_tokenize] Open tokenization page. partner_id=%s company_id=%s user_id=%s",
            self.partner_id.id, self.company_id.id, self.env.user.id,
        )

        url = f"/payment/internal/payment_method/{self.partner_id.id}/{self.company_id.id}"
        return {
            "type": "ir.actions.act_url",
            "name": _("Tokenize Payment Method"),
            "target": "self",
            "url": url,
        }
