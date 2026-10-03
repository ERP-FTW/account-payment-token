import logging
from decimal import Decimal

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.payment_cardpointe_base.services.gateway import CardPointeGatewayClient
from odoo.addons.payment_cardpointe_base.services.money import format_gateway_amount
from odoo.addons.payment_cardpointe_base.services.refunds import execute_void_or_refund

_logger = logging.getLogger(__name__)


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    _CARDPOINTE_POS_PAYMENT_BASE_FIELDS = [
        'id',
        'name',
        'pos_order_id',
        'amount',
        'payment_method_id',
        'payment_date',
        'currency_id',
        'currency_rate',
        'partner_id',
        'session_id',
        'user_id',
        'company_id',
        'card_type',
        'card_brand',
        'card_no',
        'cardholder_name',
        'payment_ref_no',
        'payment_method_authcode',
        'payment_method_issuer_bank',
        'payment_method_payment_mode',
        'transaction_id',
        'payment_status',
        'ticket',
        'is_change',
        'account_move_id',
        'uuid',
    ]

    cardpointe_retref = fields.Char()
    cardpointe_authcode = fields.Char()
    cardpointe_respcode = fields.Char()
    cardpointe_resptext = fields.Char()
    cardpointe_token = fields.Char()
    cardpointe_entrymode = fields.Char()
    cardpointe_emvtagdata = fields.Text()
    cardpointe_status = fields.Char()
    cardpointe_original_retref = fields.Char(help='Original sale retref used for this CardPointe void/refund.')
    cardpointe_operation = fields.Selection([
        ('sale', 'Sale'),
        ('void', 'Void'),
        ('refund', 'Refund'),
    ])
    cardpointe_ok = fields.Boolean(default=False)
    cardpointe_signature_required = fields.Boolean(default=False)
    cardpointe_signature_captured = fields.Boolean(default=False)
    cardpointe_signature_method = fields.Selection([
        ('inline_authcard', 'Inline authCard'),
        ('post_readSignature', 'Post readSignature'),
    ])
    cardpointe_request_id = fields.Char(copy=False, index=True)
    cardpointe_session_key = fields.Char(copy=False)
    cardpointe_request_state = fields.Selection([
        ('ready', 'Ready'),
        ('auth_started', 'Auth Started'),
        ('cancel_requested', 'Cancel Requested'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], copy=False, index=True)
    cardpointe_request_uid = fields.Many2one('res.users', copy=False)
    cardpointe_request_started_at = fields.Datetime(copy=False)
    cardpointe_request_order_uid = fields.Char(copy=False, index=True)
    cardpointe_request_payment_line_uuid = fields.Char(copy=False, index=True)
    cardpointe_terminal_order_id = fields.Char(
        copy=False, index=True,
        help='orderId sent to the terminal; the key for the Gateway inquiry by order id.',
    )

    cardpointe_capture_method = fields.Selection([
        ('terminal', 'Terminal'),
        ('iframe_manual', 'Manual iFrame'),
        ('external_reference', 'External Reference'),
    ], default='terminal', string='CardPointe Capture Method')
    cardpointe_ecomind = fields.Selection([
        ('E', 'Ecommerce'),
        ('T', 'Telephone/Mail'),
        ('R', 'Recurring'),
    ], string='CardPointe ecomind')
    cardpointe_fallback_reason = fields.Selection([
        ('manual_selected', 'Manual selected'),
        ('terminal_error', 'Terminal error'),
        ('terminal_timeout', 'Terminal timeout'),
        ('terminal_in_use', 'Terminal in use'),
        ('terminal_merchant_mode', 'Terminal merchant mode'),
        ('server_error', 'Server error'),
    ], string='Manual Entry Reason')
    cardpointe_terminal_error_status = fields.Char()
    cardpointe_terminal_error_message = fields.Char()
    cardpointe_gateway_http_status = fields.Integer()
    cardpointe_refund_hold = fields.Char(
        string='CardPointe return hold', copy=False, readonly=True,
        help='Set when a CardPointe void/refund against this payment may have moved money without '
             'Odoo recording it (no answer, or a multi-payment return that stopped part way). Returns '
             'against this sale are refused until a POS manager has checked CardPointe reporting and '
             'cleared the hold.',
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields_list = list(self._CARDPOINTE_POS_PAYMENT_BASE_FIELDS)
        fields_list += [
            'cardpointe_retref',
            'cardpointe_authcode',
            'cardpointe_respcode',
            'cardpointe_resptext',
            'cardpointe_token',
            'cardpointe_entrymode',
            'cardpointe_emvtagdata',
            'cardpointe_status',
            'cardpointe_original_retref',
            'cardpointe_operation',
            'cardpointe_ok',
            'cardpointe_signature_required',
            'cardpointe_signature_captured',
            'cardpointe_signature_method',
            'cardpointe_capture_method',
            'cardpointe_ecomind',
            'cardpointe_fallback_reason',
            'cardpointe_terminal_error_status',
            'cardpointe_terminal_error_message',
            'cardpointe_gateway_http_status',
            'cardpointe_request_id',
            'cardpointe_session_key',
            'cardpointe_request_state',
            'cardpointe_request_uid',
            'cardpointe_request_started_at',
            'cardpointe_request_order_uid',
            'cardpointe_request_payment_line_uuid',
            'cardpointe_terminal_order_id',
        ]
        return list(dict.fromkeys(fields_list))

    @api.model
    def cardpointe_get_payment_for_request(self, request_id):
        return self.search([
            ('cardpointe_request_id', '=', request_id),
            ('cardpointe_request_state', 'in', ['ready', 'auth_started', 'cancel_requested']),
        ], limit=1)

    def cardpointe_clear_request(self):
        self.ensure_one()
        self.write({
            'cardpointe_request_state': 'done',
            'cardpointe_session_key': False,
        })

    def _cardpointe_get_merchant_config(self, terminal_config):
        merchant_config = terminal_config.merchant_config_id
        if merchant_config:
            return merchant_config

        merchant_config = self.env['cardpointe.merchant.config'].search([
            ('company_id', '=', terminal_config.company_id.id),
            ('mid', '=', terminal_config.merchant_id),
        ], limit=1)
        if merchant_config:
            terminal_config.merchant_config_id = merchant_config.id
            return merchant_config
        return merchant_config

    @api.model
    def cardpointe_process_refund(self, payment_method_id, amount, refunded_orderline_ids):
        payment_method = self.env['pos.payment.method'].browse(payment_method_id).exists()
        if not payment_method or payment_method.use_payment_terminal != 'cardpointe_poc':
            raise UserError(_('Invalid CardPointe payment method.'))

        config = payment_method.cardpointe_config_id
        if not config:
            raise UserError(_('CardPointe config missing on payment method.'))
        company = payment_method.company_id
        if company and company not in self.env.user.company_ids:
            raise UserError(_('You cannot refund with a payment method of another company.'))
        merchant_config = self._cardpointe_get_merchant_config(config)
        if not merchant_config:
            raise UserError(_('CardPointe merchant config missing on terminal config.'))
        if company and (config.company_id != company or merchant_config.company_id != company):
            raise UserError(_('CardPointe terminal or merchant configuration belongs to another company.'))
        # Credentials are system-only fields: read them only after the checks above.
        merchant_config = merchant_config.sudo()
        if not merchant_config.gateway_username or not merchant_config.gateway_password:
            raise UserError(_('CardPointe gateway credentials are missing on merchant config.'))

        refund_amount = Decimal(format_gateway_amount(abs(amount or 0)))
        if refund_amount <= 0:
            raise UserError(_('Refund amount must be greater than zero.'))

        allocations = self._cardpointe_build_refund_allocations(
            payment_method=payment_method,
            refunded_orderline_ids=refunded_orderline_ids,
            refund_amount=refund_amount,
        )
        gateway = CardPointeGatewayClient(merchant_config)
        results = []
        for allocation in allocations:
            result = execute_void_or_refund(
                gw_client=gateway,
                merchid=merchant_config.mid,
                retref=allocation['retref'],
                amount=format_gateway_amount(allocation['amount']),
                orderid=allocation.get('order_uid'),
            )
            if result.get('unknown'):
                # No answer: the void/refund may have been applied. Hold the sale so neither this line
                # nor a new return can send it again before someone has checked CardPointe.
                reason = _(
                    'CardPointe did not answer the %(operation)s of %(amount)s for reference %(retref)s; '
                    'its outcome is unknown.',
                    operation=result.get('operation') or 'refund', amount=allocation['amount'],
                    retref=allocation['retref'],
                )
                return self._cardpointe_hold_refund(allocations, results, allocation, result, reason)
            if not result.get('ok') and results:
                # An earlier payment of this sale was already refunded: a retry would refund it again.
                reason = _(
                    'CardPointe refused the return for reference %(retref)s (%(message)s) after an earlier '
                    'payment of the same sale was already returned.',
                    retref=allocation['retref'],
                    message=result.get('resptext') or result.get('message') or _('no reason given'),
                )
                return self._cardpointe_hold_refund(allocations, results, allocation, result, reason)
            if not result.get('ok'):
                message = result.get('resptext') or result.get('message') or _('CardPointe refund failed.')
                lowered = (message or '').lower()
                if 'in use' in lowered:
                    message = _('Terminal is in use, retry in a few seconds.')
                elif 'invalid' in lowered and 'retref' in lowered:
                    message = _('Invalid CardPointe reference on original payment.')
                elif 'already' in lowered and 'refund' in lowered:
                    message = _('This payment appears to be already refunded.')
                elif 'connection' in lowered or 'timeout' in lowered:
                    message = _('CardPointe gateway is unavailable. Please retry in a few seconds.')
                raise UserError(message)

            results.append(dict(result, source_retref=allocation['retref']))

        if not results:
            raise UserError(_('No refundable CardPointe transaction was found for this return order.'))

        primary = results[0]
        operation = 'void' if any(r.get('operation') == 'void' for r in results) else 'refund'
        retrefs = ','.join(filter(None, [r.get('retref') for r in results]))
        source_retrefs = ','.join(a['retref'] for a in allocations)
        resptexts = ' | '.join(filter(None, [r.get('resptext') for r in results]))

        return {
            'status': 'approved',
            'operation': operation,
            'retref': retrefs,
            'respcode': primary.get('respcode') or '',
            'resptext': resptexts or _('Refund approved.'),
            'original_retref': source_retrefs,
            'ok': True,
        }

    def _cardpointe_hold_refund(self, allocations, applied_results, stopped_allocation, stopped_result,
                                reason):
        """Record that money may have moved for these sale payments without a completed return.

        Holds the sale payment whose operation stopped and every one already returned in this call,
        and answers the POS with ``unknown`` so the line is not offered for another attempt.
        """
        applied = [r['source_retref'] for r in applied_results]
        held_retrefs = applied + [stopped_allocation['retref']]
        payment_ids = [a['payment_id'] for a in allocations if a['retref'] in held_retrefs]
        stamp = fields.Datetime.now()
        message = reason
        if applied:
            refs = ', '.join(
                f"{r['source_retref']} ({' '.join(filter(None, [r.get('operation'), r.get('retref')]))})"
                for r in applied_results
            )
            message += ' ' + _('Already returned in this attempt: %(refs)s.', refs=refs)
        self.browse(payment_ids).sudo().write({'cardpointe_refund_hold': f'{stamp} {message}'[:1000]})
        _logger.warning('CardPointe return held payments=%s reason=%s', payment_ids, message)
        return {
            'status': 'unknown',
            'ok': False,
            'message': message + ' ' + _(
                'Do not retry this return. A POS manager must check CardPointe reporting, then clear the '
                'hold on the original payment before another return of this sale.'),
            'operation': stopped_result.get('operation') or 'refund',
            'original_retref': ','.join(held_retrefs),
            'retref': ','.join(filter(None, [r.get('retref') for r in applied_results])),
        }

    def action_cardpointe_clear_refund_hold(self):
        if not self.env.user.has_group('point_of_sale.group_pos_manager'):
            raise AccessError(_('Only a POS manager can clear a CardPointe return hold.'))
        for payment in self.filtered('cardpointe_refund_hold'):
            _logger.info('CardPointe return hold cleared payment=%s by user=%s (was: %s)',
                         payment.id, self.env.user.id, payment.cardpointe_refund_hold)
        self.sudo().write({'cardpointe_refund_hold': False})
        return True

    def _cardpointe_build_refund_allocations(self, payment_method, refunded_orderline_ids, refund_amount):
        if not refunded_orderline_ids:
            raise UserError(_('No refunded order lines were found. Please refund from a paid ticket.'))

        lines = self.env['pos.order.line'].browse(refunded_orderline_ids).exists()
        original_orders = lines.mapped('order_id')
        if not original_orders:
            raise UserError(_('Original order for refund could not be determined.'))
        if payment_method.company_id and any(
            order.company_id != payment_method.company_id for order in original_orders
        ):
            raise UserError(_('The refunded order belongs to another company.'))
        original_order_ids = original_orders.ids

        original_payments = self.search([
            ('pos_order_id', 'in', original_order_ids),
            ('payment_method_id', '=', payment_method.id),
            ('cardpointe_retref', '!=', False),
            ('amount', '>', 0),
        ], order='id asc')
        if not original_payments:
            raise UserError(_('No original CardPointe payment reference found for this refund.'))
        held = original_payments.filtered('cardpointe_refund_hold')
        if held:
            raise UserError(_(
                'A previous return of this sale may have moved money without being recorded (%(hold)s). '
                'A POS manager must check CardPointe reporting and clear the hold on the original payment '
                'before another return.', hold=held[0].cardpointe_refund_hold,
            ))

        allocations = []
        remaining = Decimal(format_gateway_amount(refund_amount))
        for payment in original_payments:
            if remaining <= 0:
                break

            paid_amount = Decimal(format_gateway_amount(payment.amount))
            refunded_total = abs(sum(self.search([
                ('payment_method_id', '=', payment_method.id),
                ('cardpointe_original_retref', '=', payment.cardpointe_retref),
                ('amount', '<', 0),
                ('cardpointe_status', '=', 'approved'),
            ]).mapped('amount')))
            refunded_total = Decimal(format_gateway_amount(refunded_total))
            available = paid_amount - refunded_total
            if available <= 0:
                continue

            allocated = min(available, remaining)
            allocations.append({
                'payment_id': payment.id,
                'retref': payment.cardpointe_retref,
                'amount': allocated,
                'order_uid': payment.pos_order_id.pos_reference or payment.pos_order_id.name,
            })
            remaining -= allocated

        if remaining > 0:
            raise UserError(_('Return amount exceeds remaining refundable amount on original CardPointe transactions.'))

        return allocations
