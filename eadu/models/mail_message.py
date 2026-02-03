# Part of Eadu. See LICENSE file for full copyright and licensing details.

from markupsafe import Markup
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
            for partner in partners:
                if eadu_p := partner._get_eadu_partner():
                    eadu_partners |= eadu_p

            if len(eadu_partners) == 1:
                epu = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(eadu_partners, 'res.partner', self.env.user.partner_id.id)
                # There should be an epu?
                if not epu:
                    result = eadu_partners.sudo()._eadu_call(
                        'res.partner',
                        'action_eadu_create_contact',
                        {
                            'name': self.env.user.name,
                            'email': self.env.user.email,
                            'eadu_ident': self.env.user.partner_id.id,
                        }
                    )
                    if result:
                        epu = self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_partners, 'res.partner', self.env.user.partner_id.id, result['result'])
                result_partners = []
                for partner in partners:
                    if epa := self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(eadu_partners, 'res.partner', partner.id):
                        result_partners.append(epa.eadu_ident)
                channel_eadu_ident = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(eadu_partners, 'discuss.channel', channel.id).eadu_ident
                if not channel_eadu_ident:
                    result = eadu_partners.sudo()._eadu_call(
                        'discuss.channel',
                        'action_eadu_channel_create', 
                        {
                            'name': channel.name,
                            'eadu_ident': channel.id,
                            'partner_ids': result_partners,
                        },                     
                    )
                    if result:
                        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_partners, 'discuss.channel', channel.id, result['channel_id'])
                        channel_eadu_ident = result['channel_id']
                result = {
                    'model': 'discuss.channel',
                    'res_id': channel_eadu_ident,
                    'body': body,
                    'partner_id': epu.eadu_ident,
                }
                return eadu_partners, result

        if match:= re.search('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', body):
            partner = self.env['res.partner'].browse(int(match.group(1)))
            if eadu_contact := partner._get_eadu_partner():
                model, res_id = self._convert_model(model, res_id)
                body = re.sub('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', str(partner.eadu_ident), body)
                eup = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(eadu_contact, 'res.partner', self.env.user.partner_id.id)
                result = {
                    'model': model,
                    'res_id': res_id, # TODO: better logic please if model is e.g. res_partner
                    'body': body,
                    'partner_id': eup.eadu_ident,
                }
            return eadu_contact, result
        return False, False

    @api.model_create_multi
    def create(self, vals):
        if self.env.context.get('eadu_message'):
            return super(MailMessage, self).create(vals)
        recs = self.env['mail.message']
        for val in vals:
            created_record = super(MailMessage, self).create(val)
            body = val['body']
            model = val.get('model')
            res_id = val.get('res_id')
            result = False
            if body and model and res_id:
                partner, result = self._handle_eadu_msg(model, res_id, body)
                if result and partner:
                    result['eadu_ident'] = created_record.id
                    # Pre-create remote attachments unattached and collect their remote IDs
                    remote_attachment_ids = []
                    for att in created_record.attachment_ids.sudo():
                        payload = {
                            'name': att.name,
                            'datas': att.datas.decode() if isinstance(att.datas, bytes) else att.datas,
                            'mimetype': att.mimetype,
                            'res_model': None,
                            'res_id': None,
                            'eadu_ident': att.id,
                        }
                        try:
                            a_res = partner.sudo()._eadu_call('ir.attachment', 'action_eadu_receive', payload)
                            if a_res and 'attachment_id' in a_res:
                                remote_attachment_ids.append(a_res['attachment_id'])
                                self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                                    partner, 'ir.attachment', att.id, a_res['attachment_id']
                                )
                        except Exception:
                            # continue even if one attachment fails
                            pass

                    # Create the remote message, passing collected remote attachment ids
                    result_with_atts = dict(result)
                    result_with_atts['attachment_ids'] = remote_attachment_ids
                    res = partner._eadu_call(
                        'mail.message',
                        'action_eadu_receive', 
                        result_with_atts, 
                    )
                    if res and 'message_id' in res:
                        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(partner, 'mail.message', created_record.id, res['message_id'])

            recs += created_record
        return recs
                    

    def action_eadu_receive(self, model, res_id, body, partner_id, eadu_ident, attachment_ids=None):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise


        vals = {
            'body': Markup(body),
            'author_id': partner_id,
            'res_id': res_id,
            'model': model,
        }
        #TODO: check that attachment_ids are legal...
        if attachment_ids:
            self.env['ir.attachment'].browse(attachment_ids).sudo().write({'res_model': model, 'res_id': res_id})
            vals['attachment_ids'] = [(4, aid) for aid in attachment_ids]
        message = self.env['mail.message'].with_context(eadu_message=True).sudo().create(vals)
        # We want user to be notified of the new message
        try:
            # Trigger the same notification pipeline as message_post
            self.env[model].sudo().browse(res_id)._notify_thread(message)
        except Exception:
            # Do not block sync on notification errors
            pass

         # Create the eadu.partner.any linkage

        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_contact, 'mail.message', message.id, eadu_ident)

        return {'message_id': message.id}

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message'):
            for message in self:
                print('sync with others', message.id)
                # If this is a discuss.channel message and attachments were updated, ensure remote linkage
                if message.model == 'discuss.channel' and ('attachment_ids' in vals or message.attachment_ids):
                    # For each remote partner mapping of this message, push new attachments and link to remote message
                    eadu_anys = self.env['eadu.partner.any'].sudo().search([
                        ('res_model', '=', 'mail.message'), ('res_id', '=', message.id)
                    ])
                    for eadu_any in eadu_anys:
                        for att in message.attachment_ids.sudo():
                            # Skip if this attachment already mapped for this partner
                            remote_att = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(eadu_any.partner_id, 'ir.attachment', att.id)
                            if remote_att:
                                continue
                            payload = {
                                'name': att.name,
                                'datas': att.datas.decode() if isinstance(att.datas, bytes) else att.datas,
                                'mimetype': att.mimetype,
                                'res_model': 'mail.message',
                                'res_id': eadu_any.eadu_ident,
                                'eadu_ident': att.id,
                            }
                            try:
                                a_res = eadu_any.partner_id.sudo()._eadu_call('ir.attachment', 'action_eadu_receive', payload)
                                if a_res and 'attachment_id' in a_res:
                                    self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                                        eadu_any.partner_id, 'ir.attachment', att.id, a_res['attachment_id']
                                    )
                            except Exception:
                                pass

                self.env['eadu.partner.any'].sudo()._sync_with_others('mail.message', message.id, vals)
        return res