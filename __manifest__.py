{
    'name': 'Biotex - Solicitud de pago y CxP',
    'summary': 'Solicitud de pago generada desde la OC, cola para administración, comprobante, estado visible para compras; CxP consolidadas por razón social y proveedor',
    'version': '19.0.1.0.0',
    'category': 'Biotex',
    'author': 'Alphaqueb Consulting SAS',
    'license': 'LGPL-3',
    'depends': ['biotex_purchase_request', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'security/payment_security.xml',
        'data/payment_data.xml',
        'views/payment_request_views.xml',
        'views/purchase_order_views.xml',
        'views/account_move_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
}
