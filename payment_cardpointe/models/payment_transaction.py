# Part of Odoo. See LICENSE file for full copyright and licensing details.
import re
import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation
from odoo import fields, models
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils
from odoo.addons.payment_cardpointe_base.services.surcharge import agreement, quote_fee, money, UnusableSurcharge


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    cp_order_id = fields.Char(readonly=True, copy=False, index=True)
    cp_merchant_id = fields.Char(readonly=True, copy=False)
    cp_submitted_amount = fields.Monetary(readonly=True, copy=False)
    cp_submitted_at = fields.Datetime(readonly=True, copy=False)
    cp_currency_id = fields.Many2one('res.currency', readonly=True, copy=False)
    cp_gateway_identity = fields.Char(readonly=True, copy=False)

    cp_quote_id = fields.Char(readonly=True, copy=False)
    cp_quote_at = fields.Datetime(readonly=True, copy=False)
    cp_quote_binding = fields.Char(readonly=True, copy=False, groups='base.group_system')
    cp_quote = fields.Json(readonly=True, copy=False)
    cp_auth_evidence = fields.Json(readonly=True, copy=False)
    cp_receipt = fields.Json(readonly=True, copy=False)
    cp_fee_hold = fields.Boolean(readonly=True, copy=False)

    def _cp_quote_fingerprint(self, token, postal):
        # Keyed digest, not a stored gateway token or a reversible postal record.
        return utils.generate_access_token(self.id, token, postal, self.amount,
            self.currency_id.id, self.payment_method_id.id, self._cp_current_gateway_identity(),
            self.provider_id.cardpointe_merchant_config_id.mid, self.provider_id.cp_surcharge_program)

    def _cp_prepare_quote(self, token, postal, quote_id):
        p = self.provider_id
        p._cp_check_surcharge_configuration()
        if not isinstance(postal, str) or not re.fullmatch(r'\d{5}(?:-\d{4})?', postal):
            raise ValidationError('Enter the cardholder postal code (12345 or 12345-6789).')
        if self.currency_id.name != 'USD':
            raise ValidationError('The surcharge integration requires USD.')
        fingerprint = self._cp_quote_fingerprint(token, postal)
        if quote_id:
            age = (fields.Datetime.now() - self.cp_quote_at).total_seconds() if self.cp_quote_at else -1
            if (quote_id != self.cp_quote_id or fingerprint != self.cp_quote_binding
                    or not 0 <= age <= 300 or not self.cp_quote):
                self.write({'cp_quote_id': False, 'cp_quote_binding': False, 'cp_quote': False})
                return {'state': 'quote_error', 'message': 'Quote changed or expired. Request a new quote before paying.'}
            return None
        self.write({'cp_quote_id': False, 'cp_quote_binding': False, 'cp_quote': False})
        try:
            response = p._cp_online_request('GET', 'surcharge', {
                'merchid': p.cardpointe_merchant_config_id.mid, 'token': token,
                'postal': postal, 'amount': '%.2f' % self.amount})
            if not isinstance(response, dict) or not response.get('ok'):
                raise UnusableSurcharge('No usable gateway quote.')
            quote = quote_fee(response.get('data'))
        except Exception:
            return {'state': 'quote_error', 'message': 'Gateway quote unavailable. No authorization was sent.'}
        quote.update(subtotal='%.2f' % self.amount,
                     total=str(money('%.2f' % self.amount) + money(quote['fee'])), currency=self.currency_id.name)
        self.write({'cp_quote_id': uuid.uuid4().hex, 'cp_quote_at': fields.Datetime.now(),
                    'cp_quote_binding': fingerprint, 'cp_quote': quote})
        return {'state': 'quote', 'quote_id': self.cp_quote_id, 'quote': quote}

    def _cp_current_gateway_identity(self):
        p = self.provider_id
        cfg = p.cardpointe_merchant_config_id
        identity = [p.id, p.company_id.id, p.state, cfg.id if cfg else False,
                    cfg.gateway_base_url if cfg else p.cardpointe_api_base]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def _cp_document_amount(self):
        """Amount allocated to linked documents, excluding validated addon fees.

        The default is the full transaction amount. Fee addons may override this
        only after validating their frozen principal/fee allocation; authorization
        and consent must still use the full transaction amount.
        """
        self.ensure_one()
        return self.amount

    def _cp_check_documents(self):
        # Document tuple updates force stale repeatable-read snapshots to fail,
        # including a different payer arriving through the same invoice portal.
        for field, table in [('invoice_ids', 'account_move'), ('sale_order_ids', 'sale_order')]:
            if field not in self._fields or not self[field]:
                continue
            docs = self[field].sorted('id')
            if table == 'account_move':
                self.env.cr.execute('UPDATE account_move SET write_date=write_date WHERE id IN %s', [tuple(docs.ids)])
            else:
                self.env.cr.execute('UPDATE sale_order SET write_date=write_date WHERE id IN %s', [tuple(docs.ids)])
            docs.invalidate_recordset()
            # An existing attempt always reconciles, even if its document changed.
            if self.cp_order_id:
                continue
            other = self.search([(field, 'in', docs.ids), ('id', '!=', self.id),
                                 ('state', 'in', ['pending', 'authorized', 'done', 'draft'])])
            if any(t.state in ('pending', 'authorized') or
                   (t.state == 'draft' and t.cp_order_id) or
                   (t.state == 'done' and not t.is_post_processed) for t in other):
                raise ValidationError('This document has an accepted or unresolved payment. Reconcile the original payment first.')
            if any(d.company_id != self.company_id or d.currency_id != self.currency_id for d in docs):
                raise ValidationError('Payment document company or currency mismatch.')
            if table == 'account_move':
                if any(d.state != 'posted' or d.move_type != 'out_invoice' for d in docs):
                    raise ValidationError('The invoice is not payable.')
                outstanding = sum(docs.mapped('amount_residual'))
            else:
                if any(d.state == 'cancel' for d in docs):
                    raise ValidationError('The order has been cancelled.')
                outstanding = sum(d.amount_total - d.amount_paid for d in docs)
            # Installments are valid; reject only an amount above the balance.
            if outstanding <= 0 or self.currency_id.compare_amounts(self._cp_document_amount(), outstanding) > 0:
                raise ValidationError('The document amount changed or was already paid. Refresh before paying.')

    _cp_order_unique = models.Constraint('unique(cp_order_id)', 'Gateway order reference must be unique.')

    def _get_specific_processing_values(self, processing_values):
        values = super()._get_specific_processing_values(processing_values)
        if self.provider_code == 'cardpointe':
            values['access_token'] = utils.generate_access_token(self.reference, self.partner_id.id, self.amount, self.currency_id.id, self.provider_id.id)
        return values

    def _cp_check_provider(self, provider_id):
        self.ensure_one()
        # Bind the displayed provider to the saved processing identity before
        # any quote or authorization. A stale checkout must never switch MID.
        if isinstance(provider_id, bool) or str(provider_id) != str(self.provider_id.id):
            raise ValidationError('Payment provider changed. Refresh the checkout; reconcile any earlier attempt before paying.')

    def _cp_check_payment_method(self, payment_method_code):
        self.ensure_one()
        # The hosted form selected by the payer must match this saved identity.
        # Missing declarations (including stale browser assets) fail closed.
        if not payment_method_code or payment_method_code != self.payment_method_id.code:
            raise ValidationError("Payment method changed. Return to the original method to retry, or refresh before paying.")

    def _cp_extra_payload(self, consent, account_class, consent_amount):
        if self.payment_method_id.code != 'card':
            raise ValidationError('Unsupported CardPointe payment method.')
        return {}

    def _cp_checkpoint(self, durable):
        self.env.flush_all()
        if durable:
            self.env.cr.commit()  # Only the controller's explicitly owned cursor.

    def _cp_process(self, token=None, consent=None, account_class=None, consent_amount=None, durable=False, postal=None, quote_id=None):
        self.ensure_one()
        self._cp_check_documents()
        # Serialize the payer's unresolved debits across tabs/transactions/methods.
        # A tuple update (not just a lock) makes a pre-existing repeatable-read
        # snapshot serialize/fail rather than miss another tab's committed attempt.
        self.env.cr.execute('UPDATE res_partner SET write_date=write_date WHERE id=%s', [self.partner_id.id])
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
            # Never authorize again, even after not-found, a timeout, or config changes.
            if (mid != self.cp_merchant_id or self.amount != self.cp_submitted_amount
                    or self.currency_id != self.cp_currency_id
                    or self._cp_current_gateway_identity() != self.cp_gateway_identity):
                return {'state': 'pending', 'message': 'Original merchant or amount changed; manual reconciliation required.'}
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
        held = self.search([('partner_id','=',self.partner_id.id),('company_id','=',self.company_id.id),('provider_code','=','cardpointe'),('cp_order_id','!=',False),('state','in',['pending','draft']),('id','!=',self.id)], limit=1)
        if held:
            raise ValidationError('A previous payment is unresolved. Inquire on that original payment; do not submit another debit.')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9]{10,64}', token):
            raise ValidationError('Complete the secure hosted payment form.')
        extra = self._cp_extra_payload(consent, account_class, consent_amount)
        if self.payment_method_id.code == 'card' and p.cp_surcharge_program:
            quote_result = self._cp_prepare_quote(token, postal, quote_id)
            if quote_result:
                return quote_result
            extra['postal'] = postal
        elif self.cp_quote:
            raise ValidationError('Program configuration changed after quotation. Reconcile configuration before paying.')
        self.write({'cp_order_id': 'CP'+uuid.uuid4().hex[:16], 'cp_merchant_id': mid, 'cp_submitted_amount': self.amount, 'cp_submitted_at': fields.Datetime.now(), 'cp_currency_id': self.currency_id.id, 'cp_gateway_identity': self._cp_current_gateway_identity()})
        self._set_pending(state_message='Submission outcome unknown. Do not pay again; inquire using the original reference.')
        payload = {'merchid': mid, 'orderid': self.cp_order_id, 'amount': '%.2f' % self.amount, 'currency': self.currency_id.name, 'account': token, 'capture': 'y', 'ecomind': 'E', **extra}
        self._cp_checkpoint(durable)
        try:
            response = p._cp_online_request('PUT', 'auth', payload)
        except Exception:
            # An exception after send is UNKNOWN, never a decline or permission to retry.
            response = {}
        finally:
            payload.pop('account', None)
        self._cp_apply_response(response)
        return {'state': self.state, 'redirect_url': '/payment/status'}

    def _cp_apply_response(self, response):
        self.env.cr.execute('SELECT id FROM payment_transaction WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.state in ('done', 'authorized', 'error', 'cancel'):
            return
        if self.cp_fee_hold:
            # A contradictory approval requires manual reconciliation; neither
            # a later decline nor an approval may silently release this hold.
            return
        data = response.get('data') if isinstance(response, dict) else None
        if not isinstance(response, dict) or not response.get('ok') or not isinstance(data, dict):
            return
        matches = str(data.get('merchid')) == self.cp_merchant_id and data.get('orderid') == self.cp_order_id
        if self.cp_quote and not self.cp_fee_hold:
            evidence = {'identity_matches': matches, 'approved': data.get('respstat') == 'A'}
            for key in ('amount', 'fee_amount'):
                try:
                    evidence[key] = str(money(data.get(key)))
                except UnusableSurcharge:
                    evidence[key] = None
            if data.get('fee_type') in ('SURCHRG', 'SURCHRG_WAIVED'):
                evidence['fee_type'] = data['fee_type']
            if isinstance(data.get('retref'), str) and re.fullmatch(r'[A-Za-z0-9-]{1,64}', data['retref']):
                evidence['reference'] = data['retref']
            self.cp_auth_evidence = evidence
        if data.get('currency') and data['currency'] != self.cp_currency_id.name:
            matches = False
        if not matches:
            return
        # Declines do not describe a charged total and may omit fee fields.
        # Only an identity-matched approval can establish fee agreement/hold.
        receipt = None
        if data.get('respstat') == 'A':
            try:
                receipt = agreement(data, '%.2f' % self.cp_submitted_amount,
                    quoted_fee=self.cp_quote['fee'] if self.cp_quote else None,
                    required=bool(self.cp_quote))
                if not self.cp_quote and money(receipt['fee']) != 0:
                    raise UnusableSurcharge('Undisclosed gateway program fee; reconcile merchant configuration.')
            except UnusableSurcharge:
                self.cp_fee_hold = True
                return
        ref = data.get('retref')
        if data.get('respstat') == 'A' and isinstance(ref, str) and re.fullmatch(r'[A-Za-z0-9-]{1,64}',ref):
            self.provider_reference = ref
            if self.cp_quote:
                self.cp_receipt = dict(receipt, reference=ref, order_id=self.cp_order_id, currency=self.cp_currency_id.name, agreement=True)
            self._set_done(state_message='Payment submission accepted by the gateway. This is not confirmation of bank funding.')
        elif data.get('respstat') == 'C':
            self._set_error('Payment declined by the gateway. No bank funding is claimed.')
