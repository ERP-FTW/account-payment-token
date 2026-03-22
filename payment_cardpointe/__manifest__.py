{
    'name': 'CardPointe Payment',
    'version': '16.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'CardPointe payment integration for e-commerce',
    'description': 'CardPointe payment integration for website checkout.',
    'depends': ['payment', 'website_sale', 'payment_cardpointe_base'],
    'data': [
        'views/payment_templates.xml',
        'views/account_journal.xml',
        'views/payment_provider_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_cardpointe/static/src/js/payment_form.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}
