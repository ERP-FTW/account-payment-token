# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)

ENDPOINT_CHARGE = "/auth"


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_processing_values(self, processing_values):
        self.ensure_one()
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != 'cardpointe':
            return res
        res['access_token'] = payment_utils.generate_access_token(
            processing_values['partner_id'],
            processing_values['amount'],
            processing_values['currency_id'],
        )
        return res

    def _cardpointe_set_provider_reference(self, raw):
        self.ensure_one()
        provider_ref = (
            raw.get('retref')
            or raw.get('reference')
            or raw.get('profileid')
            or self.provider_reference
        )
        if provider_ref:
            self.provider_reference = provider_ref

    def _cardpointe_done(self, message=None):
        self.ensure_one()
        self._set_done(state_message=message or _("CardPointe payment approved."))

    def _cardpointe_fail(self, message, code=None):
        self.ensure_one()
        full_message = ("%s%s" % (("[%s] " % code) if code else "", message or _("Payment failed."))).strip()
        self._set_error(full_message)

    def _cardpointe_charge_from_token(self, token, meta=None):
        self.ensure_one()
        if self.provider_code != 'cardpointe':
            return {'ok': False, 'message': _("CardPointe: invalid provider.")}

        if self.state in ('done', 'authorized') or self.provider_reference:
            message = _("CardPointe: transaction already processed.")
            self._cardpointe_fail(message)
            return {'ok': False, 'message': message}

        provider = self.provider_id
        mid = provider.cardpointe_mid
        if not mid:
            raise UserError(_("CardPointe: missing MID on the payment provider."))

        token_last4 = token[-4:] if isinstance(token, str) else '****'
        _logger.info("[CARDPOINTE] charge request tx_ref=%s token_last4=%s", self.reference, token_last4)

        payload = {
            "merchid": mid,
            "account": token,
            "amount": "%.2f" % (self.amount or 0.0),
            "currency": self.currency_id.name,
            "capture": "y",
            "orderid": self.reference,
        }

        response = provider.with_context(
            cardpointe_tx_reference=self.reference
        )._cardpointe_request('POST', ENDPOINT_CHARGE, payload=payload)

        raw = response.get('data') or {}
        is_success = bool(response.get('ok')) and isinstance(raw, dict) and raw.get('respstat') == 'A'

        if is_success:
            self._cardpointe_set_provider_reference(raw)
            self._cardpointe_done(_("CardPointe payment approved."))
            return {'ok': True, 'message': _("Payment approved."), 'raw': raw}

        message = (
            raw.get('resptext')
            if isinstance(raw, dict) and raw.get('resptext')
            else response.get('error_message')
            or _("CardPointe payment failed.")
        )
        code = raw.get('respcode') if isinstance(raw, dict) else response.get('error_code')
        self._cardpointe_fail(message, code)
        return {'ok': False, 'message': message, 'raw': raw}

    def _cardpointe_charge_from_payment_token(self, payment_token):
        """Charge using an existing Odoo payment.token backed by CardPointe profile/account ids."""
        self.ensure_one()
        if self.provider_code != 'cardpointe':
            return {'ok': False, 'message': _("CardPointe: invalid provider.")}
        if not payment_token:
            return {'ok': False, 'message': _("CardPointe: missing payment token.")}

        provider = self.provider_id
        mid = provider.cardpointe_mid
        if not mid:
            raise UserError(_("CardPointe: missing MID on the payment provider."))

        provider_ref = payment_token.provider_ref or ''
        if ':' not in provider_ref:
            raise UserError(_("CardPointe: invalid saved token reference. Expected profileid:accountid."))

        profile_id, account_id = provider_ref.split(':', 1)
        if not profile_id or not account_id:
            raise UserError(_("CardPointe: incomplete saved token reference."))

        _logger.info(
            "[CARDPOINTE] saved-token charge request tx_ref=%s payment_token_id=%s profile_id=%s account_id=%s",
            self.reference,
            payment_token.id,
            profile_id,
            account_id,
        )

        payload = {
            "merchid": mid,
            "profile": "%s/%s" % (profile_id, account_id),
            "amount": "%.2f" % (self.amount or 0.0),
            "currency": self.currency_id.name,
            "capture": "y",
            "orderid": self.reference,
            "cof": "C",
            "cofscheduled": "N",
        }

        response = provider.with_context(
            cardpointe_tx_reference=self.reference
        )._cardpointe_request('POST', ENDPOINT_CHARGE, payload=payload)

        raw = response.get('data') or {}
        is_success = bool(response.get('ok')) and isinstance(raw, dict) and raw.get('respstat') == 'A'

        if is_success:
            self._cardpointe_set_provider_reference(raw)
            self._cardpointe_done(_("CardPointe payment approved."))
            return {'ok': True, 'message': _("Payment approved."), 'raw': raw}

        message = (
            raw.get('resptext')
            if isinstance(raw, dict) and raw.get('resptext')
            else response.get('error_message')
                 or _("CardPointe saved-token payment failed.")
        )
        code = raw.get('respcode') if isinstance(raw, dict) else response.get('error_code')
        self._cardpointe_fail(message, code)
        return {'ok': False, 'message': message, 'raw': raw}

    def _send_payment_request(self):
        """Hook used by Odoo when paying with an existing saved payment token."""
        super_result = super()
        for tx in self:
            if tx.provider_code != 'cardpointe':
                continue
            if not tx.token_id:
                continue
            tx._cardpointe_charge_from_payment_token(tx.token_id)
        return super_result

    def _cardpointe_tokenize_from_token(self, token, meta=None):
        self.ensure_one()
        if self.provider_code != 'cardpointe':
            return {'ok': False, 'message': _("CardPointe: invalid provider.")}

        provider = self.provider_id
        partner = self.partner_id.commercial_partner_id

        profile_result = provider._cardpointe_create_profile_from_token(token, partner, meta=meta, consent=True)
        if not profile_result.get('ok'):
            message = profile_result.get('error_message') or _("CardPointe tokenization failed.")
            self._cardpointe_fail(message, profile_result.get('error_code'))
            return {'ok': False, 'message': message}

        normalized = profile_result['data']
        payment_token = provider._cardpointe_create_or_update_payment_token(partner, normalized)

        provider_ref = "%s:%s" % (
            normalized.get('profile_id') or '',
            normalized.get('account_id') or '',
        )
        self._cardpointe_set_provider_reference({'reference': provider_ref})
        self._cardpointe_done(_("CardPointe payment method saved."))
        return {
            'ok': True,
            'message': _("Payment method saved."),
            'payment_token_id': payment_token.id,
            'provider_ref': provider_ref,
        }

    def _cardpointe_charge_and_tokenize_from_token(self, token, meta=None):
        self.ensure_one()
        if self.provider_code != 'cardpointe':
            return {'ok': False, 'message': _("CardPointe: invalid provider.")}

        provider = self.provider_id
        partner = self.partner_id.commercial_partner_id

        profile_result = provider._cardpointe_create_profile_from_token(token, partner, meta=meta, consent=True)
        if not profile_result.get('ok'):
            message = profile_result.get('error_message') or _("CardPointe tokenization failed.")
            self._cardpointe_fail(message, profile_result.get('error_code'))
            return {'ok': False, 'message': message}

        provider._cardpointe_create_or_update_payment_token(partner, profile_result['data'])
        return self._cardpointe_charge_from_token(token, meta=meta)