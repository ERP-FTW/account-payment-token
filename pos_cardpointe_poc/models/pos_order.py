from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _payment_fields(self, order, ui_paymentline):
        values = super()._payment_fields(order, ui_paymentline)
        values.update({
            'cardpointe_retref': ui_paymentline.get('cardpointe_retref'),
            'cardpointe_authcode': ui_paymentline.get('cardpointe_authcode'),
            'cardpointe_respcode': ui_paymentline.get('cardpointe_respcode'),
            'cardpointe_resptext': ui_paymentline.get('cardpointe_resptext'),
            'cardpointe_token': ui_paymentline.get('cardpointe_token'),
            'cardpointe_status': ui_paymentline.get('cardpointe_status'),
            'cardpointe_original_retref': ui_paymentline.get('cardpointe_original_retref'),
            'cardpointe_operation': ui_paymentline.get('cardpointe_operation'),
            'cardpointe_ok': ui_paymentline.get('cardpointe_ok'),
            'cardpointe_signature_required': ui_paymentline.get('cardpointe_signature_required'),
            'cardpointe_signature_captured': ui_paymentline.get('cardpointe_signature_captured'),
            'cardpointe_signature_method': ui_paymentline.get('cardpointe_signature_method'),
        })
        return values
