# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import models, _
from odoo.addons.eadu.exceptions import EaduConnectionError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def action_eadu_push(self):
        """Push selected products to all connected Eadu partners of the current company."""
        eadu_contacts = self.env['res.partner'].sudo().search([
            ('eadu_url', '!=', False),
        ])
        if not eadu_contacts:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("No Eadu Partners"),
                    'message': _("No connected Eadu partners found for the current company."),
                    'type': 'warning',
                },
            }

        pushed = 0
        failed_contacts = set()
        for tmpl in self:
            for product in tmpl.product_variant_ids:
                for contact in eadu_contacts:
                    try:
                        contact._eadu_call('eadu.product', 'action_eadu_import_product', {
                            'eadu_ident': product.id,
                            'name': product.name,
                            'barcode': product.barcode or None,
                            'default_code': product.default_code or None,
                            'standard_price': product.standard_price,
                        })
                        pushed += 1
                    except EaduConnectionError:
                        failed_contacts.add(contact.display_name)

        if failed_contacts:
            msg = _("%(pushed)s push(es) succeeded. Could not reach: %(failed)s",
                    pushed=pushed, failed=', '.join(sorted(failed_contacts)))
            notif_type = 'warning'
        else:
            msg = _("%(pushed)s product variant(s) pushed to %(count)s Eadu partner(s).",
                    pushed=pushed,
                    count=len(eadu_contacts))
            notif_type = 'success'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Eadu Push"),
                'message': msg,
                'type': notif_type,
                'sticky': bool(failed_contacts),
            },
        }
