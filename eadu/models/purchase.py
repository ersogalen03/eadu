# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models

from odoo.addons.iap.tools.iap_tools import iap_jsonrpc as jsonrpc

class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    eadu_ident = fields.Integer('Eadu Identification')

    def _eadu_call(self, company, partner, url_ext, params):
        params.update({
            'partner_ident': company.partner_id.eadu_ident,
        })
        result = jsonrpc(partner.eadu_url + url_ext, params=params)
        return result
    
    def _convert_order_line(self):
        lines = []
        for line in self.order_line:
            lines.append({
                'name': line.name,
                'product_uom_qty': line.product_uom_qty,
                'price_unit': line.price_unit,
            })
        return lines

    def button_eadu_sync(self):
        self.ensure_one()
        if not self.partner_id.eadu_ident:
            raise
        company = self.company_id
        params = {'lines': self._convert_order_line(), 'ref': self.name}
        self._eadu_call(company, self.partner_id.eadu_ident, 'eadu/1/saleordercreate', params)





    # def create(self, vals):
    #     res = super().create(vals)
    #     # We could check this in the write as well...
    #     if vals.get('partner_id'):
    #         if partner := self.env['res.partner'].browse(vals['partner_id']) and partner.eadu_ident:
    #             purchase = self.browse(res)
    #             company = self.env['res.company'].browse(vals['company_id'])
    #             params = {'lines': vals['order_line'], 'ref': vals['name']}
    #             purchase._eadu_call(company, partner, 'eadu/1/saleordercreate', lines)
    #     return res
    
    # def write(self, vals):
    #     res =  super().write(vals)
        

    #     return res