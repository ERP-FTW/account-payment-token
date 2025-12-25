# -*- coding: utf-8 -*-
import logging
from odoo import models

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = "res.partner"

    def action_open_internal_tokenize_wizard(self):
        self.ensure_one()
        _logger.info(
            "[partner_internal_payment_tokenize] Open tokenize wizard for partner_id=%s (%s)",
            self.id, self.display_name,
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Add Card on File (Tokenize)",
            "res_model": "partner.internal.tokenize.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id},
        }
