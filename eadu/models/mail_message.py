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
                channel_link = channel._eadu_ensure_remote_channel(eadu_partner)
                if not channel_link:
                    continue

                # Build ident_placeholders for any unresolved references.
                ident_placeholders = {}
                if channel_link and not channel_link.eadu_ident:
                    ident_placeholders['res_id'] = channel_link.id
                if epu and not epu.eadu_ident:
                    ident_placeholders['partner_id'] = epu.id

                results.append({
                    'partner': eadu_partner,
                    'payload': {
                        'model': 'discuss.channel',
                        'res_id': channel_link.eadu_ident or None,
                        'body': body,
                        'partner_id': (epu.eadu_ident or None) if epu else None,
                    },
                    'ident_placeholders': ident_placeholders,
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
                    'partner_id': eup.eadu_ident if eup else None,
                }
                return eadu_contact, result
        return False, False

    @api.model_create_multi
    def create(self, vals):
        if self.env.context.get('eadu_message'):
            return super(MailMessage, self).create(vals)
        recs = self.env['mail.message']
        EaduAny = self.env['eadu.partner.any'].sudo()
        for val in vals:
            created_record = super(MailMessage, self).create(val)
            body = val['body']
            model = val.get('model')
            res_id = val.get('res_id')
            result = False
            if body and model and res_id:
                partner_group, result = self._handle_eadu_msg(model, res_id, body)
                if result and partner_group:
                    # Normalize results to a list of {'partner', 'payload', 'ident_placeholders'}.
                    results_list = result if isinstance(result, list) else [
                        {'partner': partner_group, 'payload': result, 'ident_placeholders': {}}
                    ]
                    for item in results_list:
                        eadu_partner = item['partner']
                        payload = dict(item['payload'])
                        item_placeholders = dict(item.get('ident_placeholders') or {})
                        payload['eadu_ident'] = created_record.id

                        # ── Handle attachments ───────────────────────────────
                        att_any_ids = []
                        remote_att_ids = []
                        has_queued_att = False

                        for att in created_record.attachment_ids.sudo():
                            existing = EaduAny._search_for_eadu_partner(
                                eadu_partner, 'ir.attachment', att.id
                            )
                            if existing:
                                if existing.eadu_ident:
                                    remote_att_ids.append(existing.eadu_ident)
                                else:
                                    att_any_ids.append(existing.id)
                                    has_queued_att = True
                                continue

                            att_payload = {
                                'name': att.name,
                                'datas': att.datas.decode() if isinstance(att.datas, bytes) else att.datas,
                                'mimetype': att.mimetype,
                                'res_model': None,
                                'res_id': None,
                                'eadu_ident': att.id,
                            }
                            att_any = EaduAny._send_or_queue(
                                eadu_partner,
                                'ir.attachment',
                                'action_eadu_receive',
                                att_payload,
                                local_model='ir.attachment',
                                local_res_id=att.id,
                                result_key='attachment_id',
                                partner_master=False,
                            )
                            if att_any:
                                att_any_ids.append(att_any.id)
                                if att_any.eadu_ident:
                                    remote_att_ids.append(att_any.eadu_ident)
                                else:
                                    has_queued_att = True

                        # When any attachment is queued, store all attachment
                        # any-ids as placeholders so they are resolved on retry.
                        if has_queued_att:
                            item_placeholders['attachment_ids'] = att_any_ids
                            payload['attachment_ids'] = []
                        else:
                            payload['attachment_ids'] = remote_att_ids

                        # ── Send / queue the message itself ──────────────────
                        EaduAny._send_or_queue(
                            eadu_partner,
                            'mail.message',
                            'action_eadu_receive',
                            payload,
                            local_model='mail.message',
                            local_res_id=created_record.id,
                            result_key='message_id',
                            partner_master=False,
                            ident_placeholders=item_placeholders,
                        )

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
            # I am the master and need to relay to other EADU endpoints in the channel.
            EaduAny = self.env['eadu.partner.any'].sudo()
            channel = self.env['discuss.channel'].browse(res_id).sudo()
            partners = channel.channel_partner_ids
            for partner in partners:
                eadu_partner_upd = partner._get_eadu_partner()
                if not eadu_partner_upd or eadu_partner_upd == eadu_contact:
                    continue

                existing_msg_map = EaduAny._search_for_eadu_partner(
                    eadu_partner_upd, 'mail.message', message.id
                )
                if existing_msg_map:
                    continue

                # Find channel and partner mapping for this remote endpoint.
                channel_map = EaduAny._search_for_eadu_partner(
                    eadu_partner_upd, model, res_id
                )
                partner_map = EaduAny._search_for_eadu_partner(
                    eadu_partner_upd, 'res.partner', partner.id
                )
                if not channel_map or not partner_map:
                    continue

                # ── Handle attachments for this relay destination ────────────
                att_any_ids = []
                remote_att_ids = []
                has_queued_att = False

                for att in message.attachment_ids.sudo():
                    existing_att_map = EaduAny._search_for_eadu_partner(
                        eadu_partner_upd, 'ir.attachment', att.id
                    )
                    if existing_att_map:
                        if existing_att_map.eadu_ident:
                            remote_att_ids.append(existing_att_map.eadu_ident)
                        else:
                            att_any_ids.append(existing_att_map.id)
                            has_queued_att = True
                        continue

                    att_payload = {
                        'name': att.name,
                        'datas': att.datas.decode() if isinstance(att.datas, bytes) else att.datas,
                        'mimetype': att.mimetype,
                        'res_model': None,
                        'res_id': None,
                        'eadu_ident': att.id,
                    }
                    att_any = EaduAny._send_or_queue(
                        eadu_partner_upd,
                        'ir.attachment',
                        'action_eadu_receive',
                        att_payload,
                        local_model='ir.attachment',
                        local_res_id=att.id,
                        result_key='attachment_id',
                        partner_master=False,
                    )
                    if att_any:
                        att_any_ids.append(att_any.id)
                        if att_any.eadu_ident:
                            remote_att_ids.append(att_any.eadu_ident)
                        else:
                            has_queued_att = True

                relay_placeholders = {}
                if not channel_map.eadu_ident:
                    relay_placeholders['res_id'] = channel_map.id
                if not partner_map.eadu_ident:
                    relay_placeholders['partner_id'] = partner_map.id
                if has_queued_att:
                    relay_placeholders['attachment_ids'] = att_any_ids

                EaduAny._send_or_queue(
                    eadu_partner_upd,
                    'mail.message',
                    'action_eadu_receive',
                    {
                        'model': model,
                        'res_id': channel_map.eadu_ident or None,
                        'body': body,
                        'partner_id': partner_map.eadu_ident or None,
                        'eadu_ident': message.id,
                        'attachment_ids': [] if has_queued_att else remote_att_ids,
                    },
                    local_model='mail.message',
                    local_res_id=message.id,
                    result_key='message_id',
                    partner_master=False,
                    ident_placeholders=relay_placeholders,
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