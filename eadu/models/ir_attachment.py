# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _eadu_update_values_from_fields(self, fields_values):
        if not fields_values:
            return {}
        allowed_fields = set(self._eadu_update_field_names())
        vals = {}
        for field_name, value in fields_values.items():
            if field_name in allowed_fields and field_name in self._fields:
                vals[field_name] = value
        return vals

    def _eadu_update_field_names(self):
        return ['name', 'mimetype', 'datas']

    def _eadu_prepare_update_fields(self, field_names=None):
        allowed_fields = self._eadu_update_field_names()
        if field_names is not None:
            field_names = set(field_names)
            allowed_fields = [field for field in allowed_fields if field in field_names]
        return self.env['eadu.partner.any'].sudo()._serialize_fields(self, allowed_fields)

    def _eadu_send_update(self, eadu_any, field_names=None):
        self.ensure_one()
        vals = self._eadu_prepare_update_fields(field_names)
        if vals:
            self.env['eadu.partner.any'].sudo()._send_or_queue(
                eadu_any.partner_id,
                'ir.attachment',
                'action_eadu_update',
                {
                    'eadu_ident': eadu_any.res_id,
                    'fields': vals,
                },
                eadu_any_ref=eadu_any,
            )

    def _eadu_sync_created_or_updated(self, vals):
        if self.env.context.get('eadu_message'):
            return

        EaduAny = self.env['eadu.partner.any'].sudo()
        attachment = self
        res_model = vals.get('res_model') or attachment.res_model
        res_id = vals.get('res_id') or attachment.res_id
        target_models = {'mail.message'}
        if res_model and res_id and res_model in target_models:
            eadu_anys = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', res_model), ('res_id', '=', res_id)
            ])
            message = self.env['mail.message'].sudo().browse(res_id)

            for eadu_any in eadu_anys:
                eadu_contact = eadu_any.partner_id
                existing_att = EaduAny._search_for_eadu_partner(
                    eadu_contact, 'ir.attachment', attachment.id
                )
                if not existing_att:
                    datas = vals.get('datas') or attachment.datas
                    payload = {
                        'name': attachment.name,
                        'datas': datas.decode() if isinstance(datas, bytes) else datas,
                        'mimetype': attachment.mimetype,
                        'res_model': res_model,
                        'res_id': eadu_any.eadu_ident or None,
                        'eadu_ident': attachment.id,
                    }
                    msg_placeholder = {}
                    if not eadu_any.eadu_ident:
                        msg_placeholder['res_id'] = eadu_any.id

                    EaduAny._send_or_queue(
                        eadu_contact,
                        'ir.attachment',
                        'action_eadu_receive',
                        payload,
                        local_model='ir.attachment',
                        local_res_id=attachment.id,
                        result_key='attachment_id',
                        partner_master=False,
                        ident_placeholders=msg_placeholder,
                    )
                if message:
                    message._eadu_send_update(eadu_any, {'attachment_ids'})

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
            EaduAny = self.env['eadu.partner.any'].sudo()
            changed_fields = set(vals) & set(self._eadu_update_field_names())
            for att in self:
                # Perform same sync as in create
                linked_to_message = (
                    ('res_model' in vals or 'res_id' in vals)
                    and (vals.get('res_model') or att.res_model) in {'mail.message'}
                )
                if linked_to_message:
                    att._eadu_sync_created_or_updated(vals)
                if changed_fields:
                    eadu_anys = EaduAny.search([
                        ('res_model', '=', 'ir.attachment'),
                        ('res_id', '=', att.id),
                    ])
                    for eadu_any in eadu_anys:
                        att._eadu_send_update(eadu_any, changed_fields)
                if att.res_model == 'mail.message' and att.res_id:
                    if not linked_to_message:
                        message = self.env['mail.message'].sudo().browse(att.res_id)
                        eadu_anys = EaduAny.search([
                            ('res_model', '=', 'mail.message'),
                            ('res_id', '=', att.res_id),
                        ])
                        for eadu_any in eadu_anys:
                            message._eadu_send_update(eadu_any, {'attachment_ids'})
        return res

    # Remote entry point to create attachment
    def action_eadu_receive(self, name, datas, mimetype, res_model, res_id, eadu_ident):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise

        # Map remote target to local
        target_map = self.env['eadu.partner.any']._search_for_eadu_partner(
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
                message.with_context(eadu_message=True).write({'attachment_ids': [(4, att.id)]})
            except Exception:
                pass

        # No special handling for discuss.channel here; attachments are sent
        # and linked from the mail.message creation flow for better UX.

        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            eadu_contact, 'ir.attachment', att.id, eadu_ident, partner_master=True
        )
        return {'attachment_id': att.id}

    def action_eadu_update(self, eadu_ident, fields):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise
        eadu_any = self.env['eadu.partner.any'].sudo()._search_for_eadu_ident(
            eadu_contact, 'ir.attachment', eadu_ident
        )
        if not eadu_any:
            return {'result': False}
        vals = self._eadu_update_values_from_fields(fields)
        if vals:
            eadu_any._get_record().sudo().with_context(eadu_message=True).write(vals)
        return {'result': True}
