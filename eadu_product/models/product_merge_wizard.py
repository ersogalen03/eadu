# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import _, fields, models
from odoo.exceptions import UserError


class EaduProductMergeWizard(models.TransientModel):
    _name = 'eadu.product.merge.wizard'
    _description = "Merge Eadu Catalog Product"

    source_product_id = fields.Many2one(
        'product.product',
        string="Eadu Product",
        required=True,
        domain="[('eadu_catalog', '=', True)]",
    )
    target_product_id = fields.Many2one(
        'product.product',
        string="Merge Into",
        required=True,
        domain="[('id', '!=', source_product_id)]",
    )
    move_supplierinfo = fields.Boolean(string="Move Vendor Prices", default=True)
    copy_barcode = fields.Boolean(string="Copy Barcode When Empty", default=True)

    def action_merge(self):
        self.ensure_one()
        source = self.source_product_id.sudo().with_context(active_test=False)
        target = self.target_product_id.sudo().with_context(active_test=False)
        if source == target:
            raise UserError(_("Choose a different product to merge into."))
        if not source.eadu_catalog:
            raise UserError(_("Only Eadu catalog products can be merged by this wizard."))

        if self.copy_barcode and not target.barcode and source.eadu_remote_barcode:
            if source.barcode == source.eadu_remote_barcode:
                source.barcode = False
            target.barcode = source.eadu_remote_barcode

        self.env['eadu.partner.any'].sudo().search([
            ('res_model', '=', 'product.product'),
            ('res_id', '=', source.id),
        ]).write({'res_id': target.id})

        if self.move_supplierinfo:
            self.env['product.supplierinfo'].sudo().search([
                ('product_tmpl_id', '=', source.product_tmpl_id.id),
                ('product_id', '=', source.id),
            ]).write({
                'product_tmpl_id': target.product_tmpl_id.id,
                'product_id': target.id,
            })

        target.write({
            'eadu_catalog': True,
            'eadu_merge_state': 'accepted',
            'eadu_review_reason': False,
            'eadu_origin_partner_id': source.eadu_origin_partner_id.id,
            'eadu_origin_ident': source.eadu_origin_ident,
            'eadu_remote_barcode': source.eadu_remote_barcode,
            'eadu_remote_sale_price': source.eadu_remote_sale_price,
        })
        source.write({
            'active': False,
            'eadu_merge_state': 'ignored',
            'eadu_review_reason': _("Merged into %s.", target.display_name),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.product',
            'res_id': target.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {'active_test': False},
        }
