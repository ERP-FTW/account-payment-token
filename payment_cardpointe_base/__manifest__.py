{
    'name': 'CardPointe Base',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'Base utilities for CardPointe payments',
    'description': 'Shared CardPointe configuration and API helpers.',
    'depends': ['payment'],
    'data': [
        'security/ir.model.access.csv',
        'views/cardpointe_merchant_config_views.xml',
        'views/payment_provider_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'license': 'LGPL-3',
}
