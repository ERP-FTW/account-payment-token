import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient

_logger = logging.getLogger(__name__)


class CardPointeTerminalConfig(models.Model):
    _name = 'pos.cardpointe.terminal.config'
    _description = 'POS CardPointe Terminal Config'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    base_url = fields.Char(default='https://bolt-uat.cardpointe.com/api', required=True)
    merchant_config_id = fields.Many2one('cardpointe.merchant.config', required=True)
    merchant_id = fields.Char(related='merchant_config_id.mid', store=True, readonly=True)
    auth_key = fields.Char(required=True)
    device_type = fields.Selection(
        [
            ('clover_pocket', 'Clover Pocket'),
            ('clover_flex', 'Clover Flex'),
            ('clover_mini', 'Clover Mini'),
        ],
        default='clover_pocket',
        required=True,
    )
    device_serial = fields.Char(string='HSN', help='Terminal hardware serial number (HSN).')
    print_receipt_on_terminal = fields.Boolean(
        string='Print Receipt on Terminal',
        default=False,
        help='Enable receipt printing on supported Clover terminals with built-in printers.',
    )
    request_timeout_seconds = fields.Integer(default=120, required=True)
    signature_mode = fields.Selection(
        [
            ('never', 'Never'),
            ('always', 'Always'),
            ('over_threshold', 'Over threshold'),
            ('on_policy', 'On policy (EMV CVM)'),
            ('msr_over_threshold', 'Legacy: MSR over threshold'),
        ],
        default='over_threshold',
        required=True,
        help=(
            "Over threshold: Always request signature when amount >= threshold (any entry mode).\n"
            "On policy: Capture signature only when EMV indicates signature is applicable (post-transaction)."
        ),
    )
    signature_threshold_amount = fields.Float(default=50.0, required=True)
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
    cardpointe_active_order_uid = fields.Char(copy=False, index=True)
    cardpointe_active_payment_line_uuid = fields.Char(copy=False, index=True)
    cardpointe_active_payment_method_id = fields.Many2one('pos.payment.method', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalize_signature_mode(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._normalize_signature_mode(vals)
        return super().write(vals)

    @staticmethod
    def _normalize_signature_mode(vals):
        if vals.get('signature_mode') == 'msr_over_threshold':
            vals['signature_mode'] = 'over_threshold'

    @api.model
    def cardpointe_get_config_for_request(self, request_id):
        return self.search([
            ('cardpointe_active_request_id', '=', request_id),
            ('cardpointe_active_request_state', 'in', ['ready', 'auth_started', 'cancel_requested']),
        ], limit=1)

    def cardpointe_clear_active_request(self):
        self.ensure_one()
        self.write({
            'cardpointe_active_request_id': False,
            'cardpointe_active_request_state': 'done',
            'cardpointe_active_session_key': False,
            'cardpointe_active_request_uid': False,
            'cardpointe_active_order_uid': False,
            'cardpointe_active_payment_line_uuid': False,
            'cardpointe_active_payment_method_id': False,
        })

    def cardpointe_active_request_is_stale(self):
        self.ensure_one()
        if not self.cardpointe_active_session_key:
            return False
        if self.cardpointe_active_request_state not in ('ready', 'auth_started', 'cancel_requested'):
            return False
        if not self.cardpointe_active_started_at:
            return False
        timeout = self.request_timeout_seconds or 120
        grace_seconds = max(timeout * 2, 300)
        age = fields.Datetime.now() - self.cardpointe_active_started_at
        return age.total_seconds() > grace_seconds

    def cardpointe_clear_stale_active_request(self):
        self.ensure_one()
        if not self.cardpointe_active_request_is_stale():
            return False
        _logger.warning(
            'CardPointe stale active request cleared config_id=%s request_id=%s state=%s started_at=%s order_uid=%s payment_line_uuid=%s',
            self.id,
            self.cardpointe_active_request_id,
            self.cardpointe_active_request_state,
            self.cardpointe_active_started_at,
            self.cardpointe_active_order_uid,
            self.cardpointe_active_payment_line_uuid,
        )
        self.cardpointe_clear_active_request()
        return True

    def action_cardpointe_clear_active_request(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can clear active CardPointe requests.'))
        _logger.warning(
            'CardPointe active request manually cleared by user_id=%s config_id=%s request_id=%s state=%s started_at=%s order_uid=%s payment_line_uuid=%s',
            self.env.user.id,
            self.id,
            self.cardpointe_active_request_id,
            self.cardpointe_active_request_state,
            self.cardpointe_active_started_at,
            self.cardpointe_active_order_uid,
            self.cardpointe_active_payment_line_uuid,
        )
        self.cardpointe_clear_active_request()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('CardPointe Runtime State'),
                'message': _('Active CardPointe request cleared.'),
                'type': 'success',
                'sticky': False,
            },
        }

    @api.onchange('merchant_config_id')
    def _onchange_merchant_config_id(self):
        if self.merchant_config_id:
            self.merchant_id = self.merchant_config_id.mid

    def action_test_connect(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can run terminal connect tests.'))

        client = CardPointeTerminalClient(self)
        result = client.connect()

        message = _('Connect failed.')
        msg_type = 'warning'

        if result.get('ok'):
            # IMPORTANT: free the terminal session immediately so POS can use it
            session_key = result.get('session_key')
            disconnect_msg = ''
            if session_key:
                disc = client.disconnect(session_key)
                if not disc.get('ok'):
                    # We still report connect success, but warn admin the session may remain open
                    disconnect_msg = _(' (but disconnect failed: %s)') % (disc.get('message') or 'unknown error')
                    msg_type = 'warning'
                else:
                    disconnect_msg = _(' (disconnect OK)')
            message = _('Connect succeeded. Session key returned by terminal API.%s') % disconnect_msg
            if msg_type != 'warning':
                msg_type = 'success'
        elif result.get('message'):
            message = _('Connect failed: %s') % result.get('message')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('CardPointe Test Connect'),
                'message': message,
                'type': msg_type,
                'sticky': False,
            },
        }
