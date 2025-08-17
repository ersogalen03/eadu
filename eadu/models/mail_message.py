# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models

import re

class MailMessage(models.Model):
    _inherit = "mail.message"

    def _convert_model(self, model, res_id):
        return model, res_id

    def _handle_eadu_msg(self, model, res_id, body):
        result = {}
        if match:= re.search('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', body):
            partner = self.env['res.partner'].browse(int(match.group(1)))
            user = partner.user_ids.filtered(lambda u: u.eadu_ident)
            if partner.commercial_partner_id and partner.commercial_partner_id.eadu_ident and user:
                model, res_id = self._convert_model(model, res_id)
                body = re.sub('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', str(user.eadu_ident), body)
                partner_user = self.env['purchase.order']._search_create_partner_user(partner.commercial_partner_id, self.env.user)

                result = {
                    'model': model, 
                    'res_id': res_id,
                    'body': body,
                    'user_id': partner_user.eadu_ident,
                }
        return partner, result

    @api.model_create_multi
    def create(self, vals):
        if self.env.context.get('eadu_message'):
            return super(MailMessage, self).create(vals)
        for val in vals:
            body = val['body']
            model = val.get('model')
            res_id = val.get('res_id')
            result = False
            if body and model and res_id:
                partner, result = self._handle_eadu_msg(model, res_id, body)
                if result and partner:
                    partner_parent = partner.commercial_partner_id
                    res = partner_parent._eadu_call(
                        'eadu/1/messagereceive', 
                        result
                    )
        return super(MailMessage, self).create(val)
    




class EaduMessagePartner(models.Model):
    _name = "eadu.message.partner"
    _description = "Link between Messages and Eadu Users"

    message_id = fields.Many2one('mail.message', 'Message')
    partner_id = fields.Many2one('res.partner', 'Eadu Partner Company')
    eadu_ident = fields.Integer('Eadu Identification')
    to_synchronize = fields.Boolean('To Synchronize', default=True)