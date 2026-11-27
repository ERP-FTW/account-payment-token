from odoo import http
from odoo.addons.payment import utils
from odoo.http import request


class CardPointeReceipt(http.Controller):
    @http.route('/payment/cardpointe/receipt', type='http', auth='public', website=True)
    def receipt(self, reference, access_token):
        tx = request.env['payment.transaction'].sudo().search([('reference', '=', reference), ('provider_code', '=', 'cardpointe')], limit=1)
        if not tx or not utils.check_access_token(access_token, tx.reference, tx.partner_id.id, tx.amount, tx.currency_id.id, tx.provider_id.id) or not tx.cp_receipt:
            return request.not_found()
        return request.render('account_payment_markup_cardpointe.program_receipt', {'receipt': tx.cp_receipt}, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
