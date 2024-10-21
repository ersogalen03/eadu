# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    eadu_ident = fields.Integer('Eadu Identification')

    def _eadu_call(self, partner):
        # 1: Compose user
        user = self.env.user
        user_p = self.env['eadu.user.partner'].search([('user_id', '=', user.id), ('partner_id', '=', partner.id)], limit=1)
        if not user_p:
            vals = {'user': user.id, 'partner': partner.id}
            to_send = {
                'name': user.name,
                'login': user.login + self.company_id.name,
            }

        # 2: Compose sale order
        
        lines = [{
            'name': l.product_id.name,
            'product_uom_qty': l.product_uom_qty,
            'product_uom': l.product_uom.name,
            'price_unit': l.price_unit,
        } for l in self.order_line]

        vals = {
            'lines': lines,
        }
        if partner:
            vals['eadu_ident'] = partner.id

    def create(self, vals):
        res = super().create(vals)
        # We could check this in the write as well...
        if vals.get('partner_id'):
            if partner := self.env['res.partner'].browse(vals['partner_id']) and partner.eadu_ident:
                sale = self.browse(res)
                sale._eadu_call(partner)
        return res

