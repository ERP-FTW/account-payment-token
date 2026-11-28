# Part of Odoo. See LICENSE file for full copyright and licensing details.
import re
import math
import uuid
from odoo import fields, models
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    cp_order_id = fields.Char(readonly=True, copy=False, index=True)
    cp_merchant_id = fields.Char(readonly=True, copy=False)
    cp_submitted_amount = fields.Monetary(readonly=True, copy=False)
    cp_submitted_at = fields.Datetime(readonly=True, copy=False)
    cp_currency_id = fields.Many2one('res.currency', readonly=True, copy=False)

    _cp_order_unique = models.Constraint('unique(cp_order_id)', 'Gateway order reference must be unique.')

    def _get_specific_processing_values(self, processing_values):
        values = super()._get_specific_processing_values(processing_values)
        if self.provider_code == 'cardpointe':
            values['access_token'] = utils.generate_access_token(self.reference, self.partner_id.id, self.amount, self.currency_id.id, self.provider_id.id)
        return values

    def _cp_prepare_authorization(self, token, options):
        if self.payment_method_id.code != 'card':
            raise ValidationError('Unsupported CardPointe payment method.')
        return None, {}

    def _cp_process(self, token=None, *, durable=False, **options):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM payment_transaction WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        p = self.provider_id
        cfg = p.cardpointe_merchant_config_id
        mid = cfg.mid if cfg else p.cardpointe_mid
        if self.provider_code != 'cardpointe' or p.company_id != self.company_id or (cfg and cfg.company_id != self.company_id):
            raise ValidationError('Payment merchant/company mismatch.')
        if self.state in ('done', 'authorized', 'error', 'cancel'):
            return {'state': self.state, 'redirect_url': '/payment/status'}
        if self.cp_order_id:
            try:
                response = p._cp_online_request('GET', 'inquireByOrderid/%s/%s' % (self.cp_order_id, self.cp_merchant_id))
            except Exception:
                response = {}
            self._cp_apply_response(response)
            return {'state': self.state, 'redirect_url': '/payment/status'}
        if p.state not in ('enabled', 'test') or self.operation != 'online_direct' or self.amount <= 0 or not mid:
            raise ValidationError('Online payment is not available.')
        if self.payment_method_id not in p.payment_method_ids or not self.payment_method_id.active:
            raise ValidationError('This payment method is not enabled.')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9]{10,64}', token):
            raise ValidationError('Complete the secure hosted payment form.')
        result, extra = self._cp_prepare_authorization(token, options)
        if result:
            return result
        self.write({
            'cp_order_id': 'CP' + uuid.uuid4().hex[:16],
            'cp_merchant_id': mid,
            'cp_submitted_amount': self.amount,
            'cp_submitted_at': fields.Datetime.now(),
            'cp_currency_id': self.currency_id.id,
        })
        self._set_pending(state_message='Waiting for CardPointe to confirm this payment.')
        payload = {'merchid': mid, 'orderid': self.cp_order_id, 'amount': '%.2f' % self.amount, 'currency': self.currency_id.name, 'account': token, 'capture': 'y', 'ecomind': 'E', **extra}
        if durable:
            self.env.cr.commit()  # Save the order before sending the debit.
        try:
            response = p._cp_online_request('PUT', 'auth', payload)
        except Exception:
            # An exception after send is UNKNOWN, never a decline or permission to retry.
            response = {}
        finally:
            payload.pop('account', None)
        self._cp_apply_response(response, direct=True)
        return {'state': self.state, 'redirect_url': '/payment/status'}

    def _cp_apply_response(self, response, *, direct=False):
        self.env.cr.execute('SELECT id FROM payment_transaction WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.state in ('done', 'authorized', 'error', 'cancel') or not response:
            return
        if not response.get('ok'):
            status = response.get('http_status')
            if direct and status in (400, 401, 403, 404, 405, 406, 415, 422):
                self._set_error(f'CardPointe rejected the authorization request (HTTP {status}).')
            return
        data = response.get('data')
        if not isinstance(data, dict):
            return

        # Direct replies belong to this HTTP request. Inquiries must identify the saved order.
        order_ids = [data[k] for k in ('orderId', 'orderid') if k in data]
        if not direct and (not order_ids or not data.get('merchid')):
            return
        if any(value != self.cp_order_id for value in order_ids):
            return
        if data.get('merchid') and str(data['merchid']) != self.cp_merchant_id:
            return
        if data.get('retref'):
            self._cardpointe_set_provider_reference(data)
        self._process('cardpointe', data)

    def _extract_amount_data(self, payment_data):
        if self.provider_code != 'cardpointe':
            return super()._extract_amount_data(payment_data)
        if payment_data.get('respstat') != 'A' or not payment_data.get('retref'):
            return None
        try:
            amount = float(payment_data['amount'])
        except (KeyError, ValueError, TypeError):
            amount = 0.0
        return {
            'amount': amount if math.isfinite(amount) else 0.0,
            'currency_code': payment_data.get('currency') or self.cp_currency_id.name,
        }

    def _apply_updates(self, payment_data):
        if self.provider_code != 'cardpointe':
            return super()._apply_updates(payment_data)
        status = payment_data.get('respstat')
        # A processor description can include the rejected account number.
        description = re.sub(r'\d{6,}', '***', str(payment_data.get('resptext') or '')[:180])
        detail = ' '.join(str(item) for item in
                          (payment_data.get('respproc'), payment_data.get('respcode'), description) if item)
        message = f'CardPointe: {detail}' if detail else ''
        if status == 'A' and payment_data.get('retref'):
            self._set_done(state_message=message or 'CardPointe accepted the payment.')
        elif status == 'C':
            self._set_error(message or 'CardPointe declined the payment.')
        elif message:
            self.state_message = message
