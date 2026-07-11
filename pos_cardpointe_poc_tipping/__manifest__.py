{
    'name': 'POS CardPointe POC Tipping',
    'version': '18.0.2.0.0',
    'summary': 'CardPointe terminal tipping using the native Odoo POS tip product',
    'category': 'Sales/Point of Sale',
    'depends': ['pos_cardpointe_poc', 'pos_restaurant'],
    'data': [
        'views/cardpointe_terminal_config_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_cardpointe_poc_tipping/static/src/js/pos_cardpointe_poc_tipping.js',
        ],
    },
    'installable': True,
    'license': 'LGPL-3',
}
