# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import models, _
from odoo.addons.eadu.exceptions import EaduConnectionError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message') and {'name', 'image_1920', 'list_price'} & set(vals):
            changed_fields = set(vals) & {'name', 'image_1920', 'list_price'}
            EaduAny = self.env['eadu.partner.any'].sudo()
            for product in self.mapped('product_variant_ids'):
                eadu_anys = EaduAny.search([
                    ('res_model', '=', 'product.product'),
                    ('res_id', '=', product.id),
                ])
                for eadu_any in eadu_anys:
                    product._eadu_send_update(eadu_any, changed_fields)
        return res

    def action_eadu_push(self):
        """Push selected variants to connected Eadu partners as native products."""
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
                        contact._eadu_call('product.product', 'action_eadu_import_product', {
                            'eadu_ident': product.id,
                            'name': product.name,
                            'barcode': product.barcode or None,
                            'default_code': product.default_code or None,
                            'sale_price': product.lst_price,
                            'currency_id': product.currency_id.id if product.currency_id else None,
                            'fields': product._eadu_prepare_update_fields(),
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
