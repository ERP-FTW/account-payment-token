# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import api, http, SUPERUSER_ID
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing


class CardPointeController(http.Controller):
    @http.route('/payment/cardpointe/process', type='json', auth='public')
    def process(self, reference, access_token, token=None, consent=None, account_class=None, consent_amount=None, payment_method_code=None):
        # This owned cursor permits the durable pre-send checkpoint. The HTTP cursor
        # never owns the debit and a request rollback cannot erase an attempted send.
        with request.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            tx = env['payment.transaction'].search([('reference', '=', reference)], limit=1)
            if not tx or not utils.check_access_token(access_token, tx.reference, tx.partner_id.id, tx.amount, tx.currency_id.id, tx.provider_id.id):
                raise ValidationError('Invalid payment access.')
            tx._cp_check_payment_method(payment_method_code)
            result = tx._cp_process(token, consent, account_class, consent_amount, durable=True)
            cr.commit()
            PaymentPostProcessing.monitor_transaction(tx)
            if 'sale_order_ids' in tx._fields and tx.sale_order_ids:
                request.session['__website_sale_last_tx_id'] = tx.id
        return result
