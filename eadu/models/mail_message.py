# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models

import re

class MailMessage(models.Model):
    _inherit = "mail.message"

    def _convert_model(self, model, res_id):
        return model, res_id

    def _handle_eadu_msg(self, model, res_id, body):
        result = {}
        if model == 'discuss.channel':
            channel = self.env['discuss.channel'].browse(res_id)
            partners = channel.channel_partner_ids
            result_partners = []
            eadu_partners = self.env['res.partner']
            res_partner = self.env['res.partner']
            for partner in partners:
                if partner.eadu_ident:
                    eadu_partners |= partner.parent_id # if eadu_url
                    result_partners.append(partner.eadu_ident)
                    res_partner = partner

            if len(eadu_partners) == 1:
                session = eadu_partners.sudo()._eadu_login()
                epu = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(eadu_partners, 'res.users', self.env.user.id)
                # There should be an epu?
                if not epu:
                    result = eadu_partners.sudo()._eadu_call(
                        '/eadu/1/contactcreate',
                        {
                            'name': self.env.user.name,
                            'email': self.env.user.email,
                            'eadu_ident': self.env.user.id,
                        },
                        session=session
                    )
                    if result:
                        epu = self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_partners, 'res.users', self.env.user.id, result['result'])
                    if not epu.eadu_ident:
                        import pdb; pdb.set_trace()
                result_partners.append(epu.eadu_ident)
                if not channel.eadu_ident:
                    result = eadu_partners.sudo()._eadu_call(
                        '/eadu/1/channelcreate', 
                        {
                            'name': channel.name,
                            'eadu_ident': channel.id,
                            'partner_ids': result_partners,
                        }, 
                        session=session
                    )
                    if result:
                        channel.eadu_ident = result.get('result', {}).get('channel_id')
                        print("Result", result)
                result = {
                    'model': 'discuss.channel',
                    'res_id': channel.eadu_ident,
                    'body': body,
                    'user_id': epu.eadu_ident,
                }
                return res_partner, result

        if match:= re.search('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', body):
            partner = self.env['res.partner'].browse(int(match.group(1)))
            if partner.eadu_ident and partner.parent_id.eadu_url:
                model, res_id = self._convert_model(model, res_id)
                body = re.sub('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', str(partner.eadu_ident), body)
                eup = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(partner.parent_id, 'res.users', self.env.user.id)
                result = {
                    'model': model,
                    'res_id': res_id, # TODO: better logic please if model is e.g. res_partner
                    'body': body,
                    'user_id': eup.eadu_ident,
                }
            return partner, result
        return False, False

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
                    partner_parent = partner.parent_id # Somehow commercial_partner_id is failing
                    session = partner_parent._eadu_login()
                    res = partner_parent._eadu_call(
                        '/eadu/1/messagereceive', 
                        result, 
                        session=session
                    )
        return super(MailMessage, self).create(val)
    

class EaduMessagePartner(models.Model):
    _name = "eadu.message.partner"
    _description = "Link between Messages and Eadu Users"

    message_id = fields.Many2one('mail.message', 'Message')
    partner_id = fields.Many2one('res.partner', 'Eadu Partner Company')
    eadu_ident = fields.Integer('Eadu Identification')
    to_synchronize = fields.Boolean('To Synchronize', default=True)