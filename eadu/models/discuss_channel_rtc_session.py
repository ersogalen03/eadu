# Part of Eadu. See LICENSE file for full copyright and licensing details.

import json
import logging
from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.eadu.exceptions import EaduConnectionError

_logger = logging.getLogger(__name__)


class DiscussChannelRtcSession(models.Model):
    _inherit = "discuss.channel.rtc.session"

    eadu_partner_id = fields.Many2one("res.partner", index=True, ondelete="cascade")
    eadu_remote_session_id = fields.Integer(index=True)

    @api.model_create_multi
    def create(self, vals_list):
        sessions = super().create(vals_list)
        if self.env.context.get("eadu_rtc_sync"):
            for session in sessions:
                _logger.info(
                    "eadu rtc: synced remote session local=%s remote=%s channel=%s member=%s partner=%s",
                    session.id,
                    session.eadu_remote_session_id,
                    session.channel_id.id,
                    session.channel_member_id.id,
                    session.eadu_partner_id.name,
                )
            return sessions
        for session in sessions:
            _logger.info(
                "eadu rtc: local real session created session=%s channel=%s member=%s partner=%s",
                session.id,
                session.channel_id.id,
                session.channel_member_id.id,
                session.channel_member_id.partner_id.name,
            )
            session._eadu_announce_local_session()
        return sessions

    def unlink(self):
        if not self.env.context.get("eadu_rtc_sync"):
            for session in self.filtered(lambda s: not s.eadu_remote_session_id):
                for eadu_partner in session._eadu_channel_partners():
                    try:
                        eadu_partner._eadu_call(
                            "discuss.channel.rtc.session",
                            "action_eadu_session_left",
                            {"session_id": session.id},
                        )
                    except EaduConnectionError:
                        _logger.debug("eadu rtc: could not notify %s that session left", eadu_partner.name)
        return super().unlink()

    def _update_and_broadcast(self, values):
        super()._update_and_broadcast(values)
        if self.env.context.get("eadu_rtc_sync"):
            return
        for session in self.filtered(lambda s: not s.eadu_remote_session_id):
            _logger.warning(
                "EADU RTC DEBUG local state broadcast session=%s channel=%s member=%s partner=%s values=%s",
                session.id,
                session.channel_id.id,
                session.channel_member_id.id,
                session.channel_member_id.partner_id.name,
                session._eadu_session_state_values(values),
            )
            session._eadu_announce_local_session_update(values)

    def _notify_peers(self, notifications):
        self.ensure_one()
        remote_targets = self.env["discuss.channel.rtc.session"].sudo()
        target_ids = {target_id for target_ids, _content in notifications for target_id in target_ids}
        if target_ids:
            remote_targets = self.env["discuss.channel.rtc.session"].sudo().browse(
                list(target_ids)
            ).exists().filtered("eadu_remote_session_id")
        _logger.info(
            "eadu rtc: notify_peers sender=%s channel=%s targets=%s remote_targets=%s events=%s",
            self.id,
            self.channel_id.id,
            sorted(target_ids),
            {
                session.id: {
                    "remote_session": session.eadu_remote_session_id,
                    "partner": session.eadu_partner_id.name,
                }
                for session in remote_targets
            },
            [self._eadu_content_event(content) for _target_ids, content in notifications],
        )
        if not remote_targets:
            return super()._notify_peers(notifications)

        remote_targets_by_id = {session.id: session for session in remote_targets}
        local_notifications = []
        remote_items_by_partner = defaultdict(list)
        for target_session_ids, content in notifications:
            local_target_ids = []
            for target_id in target_session_ids:
                target = remote_targets_by_id.get(target_id)
                if not target:
                    local_target_ids.append(target_id)
                    continue
                remote_items_by_partner[target.eadu_partner_id].append({
                    "target_session_id": target.eadu_remote_session_id,
                    "sender_session_id": self.id,
                    "content": content,
                })
                _logger.info(
                    "eadu rtc: relay queued event=%s local_sender=%s synced_target=%s remote_target=%s partner=%s",
                    self._eadu_content_event(content),
                    self.id,
                    target.id,
                    target.eadu_remote_session_id,
                    target.eadu_partner_id.name,
                )
            if local_target_ids:
                local_notifications.append((local_target_ids, content))

        if local_notifications:
            super()._notify_peers(local_notifications)

        for eadu_partner, items in remote_items_by_partner.items():
            try:
                _logger.info(
                    "eadu rtc: sending relay partner=%s events=%s",
                    eadu_partner.name,
                    [self._eadu_content_event(item["content"]) for item in items],
                )
                eadu_partner._eadu_call(
                    "discuss.channel.rtc.session",
                    "action_eadu_relay_peer_notifications",
                    {"items": items},
                )
            except EaduConnectionError:
                _logger.debug("eadu rtc: could not relay signaling to %s", eadu_partner.name)

    @api.model
    def action_eadu_session_joined(self, channel_id, session_id, member_id, session_values=None):
        eadu_partner = self.env.user.partner_id
        if not eadu_partner.eadu_url:
            raise AccessError("RTC sync is only accepted from an EADU partner.")
        channel = self._eadu_find_local_channel(eadu_partner, channel_id)
        session = self._eadu_sync_remote_session(
            eadu_partner=eadu_partner,
            channel=channel,
            remote_session_id=session_id,
            remote_member_id=member_id,
            session_values=session_values,
            invite=True,
        )
        return {
            "synced_session_id": session.id,
            "other_sessions": self._eadu_local_sessions_for_remote(eadu_partner, channel, exclude=session),
        }

    @api.model
    def action_eadu_session_left(self, session_id):
        eadu_partner = self.env.user.partner_id
        if not eadu_partner.eadu_url:
            return
        sessions = self.sudo().search([
            ("eadu_partner_id", "=", eadu_partner.id),
            ("eadu_remote_session_id", "=", session_id),
        ])
        _logger.info(
            "eadu rtc: remote session left partner=%s remote=%s local_sessions=%s",
            eadu_partner.name,
            session_id,
            sessions.ids,
        )
        sessions.with_context(eadu_rtc_sync=True).unlink()

    @api.model
    def action_eadu_session_updated(self, session_id, values):
        eadu_partner = self.env.user.partner_id
        if not eadu_partner.eadu_url:
            raise AccessError("RTC update is only accepted from an EADU partner.")
        session = self.sudo().search([
            ("eadu_partner_id", "=", eadu_partner.id),
            ("eadu_remote_session_id", "=", session_id),
        ], limit=1)
        if not session:
            _logger.info(
                "eadu rtc: update ignored for unknown remote session partner=%s remote=%s values=%s",
                eadu_partner.name,
                session_id,
                values,
            )
            return
        values = self._eadu_session_state_values(values)
        _logger.info(
            "eadu rtc: applying remote session update partner=%s remote=%s local=%s values=%s",
            eadu_partner.name,
            session_id,
            session.id,
            values,
        )
        if values:
            session.with_context(eadu_rtc_sync=True)._update_and_broadcast(values)

    @api.model
    def action_eadu_relay_peer_notifications(self, items):
        eadu_partner = self.env.user.partner_id
        if not eadu_partner.eadu_url:
            raise AccessError("RTC relay is only accepted from an EADU partner.")
        _logger.info(
            "eadu rtc: incoming relay partner=%s events=%s",
            eadu_partner.name,
            [self._eadu_content_event(item.get("content")) for item in items],
        )
        for item in items:
            target = self.sudo().browse(int(item["target_session_id"])).exists()
            if not target:
                _logger.info("eadu rtc: relay target session gone target=%s", item["target_session_id"])
                continue
            sender = self.sudo().search([
                ("eadu_partner_id", "=", eadu_partner.id),
                ("eadu_remote_session_id", "=", int(item["sender_session_id"])),
                ("channel_id", "=", target.channel_id.id),
            ], limit=1)
            if not sender:
                _logger.warning(
                    "eadu rtc: missing synced sender partner=%s remote_sender=%s target=%s channel=%s known_synced=%s",
                    eadu_partner.name,
                    item["sender_session_id"],
                    target.id,
                    target.channel_id.id,
                    self.sudo().search([
                        ("eadu_partner_id", "=", eadu_partner.id),
                        ("channel_id", "=", target.channel_id.id),
                    ]).read(["id", "eadu_remote_session_id", "channel_member_id"]),
                )
                continue
            sender.write({})
            content = self._eadu_localize_content_channel(item["content"], target.channel_id.id)
            _logger.info(
                "eadu rtc: delivering event=%s synced_sender=%s remote_sender=%s target=%s channel=%s",
                self._eadu_content_event(content),
                sender.id,
                item["sender_session_id"],
                target.id,
                target.channel_id.id,
            )
            target._bus_send(
                "discuss.channel.rtc.session/peer_notification",
                {"sender": sender.id, "notifications": [content]},
            )

    def _eadu_announce_local_session(self):
        self.ensure_one()
        caller_partner = self.channel_member_id.partner_id
        EaduAny = self.env["eadu.partner.any"].sudo()
        for eadu_partner in self._eadu_channel_partners():
            member_map = EaduAny._search_for_eadu_partner(eadu_partner, "res.partner", caller_partner.id)
            if not member_map or not member_map.eadu_ident:
                _logger.warning(
                    "eadu rtc: no remote partner mapping for %s on %s",
                    caller_partner.name,
                    eadu_partner.name,
                )
                continue
            try:
                _logger.info(
                    "eadu rtc: announce local session=%s channel=%s remote_partner=%s remote_member=%s",
                    self.id,
                    self.channel_id.id,
                    eadu_partner.name,
                    member_map.eadu_ident,
                )
                result = eadu_partner._eadu_call(
                    "discuss.channel.rtc.session",
                    "action_eadu_session_joined",
                    {
                        "channel_id": self.channel_id.id,
                        "session_id": self.id,
                        "member_id": member_map.eadu_ident,
                        "session_values": self._eadu_session_state_values(),
                    },
                )
            except EaduConnectionError:
                _logger.warning("eadu rtc: could not announce session to %s", eadu_partner.name)
                continue
            _logger.info("eadu rtc: announce response session=%s result=%s", self.id, result)
            for remote_session in (result or {}).get("other_sessions", []):
                self._eadu_sync_remote_session(
                    eadu_partner=eadu_partner,
                    channel=self.channel_id,
                    remote_session_id=remote_session["session_id"],
                    remote_member_id=remote_session["member_id"],
                    session_values=remote_session.get("session_values"),
                    invite=False,
                )

    def _eadu_announce_local_session_update(self, values):
        self.ensure_one()
        values = self._eadu_session_state_values(values)
        if not values:
            return
        for eadu_partner in self._eadu_channel_partners():
            try:
                _logger.info(
                    "eadu rtc: announce local session update session=%s partner=%s values=%s",
                    self.id,
                    eadu_partner.name,
                    values,
                )
                eadu_partner._eadu_call(
                    "discuss.channel.rtc.session",
                    "action_eadu_session_updated",
                    {"session_id": self.id, "values": values},
                )
            except EaduConnectionError:
                _logger.debug("eadu rtc: could not announce session update to %s", eadu_partner.name)

    def _eadu_channel_partners(self):
        self.ensure_one()
        eadu_partners = self.env["res.partner"]
        for member in self.channel_id.channel_member_ids.sudo():
            if member.id == self.channel_member_id.id:
                continue
            eadu_partner = member.partner_id.eadu_connection_partner_id
            if not eadu_partner:
                continue
            channel_map = self.env["eadu.partner.any"].sudo()._search_for_eadu_partner(
                eadu_partner,
                "discuss.channel",
                self.channel_id.id,
            )
            if channel_map and channel_map.eadu_ident:
                eadu_partners |= eadu_partner
            else:
                _logger.info(
                    "eadu rtc: skipping eadu partner without channel map member=%s partner=%s eadu_partner=%s channel=%s",
                    member.id,
                    member.partner_id.name,
                    eadu_partner.name,
                    self.channel_id.id,
                )
        _logger.info(
            "eadu rtc: channel partners for session=%s channel=%s caller_member=%s eadu_partners=%s",
            self.id,
            self.channel_id.id,
            self.channel_member_id.id,
            eadu_partners.mapped("name"),
        )
        return eadu_partners

    @api.model
    def _eadu_sync_remote_session(self, eadu_partner, channel, remote_session_id, remote_member_id, session_values=None, invite=False):
        member = self._eadu_find_local_member(eadu_partner, channel, remote_member_id)
        _logger.warning(
            "EADU RTC DEBUG sync request partner=%s channel=%s remote_session=%s remote_member=%s "
            "local_member=%s local_partner=%s incoming_values=%s existing_id=%s existing_member_session=%s",
            eadu_partner.name,
            channel.id,
            remote_session_id,
            remote_member_id,
            member.id,
            member.partner_id.name,
            self._eadu_session_state_values(session_values),
            self.sudo().browse(int(remote_session_id)).exists().id,
            self.sudo().search([("channel_member_id", "=", member.id)], limit=1).id,
        )
        session = self.sudo().search([
            ("eadu_partner_id", "=", eadu_partner.id),
            ("eadu_remote_session_id", "=", remote_session_id),
            ("channel_id", "=", channel.id),
        ], limit=1)
        if session:
            _logger.info(
                "eadu rtc: reusing synced session local=%s remote=%s member=%s invite=%s",
                session.id,
                remote_session_id,
                member.id,
                invite,
            )
        else:
            occupied_id = self.sudo().browse(int(remote_session_id)).exists()
            if occupied_id:
                _logger.error(
                    "eadu rtc: REFUSING remote sync with colliding rtc session id; "
                    "cross-database RTC requires the same session ids, but id %s is already used locally. "
                    "existing_member=%s existing_partner=%s existing_remote=%s incoming_partner=%s "
                    "channel=%s remote_member=%s members=%s",
                    remote_session_id,
                    occupied_id.channel_member_id.id,
                    occupied_id.channel_member_id.partner_id.name,
                    occupied_id.eadu_remote_session_id,
                    eadu_partner.name,
                    channel.id,
                    remote_member_id,
                    self._eadu_channel_member_debug(channel),
                )
                raise AccessError("RTC session id collision while syncing an EADU call.")
            session = self.sudo().search([("channel_member_id", "=", member.id)], limit=1)
            if session:
                _logger.error(
                    "eadu rtc: REFUSING remote sync onto occupied channel member; "
                    "this means eadu.partner.any maps remote_member to the wrong local res.partner "
                    "or the remote proxy partner is already joined locally. "
                    "local_session=%s member=%s member_partner=%s member_partner_id=%s "
                    "existing_remote=%s incoming_remote=%s existing_eadu_partner=%s incoming_eadu_partner=%s "
                    "channel=%s remote_member=%s members=%s",
                    session.id,
                    member.id,
                    member.partner_id.name,
                    member.partner_id.id,
                    session.eadu_remote_session_id,
                    remote_session_id,
                    session.eadu_partner_id.name,
                    eadu_partner.name,
                    channel.id,
                    remote_member_id,
                    self._eadu_channel_member_debug(channel),
                )
                raise AccessError(
                    "RTC remote partner mapping points to a channel member that already has a local RTC session."
                )
            else:
                session = self.sudo().with_context(eadu_rtc_sync=True).create({
                    "id": int(remote_session_id),
                    "channel_member_id": member.id,
                    "eadu_partner_id": eadu_partner.id,
                    "eadu_remote_session_id": remote_session_id,
                })
                session._eadu_fix_sequence_after_explicit_id()
        session_values = self._eadu_session_state_values(session_values)
        if session_values:
            session.with_context(eadu_rtc_sync=True)._update_and_broadcast(session_values)
        if invite:
            self._eadu_invite_local_members(member, session)
        return session

    @api.model
    def _eadu_invite_local_members(self, inviter_member, inviter_session):
        channel = inviter_member.channel_id
        candidates = self.env["discuss.channel.member"].sudo().search(
            inviter_member.sudo()._get_rtc_invite_members_domain()
        )
        _logger.info(
            "eadu rtc: invite check channel=%s inviter_member=%s inviter_partner=%s "
            "inviter_session=%s candidates=%s members=%s",
            channel.id,
            inviter_member.id,
            inviter_member.partner_id.name,
            inviter_session.id,
            [
                {
                    "member_id": member.id,
                    "partner_id": member.partner_id.id,
                    "partner": member.partner_id.name,
                    "status": ",".join(filter(None, member.partner_id.user_ids.mapped("manual_im_status"))),
                }
                for member in candidates
            ],
            self._eadu_channel_member_debug(channel),
        )
        inviter_member.sudo()._rtc_invite_members()
        invited_members = channel.channel_member_ids.sudo().filtered(
            lambda member: member.rtc_inviting_session_id == inviter_session
        )
        _logger.info(
            "eadu rtc: invite result channel=%s inviter_session=%s invited_members=%s",
            channel.id,
            inviter_session.id,
            [
                {
                    "member_id": member.id,
                    "partner_id": member.partner_id.id,
                    "partner": member.partner_id.name,
                }
                for member in invited_members
            ],
        )

    def _eadu_local_sessions_for_remote(self, eadu_partner, channel, exclude):
        data = []
        for session in channel.rtc_session_ids.sudo().filtered(lambda s: not s.eadu_remote_session_id):
            if session.id == exclude.id:
                continue
            member_map = self.env["eadu.partner.any"].sudo().search([
                ("partner_id", "=", eadu_partner.id),
                ("res_model", "=", "res.partner"),
                ("res_id", "=", session.channel_member_id.partner_id.id),
            ], limit=1)
            if member_map and member_map.eadu_ident:
                data.append({
                    "session_id": session.id,
                    "member_id": member_map.eadu_ident,
                    "session_values": session._eadu_session_state_values(),
                })
        return data

    def _eadu_session_state_values(self, values=None):
        self.ensure_one() if self else None
        valid_keys = {"is_screen_sharing_on", "is_camera_on", "is_muted", "is_deaf"}
        if values is None:
            return {key: self[key] for key in valid_keys}
        return {key: values[key] for key in valid_keys if key in values}

    def _eadu_fix_sequence_after_explicit_id(self):
        self.ensure_one()
        self.env.cr.execute(
            """
            SELECT setval(
                pg_get_serial_sequence(%s, 'id'),
                GREATEST(
                    COALESCE((SELECT MAX(id) FROM discuss_channel_rtc_session), 1),
                    COALESCE((SELECT last_value FROM discuss_channel_rtc_session_id_seq), 1)
                )
            )
            """,
            [self._table],
        )

    def _eadu_find_local_channel(self, eadu_partner, remote_channel_id):
        channel_map = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_partner.id),
            ("res_model", "=", "discuss.channel"),
            ("eadu_ident", "=", remote_channel_id),
        ], limit=1)
        if not channel_map:
            raise AccessError("RTC channel is not mapped locally.")
        return self.env["discuss.channel"].sudo().browse(channel_map.res_id).exists()

    def _eadu_find_local_member(self, eadu_partner, channel, remote_member_id):
        # The caller sends eadu.partner.any.eadu_ident, which is already the
        # res.partner id to use in this receiving database. Do not reverse-map it
        # again through eadu.partner.any, or the synced RTC session can land on
        # the local user's own channel member and suppress ringing.
        member = channel.channel_member_ids.sudo().filtered(lambda m: m.partner_id.id == remote_member_id)
        if member:
            eadu_owner = member[0].partner_id.eadu_connection_partner_id
            if eadu_owner and eadu_owner.id == eadu_partner.id:
                _logger.info(
                    "eadu rtc: partner direct map eadu_partner=%s incoming_partner=%s member=%s members=%s",
                    eadu_partner.name,
                    remote_member_id,
                    member[0].id,
                    self._eadu_channel_member_debug(channel),
                )
                return member[0]
            _logger.warning(
                "eadu rtc: incoming partner is in channel but does not belong to eadu partner "
                "eadu_partner=%s incoming_partner=%s member=%s member_eadu_partner=%s members=%s",
                eadu_partner.name,
                remote_member_id,
                member[0].id,
                eadu_owner.name,
                self._eadu_channel_member_debug(channel),
            )
            raise AccessError("RTC member does not belong to the calling EADU partner.")

        member_maps = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_partner.id),
            ("res_model", "=", "res.partner"),
            ("eadu_ident", "=", remote_member_id),
        ])
        if not member_maps:
            raise AccessError("RTC member is not mapped locally.")
        channel_partner_ids = set(channel.channel_member_ids.sudo().mapped("partner_id").ids)
        channel_member_maps = member_maps.filtered(lambda m: m.res_id in channel_partner_ids)
        if not channel_member_maps:
            _logger.warning(
                "eadu rtc: mapped remote member is not a channel partner eadu_partner=%s "
                "remote_member=%s maps=%s channel=%s members=%s",
                eadu_partner.name,
                remote_member_id,
                self._eadu_partner_map_debug(member_maps),
                channel.id,
                self._eadu_channel_member_debug(channel),
            )
            raise AccessError("RTC member is not in the mapped channel.")
        if len(channel_member_maps) > 1:
            _logger.error(
                "eadu rtc: ambiguous remote member mapping eadu_partner=%s remote_member=%s "
                "candidate_maps=%s channel=%s members=%s",
                eadu_partner.name,
                remote_member_id,
                self._eadu_partner_map_debug(channel_member_maps),
                channel.id,
                self._eadu_channel_member_debug(channel),
            )
            raise AccessError("RTC member mapping is ambiguous.")
        member_map = channel_member_maps[0]
        _logger.info(
            "eadu rtc: partner reverse map fallback eadu_partner=%s remote_member=%s map=%s local_res_partner=%s all_maps=%s",
            eadu_partner.name,
            remote_member_id,
            member_map.id,
            member_map.res_id,
            self._eadu_partner_map_debug(member_maps),
        )
        member = channel.channel_member_ids.sudo().filtered(lambda m: m.partner_id.id == member_map.res_id)
        return member[0]

    @api.model
    def _eadu_partner_map_debug(self, maps):
        return [
            {
                "map_id": mapping.id,
                "partner_id": mapping.partner_id.id,
                "partner": mapping.partner_id.name,
                "res_id": mapping.res_id,
                "eadu_ident": mapping.eadu_ident,
                "master_status": mapping.master_status,
            }
            for mapping in maps
        ]

    @api.model
    def _eadu_channel_member_debug(self, channel):
        return [
            {
                "member_id": member.id,
                "partner_id": member.partner_id.id,
                "partner": member.partner_id.name,
                "commercial_partner_id": member.partner_id.commercial_partner_id.id,
                "eadu_partner": member.partner_id.eadu_connection_partner_id.name,
                "rtc_sessions": member.rtc_session_ids.ids,
                "rtc_remote_sessions": member.rtc_session_ids.mapped("eadu_remote_session_id"),
                "rtc_eadu_partners": member.rtc_session_ids.mapped("eadu_partner_id").mapped("name"),
            }
            for member in channel.channel_member_ids.sudo()
        ]

    @api.model
    def _eadu_localize_content_channel(self, content, local_channel_id):
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            return content
        if isinstance(data, dict):
            data["channelId"] = local_channel_id
            return json.dumps(data)
        return content

    @api.model
    def _eadu_content_event(self, content):
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            return "invalid-json"
        return data.get("event") if isinstance(data, dict) else "unknown"
