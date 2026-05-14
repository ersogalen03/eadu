# Part of Eadu. See LICENSE file for full copyright and licensing details.

{
    'name': "Eadu",
    'summary': "This is the base module to allow setting up the connection between two databases",
    'description': """
On the partner fill in his URL and your id in his db. 
    """,
    'category': 'Eadu',
    'version': '0.1',
    'depends': [
        'mail',
    ],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'wizard/eadu_exchange_views.xml',
        'views/res_partner_views.xml',
        'security/ir_rule.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Clean stale Discuss RTC localStorage values before mail models compute.
            'eadu/static/src/rtc/eadu_rtc_storage_sanitize.js',
            # Cross-DB RTC can expose mirrored sessions before P2P transceivers exist.
            'eadu/static/src/rtc/eadu_peer_to_peer_transceiver_patch.js',
            # Core registry + suggestion service/hook/composer patches
            'eadu/static/src/mention/eadu_mention_registry.js',
            'eadu/static/src/mention/eadu_mention_core.js',
            # Built-in mention types: /partner: and /attachment:
            'eadu/static/src/mention/eadu_mention_builtin.js',
            # Shared XML templates for eadu mention types
            'eadu/static/src/product_mention/product_mention.xml',
            # processMessage patch for plain-text composer link post-processing
            'eadu/static/src/product_mention/product_mention.js',
        ],
    },
    'demo': [
    ],
    'license': 'LGPL-3',
}
