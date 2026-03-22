from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient


class CardPointeTerminalConfig(models.Model):
    _name = 'pos.cardpointe.terminal.config'
    _description = 'POS CardPointe Terminal Config'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    base_url = fields.Char(default='https://bolt-uat.cardpointe.com/api', required=True)
    merchant_id = fields.Char(default='800000009875', required=True)
    merchant_config_id = fields.Many2one('cardpointe.merchant.config')
    auth_key = fields.Char(required=True)
    device_type = fields.Selection([('clover_flex', 'Clover Flex')], default='clover_flex', required=True)
    device_serial = fields.Char(string='HSN', help='Terminal hardware serial number (HSN).')
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
    signature_capture_method = fields.Selection(
        [
            ('inline_authcard', 'Inline authCard'),
            ('post_readSignature', 'Post readSignature'),
        ],
        default='inline_authcard',
        required=True,
    )

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

    @api.constrains('signature_mode', 'signature_capture_method')
    def _check_signature_mode_compatibility(self):
        for rec in self:
            mode = rec.signature_mode
            if mode == 'on_policy' and rec.signature_capture_method != 'post_readSignature':
                raise ValidationError(_('On policy mode requires capture method Post readSignature.'))
            if mode in ('always', 'over_threshold') and rec.signature_capture_method != 'inline_authcard':
                raise ValidationError(_('Always/Over threshold modes require capture method Inline authCard.'))

    @api.onchange('merchant_config_id')
    def _onchange_merchant_config_id(self):
        if self.merchant_config_id:
            self.merchant_id = self.merchant_config_id.mid

    @api.onchange('signature_mode')
    def _onchange_signature_mode(self):
        if self.signature_mode == 'on_policy':
            self.signature_capture_method = 'post_readSignature'
        elif self.signature_mode in ('always', 'over_threshold', 'msr_over_threshold'):
            self.signature_capture_method = 'inline_authcard'

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
