# Part of Eadu. See LICENSE file for full copyright and licensing details.

{
    'name': "EADU Demo - Eadu",
    'summary': "Demo planet database for Eadu kyber refinement through EADU",
    'description': """
WARNING: This module is for demo/story databases only. Do not install it in
production: it intentionally renames the main company and administrator and
loads fictional products, contacts, and images.

Transforms a demo database into Eadu: an embassy company, Galen Erso as
administrator, refinery users, and kyber refinement products with variants.
    """,
    'category': 'Eadu/Demo',
    'version': '0.1',
    'depends': [
        'eadu_product',
    ],
    'data': [
        'data/planet_data.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'license': 'LGPL-3',
}
