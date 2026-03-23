from . import models
from . import services


def post_init_hook(env):
    providers = env['payment.provider'].search([
        ('code', '=', 'cardpointe'),
        ('cardpointe_merchant_config_id', '=', False),
    ])
    merchant_config_obj = env['cardpointe.merchant.config']
    for provider in providers:
        existing = merchant_config_obj.search([
            ('company_id', '=', provider.company_id.id),
            ('mid', '=', provider.cardpointe_mid),
            ('gateway_base_url', '=', provider.cardpointe_api_base),
            ('gateway_username', '=', provider.cardpointe_username),
        ], limit=1)
        if not existing:
            existing = merchant_config_obj.create({
                'name': f"{provider.company_id.name} / {provider.cardpointe_mid or 'CardPointe'}",
                'company_id': provider.company_id.id,
                'mid': provider.cardpointe_mid,
                'gateway_base_url': provider.cardpointe_api_base,
                'gateway_username': provider.cardpointe_username,
                'gateway_password': provider.cardpointe_password,
                'tokenizer_url': provider.cardpointe_tokenizer_url,
                'debug_logging': provider.cardpointe_debug_logging,
                'timeout_connect': provider.cardpointe_timeout_connect,
                'timeout_read': provider.cardpointe_timeout_read,
            })
        provider.cardpointe_merchant_config_id = existing.id