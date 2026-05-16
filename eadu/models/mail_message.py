# Part of Eadu. See LICENSE file for full copyright and licensing details.

from datetime import datetime
import re

from markupsafe import Markup
from odoo import api, fields, models
from odoo.exceptions import AccessError

class MailMessage(models.Model):
    _inherit = "mail.message"

    def _is_eadu_internal_notification_body(self, body):
        if not body:
            return False
        body = str(body)
        return "o_mail_notification" in body or re.search(r"\bdata-oe-type=", body)

    def _is_eadu_internal_notification_message(self, message, body):
        return (
            message.message_type == "notification"
            and self._is_eadu_internal_notification_body(body)
        )

    def _convert_model(self, model, res_id):
        return model, res_id

    def _rewrite_oe_links(self, body, eadu_partner):
        """
        Replace every ``data-oe-model`` / ``data-oe-id`` href pair in *body*
        with the remote counterpart id when a matching ``eadu.partner.any``
        record exists for *eadu_partner*.

        Works for any model (res.partner, product.template, ir.attachment, …)
        as long as the record was previously synced and has an eadu_ident.

        Returns the rewritten body (unchanged if no replacements were made).
        """
        EaduAny = self.env['eadu.partner.any'].sudo()
        pattern = re.compile(
            r'data-oe-model="(?P<model>[^"]+)"\s+data-oe-id="(?P<id>[0-9]+)"'
            r'|'
            r'data-oe-id="(?P<id2>[0-9]+)"\s+data-oe-model="(?P<model2>[^"]+)"'
        )
        def _replace(m):
            oe_model = m.group('model') or m.group('model2')
            oe_id    = int(m.group('id') or m.group('id2'))
            link = EaduAny._search_for_eadu_partner(eadu_partner, oe_model, oe_id)
            if link and link.eadu_ident:
                remote_id = link.eadu_ident
                return (
                    f'data-oe-model="{oe_model}" data-oe-id="{remote_id}"'
                )
            # No mapping yet – leave the original text as-is so the link is
            # at least visible on the remote side, even if it cannot resolve.
            return m.group(0)

        return pattern.sub(_replace, body)

    def _eadu_attachment_payload(self, attachment):
        datas = attachment.datas
        return {
            'name': attachment.name,
            'datas': datas.decode() if isinstance(datas, bytes) else datas,
            'mimetype': attachment.mimetype,
            'res_model': None,
            'res_id': None,
            'eadu_ident': attachment.id,
        }

    def _eadu_prepare_remote_attachment_ids(self, eadu_partner):
        self.ensure_one()
        EaduAny = self.env['eadu.partner.any'].sudo()
        att_any_ids = []
        remote_att_ids = []
        has_queued_att = False

        for att in self.attachment_ids.sudo():
            existing = EaduAny._search_for_eadu_partner(
                eadu_partner, 'ir.attachment', att.id
            )
            if existing:
                att_any_ids.append(existing.id)
                if existing.eadu_ident:
                    remote_att_ids.append(existing.eadu_ident)
                else:
                    has_queued_att = True
                continue

            att_any = EaduAny._send_or_queue(
                eadu_partner,
                'ir.attachment',
                'action_eadu_receive',
                self._eadu_attachment_payload(att),
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

        return remote_att_ids, att_any_ids, has_queued_att

    def _eadu_add_parent_mapping_to_payload(self, message, eadu_partner, payload, ident_placeholders):
        if not message.parent_id:
            return
        parent_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
            eadu_partner, 'mail.message', message.parent_id.id
        )
        if not parent_map:
            return
        payload['parent_id'] = parent_map.eadu_ident or None
        if not parent_map.eadu_ident:
            ident_placeholders['parent_id'] = parent_map.id

    def _eadu_update_field_names(self):
        return ['body', 'attachment_ids', 'pinned_at']

    def _eadu_sync_origin(self):
        db_name = self.env['res.partner']._get_db_name()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}#{db_name}#mail.message#{self.id}"

    def _eadu_sync_timestamp(self):
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")

    def _eadu_next_sync_version(self, current_version=None, field_names=None):
        self.ensure_one()
        current_version = current_version or {}
        current_seq = int(current_version.get('seq') or 0)
        return {
            'fields': sorted(field_names or self._eadu_update_field_names()),
            'seq': current_seq + 1,
            'origin': self._eadu_sync_origin(),
            'ts': self._eadu_sync_timestamp(),
        }

    def _eadu_version_sort_key(self, version):
        version = version or {}
        return (
            version.get('seq') or 0,
            version.get('origin') or '',
            version.get('ts') or '',
        )

    def _eadu_incoming_version_is_newer(self, incoming_version, current_version):
        return self._eadu_version_sort_key(incoming_version) > self._eadu_version_sort_key(current_version)

    def _eadu_latest_sync_version(self, *versions):
        return max(
            (version for version in versions if version),
            key=self._eadu_version_sort_key,
            default=False,
        )

    def _eadu_version_field_names(self, version):
        field_names = set((version or {}).get('fields') or [])
        return field_names or set(self._eadu_update_field_names())

    def _eadu_send_update(self, eadu_any, field_names=None, sync_version=None):
        self.ensure_one()
        EaduAny = self.env['eadu.partner.any'].sudo()
        field_names = set(field_names or self._eadu_update_field_names())
        base_version = eadu_any.sync_version
        sync_version = sync_version or self._eadu_next_sync_version(base_version, field_names)
        fields_values = {}
        if 'body' in field_names:
            fields_values['body'] = self._rewrite_oe_links(
                EaduAny._serialize_field_value(self, 'body'), eadu_any.partner_id
            )
        if 'pinned_at' in field_names:
            fields_values['pinned_at'] = (
                fields.Datetime.to_string(self.pinned_at) if self.pinned_at else False
            )

        params = {
            'eadu_ident': eadu_any.res_id,
            'fields': fields_values,
            'base_version': base_version,
            'sync_version': sync_version,
        }
        ident_placeholders = {}
        if 'attachment_ids' in field_names:
            remote_att_ids, att_any_ids, has_queued_att = self._eadu_prepare_remote_attachment_ids(
                eadu_any.partner_id
            )
            params['attachment_ids'] = [] if has_queued_att else remote_att_ids
            if has_queued_att:
                ident_placeholders['attachment_ids'] = att_any_ids

        if not fields_values and 'attachment_ids' not in params:
            return

        EaduAny._send_or_queue(
            eadu_any.partner_id,
            'mail.message',
            'action_eadu_update',
            params,
            eadu_any_ref=eadu_any,
            ident_placeholders=ident_placeholders,
        )
        eadu_any.sync_version = sync_version

    def _handle_eadu_msg(self, model, res_id, body):
        if model == 'discuss.channel':
            channel = self.env['discuss.channel'].browse(res_id)
            partners = channel.channel_partner_ids

            eadu_partners = self.env['res.partner']
            for partner in partners:
                if eadu_p := partner.eadu_connection_partner_id:
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
                    if partner.eadu_connection_partner_id == eadu_partner:
                        continue
                    partner._eadu_ensure_remote_partner(eadu_partner)

                # 2. Ensure the message author exists remotely.
                epu = self.env.user.partner_id._eadu_ensure_remote_partner(eadu_partner)

                # 3. Ensure the channel itself exists remotely (uses mappings from steps 1-2).
                channel_link = channel._eadu_ensure_remote_channel(eadu_partner)
                if not channel_link:
                    continue

                # 4. Rewrite any data-oe-model/data-oe-id links in the body.
                rewritten_body = self._rewrite_oe_links(body, eadu_partner)

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
                        'body': rewritten_body,
                        'partner_id': (epu.eadu_ident or None) if epu else None,
                    },
                    'ident_placeholders': ident_placeholders,
                })

            return eadu_partners, results

        # Non-channel messages: try to find an eadu partner from any linked
        # record referenced by a data-oe-model/data-oe-id link in the body.
        EaduAny = self.env['eadu.partner.any'].sudo()
        oe_pattern = re.compile(
            r'data-oe-model="(?P<model>[^"]+)"\s+data-oe-id="(?P<id>[0-9]+)"'
            r'|'
            r'data-oe-id="(?P<id2>[0-9]+)"\s+data-oe-model="(?P<model2>[^"]+)"'
        )
        for m in oe_pattern.finditer(body):
            oe_model = m.group('model') or m.group('model2')
            oe_id    = int(m.group('id') or m.group('id2'))
            # Look up all eadu partners that have synced this record.
            links = EaduAny.search([('res_model', '=', oe_model), ('res_id', '=', oe_id)])
            if not links:
                continue
            # Use the first partner found (or improve with multi-partner logic later).
            eadu_contact = links[0].partner_id
            model, res_id = self._convert_model(model, res_id)
            rewritten_body = self._rewrite_oe_links(body, eadu_contact)
            eup = EaduAny._search_for_eadu_partner(
                eadu_contact, 'res.partner', self.env.user.partner_id.id
            )
            result = {
                'model': model,
                'res_id': res_id,
                'body': rewritten_body,
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
            if self._is_eadu_internal_notification_message(created_record, body):
                recs += created_record
                continue
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
                        created_record._eadu_add_parent_mapping_to_payload(
                            created_record, eadu_partner, payload, item_placeholders
                        )

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

                            att_any = EaduAny._send_or_queue(
                                eadu_partner,
                                'ir.attachment',
                                'action_eadu_receive',
                                created_record._eadu_attachment_payload(att),
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

                        sync_version = created_record._eadu_next_sync_version()

                        # ── Send / queue the message itself ──────────────────
                        message_any = EaduAny._send_or_queue(
                            eadu_partner,
                            'mail.message',
                            'action_eadu_receive',
                            dict(payload, sync_version=sync_version),
                            local_model='mail.message',
                            local_res_id=created_record.id,
                            result_key='message_id',
                            partner_master=False,
                            ident_placeholders=item_placeholders,
                        )
                        if message_any:
                            message_any.sync_version = sync_version

            recs += created_record
        return recs
                    

    def action_eadu_receive(self, model, res_id, body, partner_id, eadu_ident, attachment_ids=None, sync_version=None, parent_id=None):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise

        if self._is_eadu_internal_notification_body(body):
            return {'message_id': False}

        vals = {
            'body': Markup(body),
            'author_id': partner_id,
            'res_id': res_id,
            'model': model,
        }
        if parent_id:
            vals['parent_id'] = parent_id
        #TODO: check that attachment_ids are legal...
        if attachment_ids:
            self.env['ir.attachment'].browse(attachment_ids).sudo().with_context(eadu_message=True).write({
                'res_model': model,
                'res_id': res_id,
            })
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
        message_any = self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            eadu_contact, 'mail.message', message.id, eadu_ident,
            partner_master=partner_master,
        )
        if sync_version:
            message_any.sync_version = sync_version
        if not partner_master:
            # I am the master and need to relay to other EADU endpoints in the channel.
            EaduAny = self.env['eadu.partner.any'].sudo()
            channel = self.env['discuss.channel'].browse(res_id).sudo()
            partners = channel.channel_partner_ids
            for partner in partners:
                eadu_partner_upd = partner.eadu_connection_partner_id
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

                    att_any = EaduAny._send_or_queue(
                        eadu_partner_upd,
                        'ir.attachment',
                        'action_eadu_receive',
                        message._eadu_attachment_payload(att),
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
                parent_id = None
                parent_map = EaduAny._search_for_eadu_partner(
                    eadu_partner_upd, 'mail.message', message.parent_id.id
                ) if message.parent_id else False
                if parent_map:
                    parent_id = parent_map.eadu_ident or None
                    if not parent_map.eadu_ident:
                        relay_placeholders['parent_id'] = parent_map.id

                relay_any = EaduAny._send_or_queue(
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
                        'sync_version': sync_version,
                        'parent_id': parent_id,
                    },
                    local_model='mail.message',
                    local_res_id=message.id,
                    result_key='message_id',
                    partner_master=False,
                    ident_placeholders=relay_placeholders,
                )
                if relay_any and sync_version:
                    relay_any.sync_version = sync_version



        return {'message_id': message.id}

    def action_eadu_update(self, eadu_ident, fields=None, attachment_ids=None, base_version=None, sync_version=None):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise

        eadu_any = self.env['eadu.partner.any'].sudo()._search_for_eadu_ident(
            eadu_contact, 'mail.message', eadu_ident
        )
        if not eadu_any:
            return {'result': False}

        message = eadu_any._get_record().sudo()
        current_version = eadu_any.sync_version
        fields = fields or {}
        incoming_field_names = set(fields)
        if attachment_ids is not None:
            incoming_field_names.add('attachment_ids')
        has_conflict = current_version and base_version and current_version != base_version
        merge_non_overlapping = False
        if has_conflict:
            local_field_names = self._eadu_version_field_names(current_version)
            has_field_overlap = bool(incoming_field_names & local_field_names) or not incoming_field_names
            if not self._eadu_incoming_version_is_newer(sync_version, current_version) and has_field_overlap:
                message._eadu_send_update(eadu_any, sync_version=current_version)
                return {'result': False, 'conflict': True}
            merge_non_overlapping = not has_field_overlap

        vals = {}
        if 'body' in fields:
            vals['body'] = Markup(fields['body'])
        if 'pinned_at' in fields:
            vals['pinned_at'] = fields['pinned_at'] or False
        if attachment_ids is not None:
            if attachment_ids:
                self.env['ir.attachment'].browse(attachment_ids).sudo().with_context(eadu_message=True).write({
                    'res_model': message.model,
                    'res_id': message.res_id,
                })
            vals['attachment_ids'] = [(6, 0, attachment_ids)]

        if vals:
            message.with_context(eadu_message=True).write(vals)
        if sync_version:
            eadu_any.sync_version = sync_version
        if merge_non_overlapping:
            eadu_any.sync_version = self._eadu_latest_sync_version(current_version, sync_version)
            message._eadu_send_update(eadu_any)

        if message.model == 'discuss.channel' and eadu_any.master_status == 'me':
            relay_anys = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', 'mail.message'),
                ('res_id', '=', message.id),
                ('partner_id', '!=', eadu_contact.id),
            ])
            for relay_any in relay_anys:
                message._eadu_send_update(relay_any)

        return {'result': True, 'conflict': bool(has_conflict), 'merged': merge_non_overlapping}

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message'):
            changed_fields = set(vals) & set(self._eadu_update_field_names())
            if not changed_fields:
                return res
            EaduAny = self.env['eadu.partner.any'].sudo()
            for message in self:
                eadu_anys = EaduAny.search([
                    ('res_model', '=', 'mail.message'),
                    ('res_id', '=', message.id),
                ])
                for eadu_any in eadu_anys:
                    message._eadu_send_update(eadu_any, changed_fields)
        return res
