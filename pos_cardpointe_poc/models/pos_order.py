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
            'cardpointe_entrymode': ui_paymentline.get('cardpointe_entrymode'),
            'cardpointe_emvtagdata': ui_paymentline.get('cardpointe_emvtagdata'),
            'cardpointe_capture_method': ui_paymentline.get('cardpointe_capture_method'),
            'cardpointe_ecomind': ui_paymentline.get('cardpointe_ecomind'),
            'cardpointe_fallback_reason': ui_paymentline.get('cardpointe_fallback_reason'),
            'cardpointe_terminal_error_status': ui_paymentline.get('cardpointe_terminal_error_status'),
            'cardpointe_terminal_error_message': ui_paymentline.get('cardpointe_terminal_error_message'),
            'cardpointe_gateway_http_status': ui_paymentline.get('cardpointe_gateway_http_status'),
        })
        return values
