# Part of Eadu. See LICENSE file for full copyright and licensing details.

from markupsafe import Markup
from odoo import api, fields, models
from odoo.exceptions import AccessError

import re

class MailMessage(models.Model):
    _inherit = "mail.message"

    def _convert_model(self, model, res_id):
        return model, res_id

    def _handle_eadu_msg(self, model, res_id, body):
        if model == 'discuss.channel':
            channel = self.env['discuss.channel'].browse(res_id)
            partners = channel.channel_partner_ids

            eadu_partners = self.env['res.partner']
            for partner in partners:
                if eadu_p := partner._get_eadu_partner():
                    eadu_partners |= eadu_p

            if not eadu_partners:
                return False, False

            # If there is a relay master, only send to that partner; it will forward.
            already_linked = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', 'discuss.channel'),
                ('res_id', '=', channel.id)], limit=1)
            master = already_linked.filtered(lambda a: a.master_status == 'partner')
            if master:
                eadu_partners = master.partner_id

            results = []
            for eadu_partner in eadu_partners:
                # 1. Ensure every channel member exists remotely.
                for partner in partners:
                    if partner._get_eadu_partner() == eadu_partner:
                        continue
                    partner._eadu_ensure_remote_partner(eadu_partner)

                # 2. Ensure the message author exists remotely.
                epu = self.env.user.partner_id._eadu_ensure_remote_partner(eadu_partner)

                # 3. Ensure the channel itself exists remotely (uses mappings from steps 1-2).
                channel_eadu_ident = channel._eadu_ensure_remote_channel(eadu_partner)
                if not channel_eadu_ident:
                    continue

                results.append({
                    'partner': eadu_partner,
                    'payload': {
                        'model': 'discuss.channel',
                        'res_id': channel_eadu_ident,
                        'body': body,
                        'partner_id': epu.eadu_ident if epu else False,
                    },
                })

            return eadu_partners, results

        if match := re.search('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', body):
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
                partner_group, result = self._handle_eadu_msg(model, res_id, body)
                if result and partner_group:
                    # Normalize results to a list of {'partner': <res.partner>, 'payload': {...}}
                    results_list = result if isinstance(result, list) else [{'partner': partner_group, 'payload': result}]
                    for item in results_list:
                        eadu_partner = item['partner']
                        payload = dict(item['payload'])
                        payload['eadu_ident'] = created_record.id

                        # Collect remote attachment ids for this specific partner
                        remote_attachment_ids = []
                        for att in created_record.attachment_ids.sudo():
                            # Reuse existing mapping if available
                            existing = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                                eadu_partner, 'ir.attachment', att.id
                            )
                            if existing:
                                remote_attachment_ids.append(existing.eadu_ident)
                                continue
                            att_payload = {
                                'name': att.name,
                                'datas': att.datas.decode() if isinstance(att.datas, bytes) else att.datas,
                                'mimetype': att.mimetype,
                                'res_model': None,
                                'res_id': None,
                                'eadu_ident': att.id,
                            }
                            try:
                                a_res = eadu_partner.sudo()._eadu_call('ir.attachment', 'action_eadu_receive', att_payload)
                                if a_res and 'attachment_id' in a_res:
                                    remote_attachment_ids.append(a_res['attachment_id'])
                                    self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                                        eadu_partner, 'ir.attachment', att.id, a_res['attachment_id'],
                                        partner_master=False
                                    )
                            except Exception:
                                # continue even if one attachment fails
                                pass

                        # Create the remote message for this partner
                        payload['attachment_ids'] = remote_attachment_ids
                        try:
                            res = eadu_partner._eadu_call(
                                'mail.message',
                                'action_eadu_receive', 
                                payload, 
                            )
                            if res and 'message_id' in res:
                                self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                                    eadu_partner, 'mail.message', created_record.id, res['message_id'],
                                    partner_master=False
                                )
                        except Exception:
                            # Don't block local creation if remote fails for one partner
                            pass

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

        partner_master = True
        if model == 'discuss.channel':
            # Determine ownership at channel level. In relay scenarios, the
            # current eadu_contact may differ from the mapping partner_id.
            already_linked = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', 'discuss.channel'),
                ('res_id', '=', res_id),
            ])
            if already_linked.filtered(lambda a: a.master_status == 'me'):
                partner_master = False
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_contact, 'mail.message', message.id, eadu_ident, partner_master=partner_master)
        if not partner_master:
            # It means I am the master and need to send it to other EADU endpoints in the channel.
            channel = self.env['discuss.channel'].browse(res_id).sudo()
            partners = channel.channel_partner_ids
            for partner in partners:
                eadu_partner_upd = partner._get_eadu_partner()
                if not eadu_partner_upd or eadu_partner_upd == eadu_contact:
                    continue

                existing_msg_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                    eadu_partner_upd, 'mail.message', message.id
                )
                if existing_msg_map:
                    continue

                # Find channel and partner mapping for this remote endpoint.
                channel_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                    eadu_partner_upd, model, res_id
                )
                partner_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                    eadu_partner_upd, 'res.partner', partner.id
                )
                if not channel_map or not partner_map:
                    continue

                remote_attachment_ids = []
                for att in message.attachment_ids.sudo():
                    existing_att_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                        eadu_partner_upd, 'ir.attachment', att.id
                    )
                    if existing_att_map:
                        remote_attachment_ids.append(existing_att_map.eadu_ident)
                        continue

                    att_payload = {
                        'name': att.name,
                        'datas': att.datas.decode() if isinstance(att.datas, bytes) else att.datas,
                        'mimetype': att.mimetype,
                        'res_model': None,
                        'res_id': None,
                        'eadu_ident': att.id,
                    }
                    try:
                        a_res = eadu_partner_upd.sudo()._eadu_call('ir.attachment', 'action_eadu_receive', att_payload)
                        if a_res and 'attachment_id' in a_res:
                            remote_attachment_ids.append(a_res['attachment_id'])
                            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                                eadu_partner_upd, 'ir.attachment', att.id, a_res['attachment_id'],
                                partner_master=False
                            )
                    except Exception:
                        pass

                result = eadu_partner_upd.sudo()._eadu_call('mail.message', 'action_eadu_receive', {
                    'model': model,
                    'res_id': channel_map.eadu_ident,
                    'body': body,
                    'partner_id': partner_map.eadu_ident,
                    'eadu_ident': message.id,
                    'attachment_ids': remote_attachment_ids,
                })
                if result and result.get('message_id'):
                    self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                        eadu_partner_upd, 'mail.message', message.id, result['message_id'],
                        partner_master=False
                        
                    )



        return {'message_id': message.id}

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message'):
            for message in self:
                # print('sync with others', message.id)
                # # If this is a discuss.channel message and attachments were updated, ensure remote linkage
                # if message.model == 'discuss.channel' and ('attachment_ids' in vals or message.attachment_ids):
                #     # For each remote partner mapping of this message, push new attachments and link to remote message
                #     eadu_anys = self.env['eadu.partner.any'].sudo().search([
                #         ('res_model', '=', 'mail.message'), ('res_id', '=', message.id)
                #     ])
                #     for eadu_any in eadu_anys:
                #         for att in message.attachment_ids.sudo():
                #             # Skip if this attachment already mapped for this partner
                #             remote_att = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(eadu_any.partner_id, 'ir.attachment', att.id)
                #             if remote_att:
                #                 continue
                #             payload = {
                #                 'name': att.name,
                #                 'datas': att.datas.decode() if isinstance(att.datas, bytes) else att.datas,
                #                 'mimetype': att.mimetype,
                #                 'res_model': 'mail.message',
                #                 'res_id': eadu_any.eadu_ident,
                #                 'eadu_ident': att.id,
                #             }
                #             try:
                #                 a_res = eadu_any.partner_id.sudo()._eadu_call('ir.attachment', 'action_eadu_receive', payload)
                #                 if a_res and 'attachment_id' in a_res:
                #                     self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                #                         eadu_any.partner_id, 'ir.attachment', att.id, a_res['attachment_id']
                #                     )
                #             except Exception:
                #                 pass
                #import pdb; pdb.set_trace()
                #self.env['eadu.partner.any'].sudo()._sync_with_others('mail.message', message.id, vals)
                pass
        return res