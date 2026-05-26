{
    'name': 'POS CardPointe POC',
    'version': '18.0.1.0.0',
    'summary': 'POC CardPointe terminal integration for POS',
    'category': 'Sales/Point of Sale',
    'depends': ['point_of_sale', 'payment_cardpointe_base'],
    'data': [
        'security/ir.model.access.csv',
        'views/cardpointe_terminal_config_views.xml',
        'views/pos_payment_method_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_cardpointe_poc/static/src/js/pos_cardpointe_poc.js',
            'pos_cardpointe_poc/static/src/js/manual_entry_popup.js',
            'pos_cardpointe_poc/static/src/xml/manual_entry_popup.xml',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'license': 'LGPL-3',
}
