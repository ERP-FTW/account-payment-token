{
    'name': 'Account Payment Markup',
    'version': '19.0.2.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'Manual fees on inbound journal methods and online payment methods',
    'depends': ['account_payment'],
    'data': [
        'security/ir.model.access.csv', 'security/rules.xml', 'data/product.xml',
        'views/online_method.xml',
        'views/payment_method_views.xml',
        'views/payment_register_views.xml',
        'views/payment_portal_templates.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
}
