# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)

ENDPOINT_CHARGE = "/auth"


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _cardpointe_set_default_stored_credential_semantics(self, initiator=None, schedule=None):
        """Fill generic stored-credential semantics when fields exist and values are missing."""
        self.ensure_one()
        write_vals = {}
        if 'stored_credential_initiator' in self._fields and not self.stored_credential_initiator:
            write_vals['stored_credential_initiator'] = initiator or (
                'merchant' if self.operation in ('offline',) else 'customer'
            )
        if 'stored_credential_schedule' in self._fields and not self.stored_credential_schedule:
            write_vals['stored_credential_schedule'] = schedule or (
                'scheduled' if self.operation in ('offline',) else 'unscheduled'
            )
        if write_vals:
            self.write(write_vals)

    def _cardpointe_get_stored_credential_payload_fields(self):
        """Map generic stored-credential semantics to CardPointe auth fields."""
        self.ensure_one()
        semantics = (
            self._get_stored_credential_semantics()
            if hasattr(self, '_get_stored_credential_semantics')
            else {}
        )
        initiator = semantics.get('initiator')
        schedule = semantics.get('schedule')

        if not initiator:
            initiator = 'merchant' if self.operation in ('offline',) else 'customer'
        if not schedule:
            schedule = 'scheduled' if self.operation in ('offline',) else 'unscheduled'

        _logger.info(
            "[CARDPOINTE] stored-credential semantics tx_ref=%s operation=%s initiator=%s schedule=%s",
            self.reference,
            self.operation,
            initiator,
            schedule,
        )

        return {
            "cof": "M" if initiator == 'merchant' else "C",
            "cofscheduled": "Y" if schedule == 'scheduled' else "N",
        }

    def _cardpointe_get_ecomind(self, flow=None):
        """Resolve the CardPointe ecomind indicator for CNP auth requests."""
        self.ensure_one()
        flow_value = (
            flow
            or self.env.context.get('cardpointe_cnp_flow')
            or self.env.context.get('cardpointe_flow')
            or ''
        )
        normalized_flow = str(flow_value).strip().lower()
        if normalized_flow in ('e', 't', 'r'):
            return normalized_flow.upper()

        flow_map = {
            # Ecommerce / web / mobile / customer initiated
            'ecommerce': 'E',
            'web': 'E',
            'mobile': 'E',
            'direct': 'E',
            'cit': 'E',
            'customer_initiated': 'E',
            # Phone or mail order
            'telephone': 'T',
            'phone': 'T',
            'mail': 'T',
            'moto': 'T',
            # Merchant-initiated recurring
            'recurring': 'R',
            'mit': 'R',
            'merchant_initiated': 'R',
        }
        if normalized_flow in flow_map:
            return flow_map[normalized_flow]

        if self.token_id and self.operation in ('offline',):
            return 'R'
        return 'E'

    def _cardpointe_get_cnp_contact_payload(self, meta=None):
        """Build best-effort CNP billing/contact fields for CardPointe auth payloads."""
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        metadata = meta if isinstance(meta, dict) else {}
        payload = {
            #"name": metadata.get('name') or partner.name,
            #"email": metadata.get('email') or partner.email,
            #"phone": metadata.get('phone') or partner.phone,
            "address": metadata.get('address') or partner.street,
            #"city": metadata.get('city') or partner.city,
            #"region": metadata.get('region') or (partner.state_id.code if partner.state_id else None),
            #"country": metadata.get('country') or (partner.country_id.code if partner.country_id else None),
            "postal": metadata.get('postal') or partner.zip,
        }
        return {key: value for key, value in payload.items() if value}

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

    def _cardpointe_charge_from_token(
        self,
        token,
        meta=None,
        flow=None,
        save_payment_method=False,
        capture=None,
        amount=None,
        success_message=None,
    ):
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
        resolved_amount = self.amount if amount is None else amount
        resolved_capture = capture if capture is not None else 'y'

        if save_payment_method:
            self._cardpointe_set_default_stored_credential_semantics(
                initiator='customer',
                schedule='unscheduled',
            )

        _logger.info(
            "[CARDPOINTE] charge request tx_ref=%s token_last4=%s save_payment_method=%s amount=%s capture=%s",
            self.reference,
            token_last4,
            save_payment_method,
            resolved_amount,
            resolved_capture,
        )

        payload = {
            "merchid": mid,
            "account": token,
            "amount": "%.2f" % (resolved_amount or 0.0),
            "currency": self.currency_id.name,
            "capture": resolved_capture,
            "orderid": self.reference,
            "ecomind": self._cardpointe_get_ecomind(flow=flow),
            **self._cardpointe_get_cnp_contact_payload(meta=meta),
        }

        if save_payment_method:
            # Required by UAT when using CardPointe profile service on first-use auths.
            payload["profile"] = "Y"
            payload.update(self._cardpointe_get_stored_credential_payload_fields())

        response = provider.with_context(
            cardpointe_tx_reference=self.reference
        )._cardpointe_request('POST', ENDPOINT_CHARGE, payload=payload)

        raw = response.get('data') or {}
        is_success = bool(response.get('ok')) and isinstance(raw, dict) and raw.get('respstat') == 'A'

        if is_success:
            result = {
                'ok': True,
                'message': success_message or _("Payment approved."),
                'raw': raw,
            }

            if save_payment_method:
                partner = self.partner_id.commercial_partner_id
                normalized = provider._cardpointe_normalize_profile_data(raw, token=token)
                profile_id = normalized.get('profile_id')
                account_id = normalized.get('account_id')

                if not profile_id or not account_id:
                    message = _(
                        "CardPointe auth succeeded but did not return profile/account ids for token storage."
                    )
                    self._cardpointe_fail(message)
                    return {'ok': False, 'message': message, 'raw': raw}

                payment_token = provider._cardpointe_create_or_update_payment_token(partner, normalized)
                result.update({
                    'payment_token_id': payment_token.id,
                    'provider_ref': "%s:%s" % (profile_id, account_id),
                })

            if save_payment_method and result.get('provider_ref'):
                self._cardpointe_set_provider_reference({'reference': result['provider_ref']})
            else:
                self._cardpointe_set_provider_reference(raw)
            _logger.info("[CARDPOINTE] tx_ref=%s provider_reference=%s", self.reference, self.provider_reference)

            self._cardpointe_done(success_message or _("CardPointe payment approved."))
            return result

        message = (
            raw.get('resptext')
            if isinstance(raw, dict) and raw.get('resptext')
            else response.get('error_message')
            or _("CardPointe payment failed.")
        )
        code = raw.get('respcode') if isinstance(raw, dict) else response.get('error_code')
        self._cardpointe_fail(message, code)
        return {'ok': False, 'message': message, 'raw': raw}

    def _cardpointe_charge_from_payment_token(self, payment_token, flow=None, meta=None):
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

        contact_payload = self._cardpointe_get_cnp_contact_payload(meta=meta)

        _logger.info(
            "[CARDPOINTE] saved-token charge request tx_ref=%s payment_token_id=%s profile_id=%s "
            "account_id=%s contact_keys=%s",
            self.reference,
            payment_token.id,
            profile_id,
            account_id,
            sorted(contact_payload.keys()),
        )

        payload = {
            "merchid": mid,
            "profile": "%s/%s" % (profile_id, account_id),
            "amount": "%.2f" % (self.amount or 0.0),
            "currency": self.currency_id.name,
            "capture": "y",
            "orderid": self.reference,
            "ecomind": self._cardpointe_get_ecomind(flow=flow),
            **self._cardpointe_get_stored_credential_payload_fields(),
            **contact_payload,
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
            tx._cardpointe_set_default_stored_credential_semantics(
                initiator='customer', schedule='unscheduled'
            )
            tx._cardpointe_charge_from_payment_token(tx.token_id)
        return super_result

    def _cardpointe_tokenize_from_token(self, token, meta=None):
        self.ensure_one()
        if self.provider_code != 'cardpointe':
            return {'ok': False, 'message': _("CardPointe: invalid provider.")}

        # UAT requirement for token storage with no transaction:
        # amount=0.00, capture=N, profile=Y, cof=C, cofscheduled=N
        return self._cardpointe_charge_from_token(
            token,
            meta=meta,
            flow='customer_initiated',
            save_payment_method=True,
            amount=0.0,
            capture='n',
            success_message=_("Payment method saved."),
        )

    def _cardpointe_charge_and_tokenize_from_token(self, token, meta=None, flow=None):
        self.ensure_one()
        if self.provider_code != 'cardpointe':
            return {'ok': False, 'message': _("CardPointe: invalid provider.")}

        # UAT requirement for first customer-initiated payment that stores the token:
        # include profile=Y and customer-initiated stored-credential indicators on auth.
        return self._cardpointe_charge_from_token(
            token,
            meta=meta,
            flow=flow,
            save_payment_method=True,
            capture='y',
            success_message=_("Payment approved."),
        )