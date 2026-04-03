from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    cardpointe_active_request_id = fields.Char(copy=False, index=True)
    cardpointe_active_session_key = fields.Char(copy=False)
    cardpointe_active_request_state = fields.Selection([
        ('ready', 'Ready'),
        ('auth_started', 'Auth Started'),
        ('cancel_requested', 'Cancel Requested'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], copy=False, index=True)
    cardpointe_active_request_uid = fields.Many2one('res.users', copy=False)
    cardpointe_active_started_at = fields.Datetime(copy=False)
    cardpointe_active_payment_line_uuid = fields.Char(copy=False, index=True)
    cardpointe_active_payment_method_id = fields.Many2one('pos.payment.method', copy=False)

    @api.model
    def cardpointe_get_order_for_request(self, request_id):
        return self.search([
            ('cardpointe_active_request_id', '=', request_id),
            ('cardpointe_active_request_state', 'in', ['ready', 'auth_started', 'cancel_requested']),
        ], limit=1)

    def cardpointe_clear_active_request(self):
        self.ensure_one()
        self.write({
            'cardpointe_active_request_state': 'done',
            'cardpointe_active_session_key': False,
        })
