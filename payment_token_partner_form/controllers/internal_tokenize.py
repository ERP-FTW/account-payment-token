# -*- coding: utf-8 -*-
import logging

from odoo import http
from odoo.http import request
from odoo.addons.payment import utils as payment_utils
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.website.controllers.main import QueryURL

_logger = logging.getLogger(__name__)


class InternalTokenizeController(CustomerPortal):
    """Internal (back-office) entry point to tokenize a card for a partner.

    We intentionally re-use Odoo's standard 'payment methods' portal template so that:
      - it works across payment providers (tokenization handled by provider integrations)
      - it stays aligned with upstream payment UX/JS
    """
    @http.route(['/my/internal/payment_method'], type='http', auth='user', website=True)
    def internal_payment_methods(self, partner_id=None, **kw):
        if not partner_id:
            _logger.warning("[payment_token_partner_form] Missing partner_id")
            return request.not_found()

        partner = request.env['res.partner'].browse(int(partner_id))
        if not partner.exists():
            _logger.warning("[payment_token_partner_form] Partner not found: %s", partner_id)
            return request.not_found()

        partner_sudo = partner.sudo()
        _logger.info("[payment_token_partner_form] Open internal tokenization page for partner=%s (%s)",
                     partner_sudo.id, partner_sudo.display_name)

        # ----- Providers / Methods (compatible, tokenizable) -----
        providers_sudo = self._get_compatible_providers_sudo(partner_sudo)
        payment_methods_sudo = self._get_compatible_payment_methods_sudo(providers_sudo, partner_sudo)

        tokens_sudo = request.env['payment.token'].sudo().search([
            ('partner_id', '=', partner_sudo.id),
            ('provider_id', 'in', providers_sudo.ids),
        ])

        # Access token is used by standard payment flows (JS endpoints & portal security).
        access_token = self._generate_partner_access_token(partner_sudo.id)

        _logger.info(
            "[payment_token_partner_form] context: providers=%s, payment_methods=%s, tokens=%s",
            len(providers_sudo), len(payment_methods_sudo), len(tokens_sudo),
        )

        qcontext = self._prepare_portal_layout_values()
        qcontext.update({
            'partner': partner_sudo,
            'billing_address': partner_sudo,
            'access_token': access_token,

            # Odoo templates sometimes use either <x> or <x>_sudo depending on version.
            'providers': providers_sudo,
            'providers_sudo': providers_sudo,
            'payment_methods': payment_methods_sudo,
            'payment_methods_sudo': payment_methods_sudo,
            'tokens': tokens_sudo,
            'tokens_sudo': tokens_sudo,

            'page_name': 'payment_methods',
            'default_url': '/my/internal/payment_method',
            'keep': QueryURL('/my/internal/payment_method'),
            'tokenize_partner_id': partner_sudo.id,
            'tokenize_internal_mode': True,
        })

        # Standard template from addon 'payment'
        return request.render('payment.payment_methods', qcontext)

    # -------------------------
    # Helpers
    # -------------------------
    def _generate_partner_access_token(self, partner_id):
        """Generate the access token (signature differs across minor versions)."""
        try:
            return payment_utils.generate_access_token(partner_id, None, None)
        except TypeError:
            try:
                return payment_utils.generate_access_token(partner_id)
            except Exception:
                _logger.exception("[payment_token_partner_form] Failed generating access token for partner %s", partner_id)
                # last resort: empty token (will likely fail downstream but avoids crashing)
                return ''

    def _get_compatible_providers_sudo(self, partner_sudo):
        """Return compatible providers for tokenization (cross-version fallback)."""
        # Primary: payment.provider (Odoo 16+)
        Provider = request.env.get('payment.provider')
        if Provider and hasattr(Provider, '_get_compatible_providers'):
            try:
                return Provider.sudo()._get_compatible_providers(
                    partner_id=partner_sudo.id,
                    force_tokenization=True,
                    is_validation=True,
                )
            except TypeError:
                # older signature
                return Provider.sudo()._get_compatible_providers(partner_sudo.id, force_tokenization=True)
            except Exception:
                _logger.exception("[payment_token_partner_form] _get_compatible_providers failed")
                return Provider.sudo().search([('state', 'in', ('enabled', 'test'))])

        # Fallback: payment.acquirer (very old)
        Acquirer = request.env.get('payment.acquirer')
        if Acquirer and hasattr(Acquirer, '_get_compatible_acquirers'):
            try:
                return Acquirer.sudo()._get_compatible_acquirers(
                    partner_id=partner_sudo.id,
                    force_tokenization=True,
                    is_validation=True,
                )
            except Exception:
                _logger.exception("[payment_token_partner_form] _get_compatible_acquirers failed")
                return Acquirer.sudo().search([('state', 'in', ('enabled', 'test'))])

        _logger.error("[payment_token_partner_form] No provider/acquirer model found; check installed payment version")
        return request.env['payment.provider'].sudo().browse([])

    def _get_compatible_payment_methods_sudo(self, providers_sudo, partner_sudo):
        """Return compatible payment methods, with graceful fallback."""
        PayMethod = request.env.get('payment.method')
        if PayMethod and hasattr(PayMethod, '_get_compatible_payment_methods'):
            try:
                return PayMethod.sudo()._get_compatible_payment_methods(
                    providers_sudo.ids,
                    partner_sudo.id,
                    force_tokenization=True,
                )
            except TypeError:
                # some versions require 'report' kw, others don't
                try:
                    return PayMethod.sudo()._get_compatible_payment_methods(
                        providers_sudo.ids,
                        partner_sudo.id,
                        force_tokenization=True,
                        report='account.move',
                    )
                except Exception:
                    _logger.exception("[payment_token_partner_form] _get_compatible_payment_methods failed (TypeError path)")
            except Exception:
                _logger.exception("[payment_token_partner_form] _get_compatible_payment_methods failed")

        # Fallback: compute from provider relations
        try:
            if 'payment_method_ids' in providers_sudo._fields:
                return providers_sudo.mapped('payment_method_ids').sudo()
        except Exception:
            _logger.exception("[payment_token_partner_form] provider.payment_method_ids fallback failed")
        return request.env['payment.method'].sudo().browse([])
