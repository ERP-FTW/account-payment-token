# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import re

from odoo import _, api, models
from odoo.addons.payment import setup_provider
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ENDPOINT_PROFILE_CREATE = 'profile'
ENDPOINT_PROFILE_GET = 'profile/{profileid}/{accountid}/{merchid}'
ENDPOINT_PROFILE_DELETE = 'profile/{profileid}/{accountid}/{merchid}'


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    def _register_hook(self):
        """Ensure CardPointe provider setup exists on upgrades."""
        res = super()._register_hook()
        try:
            setup_provider(self.env.cr, self.env.registry, 'cardpointe')
        except Exception:
            _logger.exception('[CARDPOINTE] Failed to setup provider during registry hook.')
        return res

    @api.model
    def _setup_provider(self, code):
        """Ensure CardPointe payment method exists."""
        res = super()._setup_provider(code)
        if code != 'cardpointe':
            return res

        payment_method = self.env['account.payment.method'].search([('code', '=', code)], limit=1)
        if not payment_method:
            self.env['account.payment.method'].sudo().create({
                'name': _('CardPointe'),
                'code': code,
                'payment_type': 'inbound',
            })

        return res

    @api.depends('code')
    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'cardpointe').update({
            'support_manual_capture': False,
            'support_refund': False,
            'support_tokenization': True,
        })

    def _cardpointe_create_profile_from_token(self, token, partner, meta=None, consent=False):
        """Create a reusable CardPointe profile from a hosted token."""
        self.ensure_one()
        if self.code != 'cardpointe':
            raise UserError(_("CardPointe: invalid payment provider."))
        if not token:
            raise UserError(_("CardPointe: missing hosted token for profile creation."))
        if not self.cardpointe_mid:
            raise UserError(_("CardPointe: missing merchant id on the provider."))

        if isinstance(partner, models.BaseModel):
            partner = partner[:1]
        else:
            partner = self.env['res.partner'].browse(partner).exists()[:1]
        if not partner:
            raise UserError(_("CardPointe: missing partner for profile creation."))

        payload = {
            'merchid': self.cardpointe_mid,
            'account': token,
            'name': partner.name,
            'email': partner.email,
            'phone': partner.phone,
            'address': partner.street,
            'city': partner.city,
            'region': partner.state_id.code if partner.state_id else None,
            'country': partner.country_id.code if partner.country_id else None,
            'postal': partner.zip,
        }
        payload = {k: v for k, v in payload.items() if v}

        _logger.info(
            "[CARDPOINTE] profile create request provider_id=%s partner_id=%s token_last4=%s consent=%s",
            self.id,
            partner.id,
            token[-4:] if isinstance(token, str) else '****',
            bool(consent),
        )

        response = self.with_context(cardpointe_partner_id=partner.id)._cardpointe_request(
            'POST', ENDPOINT_PROFILE_CREATE, payload=payload
        )

        raw = response.get('data') or {}
        is_success = bool(response.get('ok')) and isinstance(raw, dict) and raw.get('respstat') == 'A'

        if not is_success:
            _logger.info(
                "[CARDPOINTE] profile create failure provider_id=%s partner_id=%s correlation_id=%s http_status=%s respcode=%s resptext=%s",
                self.id,
                partner.id,
                response.get('correlation_id'),
                response.get('http_status'),
                raw.get('respcode') if isinstance(raw, dict) else response.get('error_code'),
                raw.get('resptext') if isinstance(raw, dict) else response.get('error_message'),
            )
            return {
                'ok': False,
                'error_code': raw.get('respcode') if isinstance(raw, dict) else response.get('error_code'),
                'error_message': raw.get('resptext') if isinstance(raw, dict) else response.get('error_message'),
                'raw': raw,
            }

        normalized = self._cardpointe_normalize_profile_data(raw, token=token)
        return {'ok': True, 'data': normalized, 'raw': raw}

    def _cardpointe_get_profile(self, profile_id, account_id='0'):
        """Fetch an existing CardPointe profile and normalize profile/card details."""
        self.ensure_one()
        if self.code != 'cardpointe':
            raise UserError(_("CardPointe: invalid payment provider."))
        if not profile_id:
            raise UserError(_("CardPointe: missing profile id."))
        if not self.cardpointe_mid:
            raise UserError(_("CardPointe: missing merchant id on the provider."))

        endpoint = ENDPOINT_PROFILE_GET.format(
            profileid=profile_id,
            accountid=account_id or '0',
            merchid=self.cardpointe_mid,
        )
        response = self._cardpointe_request('GET', endpoint)
        raw = response.get('data') or []

        if not response.get('ok'):
            return {
                'ok': False,
                'error_code': response.get('error_code'),
                'error_message': response.get('error_message'),
                'raw': raw,
            }

        normalized = self._cardpointe_normalize_profile_data(raw)
        return {'ok': True, 'data': normalized, 'raw': raw}

    def _cardpointe_delete_profile(self, profile_id, account_id='0'):
        """Delete a CardPointe profile or profile account without exposing card details."""
        self.ensure_one()
        if self.code != 'cardpointe':
            raise UserError(_("CardPointe: invalid payment provider."))
        if not profile_id:
            raise UserError(_("CardPointe: missing profile id."))
        if not self.cardpointe_mid:
            raise UserError(_("CardPointe: missing merchant id on the provider."))

        endpoint = ENDPOINT_PROFILE_DELETE.format(
            profileid=profile_id,
            accountid=account_id or '0',
            merchid=self.cardpointe_mid,
        )
        response = self._cardpointe_request('DELETE', endpoint)
        raw = response.get('data') or {}

        is_success = bool(response.get('ok')) and isinstance(raw, dict) and raw.get('respstat') == 'A'

        return {
            'ok': is_success,
            'error_code': raw.get('respcode') if isinstance(raw, dict) else response.get('error_code'),
            'error_message': None if is_success else (
                raw.get('resptext') if isinstance(raw, dict) else response.get('error_message')
            ),
            'raw': raw,
        }

    def _cardpointe_create_or_update_payment_token(self, partner, normalized):
        """Create or update an Odoo payment.token from normalized CardPointe profile data."""
        self.ensure_one()
        partner = partner.commercial_partner_id

        profile_id = normalized.get('profile_id')
        account_id = normalized.get('account_id')
        if not profile_id or not account_id:
            raise UserError(_("CardPointe: missing profile/account ids for token creation."))

        provider_ref = "%s:%s" % (profile_id, account_id)
        brand = normalized.get('brand') or _("Card")
        last4 = normalized.get('last4') or '????'
        payment_details = "%s •••• %s" % (brand, last4)

        Token = self.env['payment.token'].sudo()
        existing = Token.search([
            ('provider_id', '=', self.id),
            ('partner_id', '=', partner.id),
            ('provider_ref', '=', provider_ref),
        ], limit=1)

        token_vals = {
            'provider_id': self.id,
            'partner_id': partner.id,
            'provider_ref': provider_ref,
            'payment_details': payment_details,
        }

        if 'name' in Token._fields:
            token_vals['name'] = payment_details
        if 'company_id' in Token._fields:
            token_vals['company_id'] = self.company_id.id
        if 'verified' in Token._fields:
            token_vals['verified'] = True
        if 'active' in Token._fields:
            token_vals['active'] = True

        if 'payment_method_id' in Token._fields:
            payment_method = False
            if 'payment_method_ids' in self._fields and self.payment_method_ids:
                payment_method = self.payment_method_ids[:1]
            if not payment_method:
                payment_method = self.env['payment.method'].sudo().search([
                    ('code', 'in', ['card', 'cardpointe']),
                ], limit=1)
            if payment_method:
                token_vals['payment_method_id'] = payment_method.id

        if existing:
            existing.write(token_vals)
            return existing

        return Token.create(token_vals)

    def _cardpointe_normalize_profile_data(self, payload, token=None):
        """Normalize CardPointe profile API payloads to payment.token-friendly card fields."""
        if isinstance(payload, list):
            data = payload[0] if payload else {}
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {}

        expiry_month, expiry_year = self._cardpointe_parse_expiry(
            data.get('expiry') or data.get('exp') or data.get('expiration') or ''
        )
        last4 = (
            data.get('last4')
            or data.get('acctlastfour')
            or data.get('account_last4')
            or self._cardpointe_extract_last4(data.get('account'))
            or self._cardpointe_extract_last4(data.get('token'))
            or (token[-4:] if isinstance(token, str) else None)
        )
        return {
            'profile_id': data.get('profileid') or data.get('profile_id') or data.get('profile'),
            'account_id': data.get('acctid') or data.get('accountid') or data.get('account_id'),
            'last4': last4,
            'brand': data.get('accttype') or data.get('cardtype') or data.get('brand'),
            'expiry_month': expiry_month,
            'expiry_year': expiry_year,
            'default_account': data.get('defaultacct'),
            'respstat': data.get('respstat'),
            'respcode': data.get('respcode'),
            'resptext': data.get('resptext'),
            'cofpermission': data.get('cofpermission'),
            'token': data.get('token'),
        }

    def _cardpointe_parse_expiry(self, value):
        """Parse flexible CardPointe expiry strings into month/year integers."""
        raw = re.sub(r'\D', '', (value or '').strip())
        if len(raw) < 4:
            return None, None
        month = int(raw[:2])
        year = int(raw[2:6]) if len(raw) >= 6 else int(raw[2:4]) + 2000
        if month < 1 or month > 12:
            return None, None
        return month, year

    def _cardpointe_extract_last4(self, value):
        """Extract the last4 digits from masked card/account strings."""
        digits = re.sub(r'\D', '', value or '')
        return digits[-4:] if len(digits) >= 4 else None