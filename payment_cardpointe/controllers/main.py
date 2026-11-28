# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import api, http, SUPERUSER_ID
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing


class CardPointeController(http.Controller):
    @http.route('/payment/cardpointe/process', type='jsonrpc', auth='public')
    def process(self, reference, access_token, token=None, **options):
        # This owned cursor permits the durable pre-send checkpoint. The HTTP cursor
        # never owns the debit and a request rollback cannot erase an attempted send.
        with request.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            tx = env['payment.transaction'].search([('reference', '=', reference)], limit=1)
            if not tx or not utils.check_access_token(access_token, tx.reference, tx.partner_id.id, tx.amount, tx.currency_id.id, tx.provider_id.id):
                raise ValidationError('Invalid payment access.')
            options.pop('durable', None)
            result = tx._cp_process(token, durable=True, **options)
            cr.commit()
            PaymentPostProcessing.monitor_transaction(tx)
        return result


class CardPointePaymentStatus(PaymentPostProcessing):
    @http.route()
    def display_status(self, **kwargs):
        tx = self._get_monitored_transaction()
        if tx.provider_code == 'cardpointe' and tx.state == 'pending' and tx.cp_order_id:
            tx._cp_process()  # Reading the status of a submitted order only performs an inquiry.
        return super().display_status(**kwargs)
