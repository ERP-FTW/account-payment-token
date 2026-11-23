{
    'name': 'Account Payment Markup',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'Provider-scoped card payment markup calculation and invoice payment support',
    'depends': ['account', 'payment'],
    'data': [
        'views/payment_provider_views.xml',
        'views/payment_method_views.xml',
        'views/payment_register_views.xml',
        'views/payment_portal_templates.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
}
