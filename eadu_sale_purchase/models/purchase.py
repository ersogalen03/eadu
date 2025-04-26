# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    eadu_ident = fields.Integer('Eadu Identification', copy=False)



    # Utility methods



    def _convert_order_line(self):
        lines = []
        for line in self.order_line:
            lines.append({
                'name': line.product_id.name,
                'product_uom_qty': line.product_uom_qty,
                'price_unit': line.price_unit,
            })
        return lines

    @api.model
    def _search_create_partner_user(self, partner, user):
        partner_user = self.env['eadu.partner.user'].sudo().search([('partner_id', '=', partner.id), ('user_id', '=', user.id)], limit=1)
        if not partner_user:
            result = partner._eadu_call('eadu/1/usercreate', {'name': user.name, 'eadu_ident': user.id})
            partner_user = self.env['eadu.partner.user'].sudo().create({
                'partner_id': partner.id,
                'user_id': user.id,
                'eadu_ident': result['user_id'],
            })
        return partner_user


    # Button methods

    def button_eadu_sync(self):
        self.ensure_one()
        if not self.partner_id.eadu_ident:
            raise
        # Search user_partner link
        partner_user = self._search_create_partner_user(self.partner_id, self.env.user)

        params = {
            'lines': self._convert_order_line(), 
            'ref': self.name, 
            'eadu_ident': self.id, 
            'user_id': partner_user.eadu_ident
            }
        result = self.partner_id._eadu_call('eadu/1/saleordercreate', params)
        self.eadu_ident = result['sale_order_id']
