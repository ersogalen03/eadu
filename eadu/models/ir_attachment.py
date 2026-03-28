# Part of Eadu. See LICENSE file for full copyright and licensing details.

import base64
from odoo import api, models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _eadu_sync_created_or_updated(self, vals):
        if self.env.context.get('eadu_message'):
            return
        
        attachment = self
        res_model = vals.get('res_model') or attachment.res_model
        res_id = vals.get('res_id') or attachment.res_id
        target_models = {'mail.message'}
        if res_model and res_id and res_model in target_models:
            eadu_anys = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', res_model), ('res_id', '=', res_id)
            ])

            for eadu_any in eadu_anys:
                eadu_contact = eadu_any.partner_id

                datas = vals.get('datas') or attachment.datas
                payload = {
                    'name': attachment.name,
                    'datas': datas.decode() if isinstance(datas, bytes) else datas,
                    'mimetype': attachment.mimetype,
                    'res_model': res_model,
                    'res_id': eadu_any.eadu_ident,
                    'eadu_ident': attachment.id,
                }
                res = eadu_contact.sudo()._eadu_call('ir.attachment', 'action_eadu_receive', payload)
                if res and 'attachment_id' in res:
                    self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                        eadu_contact, 'ir.attachment', attachment.id, res['attachment_id']
                    )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('eadu_message'):
            return super().create(vals_list)

        attachments = self.env['ir.attachment']
        for vals in vals_list:
            attachment = super(IrAttachment, self).create(vals)

            # Sync to remote if target model qualifies (shared logic with write)
            attachment._eadu_sync_created_or_updated(vals)

            attachments |= attachment
        return attachments

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message'):
            for att in self:
                # Perform same sync as in create
                if 'res_model' in vals and vals['res_model'] in {'mail.message'}:
                    att._eadu_sync_created_or_updated(vals)
                # Existing cross-partner sync
                #self.env['eadu.partner.any'].sudo()._sync_with_others('ir.attachment', att.id, vals)
        return res

    # Remote entry point to create attachment
    def action_eadu_receive(self, name, datas, mimetype, res_model, res_id, eadu_ident):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise

        # Map remote target to local
        target_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
            eadu_contact, res_model, res_id
        )
        local_res_id = target_map and target_map.res_id

        create_vals = {
            'name': name,
            'datas': datas,
            'mimetype': mimetype,
        }
        # attach either directly to target record or link to message
        if local_res_id:
            create_vals.update({'res_model': res_model, 'res_id': local_res_id})
        att = self.env['ir.attachment'].sudo().with_context(eadu_message=True).create(create_vals)

        # If target is a message, ensure m2m link exists
        if res_model == 'mail.message' and local_res_id:
            message = self.env['mail.message'].sudo().browse(local_res_id)
            try:
                # Link attachment to message
                message.write({'attachment_ids': [(4, att.id)]})
            except Exception:
                pass

        # No special handling for discuss.channel here; attachments are sent
        # and linked from the mail.message creation flow for better UX.

        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            eadu_contact, 'ir.attachment', att.id, eadu_ident, partner_master=True
        )
        return {'attachment_id': att.id}
