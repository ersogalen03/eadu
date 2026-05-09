# Part of Eadu. See LICENSE file for full copyright and licensing details.

{
    'name': "Eadu Product",
    'summary': "Bridge between Eadu and the standard Odoo product module",
    'description': """
Products imported from another Eadu database land in the eadu.product queue first.
The system auto-suggests a matching local product.product based on barcode or name.
From the list view the user can either create a new product or link to an existing one.
Linking creates a product.supplierinfo and eadu.partner.any records on both sides.
    """,
    'category': 'Eadu',
    'version': '0.1',
    'depends': [
        'eadu',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/ir_rule.xml',
        'views/eadu_product_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Registers /product: into the eadu mention registry
            'eadu_product/static/src/mention/eadu_product_mention.js',
            # XML templates for the product suggestion row
            'eadu_product/static/src/mention/eadu_product_mention.xml',
        ],
    },
    'license': 'LGPL-3',
}
