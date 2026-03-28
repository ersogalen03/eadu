# Part of Eadu. See LICENSE file for full copyright and licensing details.

import base64
from unittest.mock import patch

from odoo.tests import common, tagged

from odoo.addons.eadu.models import res_partner as res_partner_model


@tagged("post_install", "-at_install")
class TestEaduExchangeMultiCompany(common.TransactionCase):
    def setUp(self):
        super().setUp()
        self.ResCompany = self.env["res.company"].sudo()
        self.ResPartner = self.env["res.partner"].sudo()
        self.ResUsers = self.env["res.users"].sudo()

        self.company_a = self.ResCompany.create({"name": "EADU Test Company A"})
        self.company_b = self.ResCompany.create({"name": "EADU Test Company B"})

        # Partners representing remote databases in each company.
        self.partner_a = self.ResPartner.create(
            {
                "name": "DB A",
                "company_type": "company",
                "company_id": self.company_a.id,
            }
        )
        self.partner_b = self.ResPartner.create(
            {
                "name": "DB B",
                "company_type": "company",
                "company_id": self.company_b.id,
            }
        )

        group_user = self.env.ref("base.group_user")
        group_partner_manager = self.env.ref("base.group_partner_manager")
        group_system = self.env.ref("base.group_system")
        self.user_a = self.ResUsers.create(
            {
                "name": "Alice A",
                "login": "alice_a_eadu_test",
                "email": "alice_a@example.com",
                "company_id": self.company_a.id,
                "company_ids": [(6, 0, [self.company_a.id])],
                "group_ids": [(6, 0, [group_user.id, group_partner_manager.id, group_system.id])],
            }
        )
        self.user_b = self.ResUsers.create(
            {
                "name": "Bob B",
                "login": "bob_b_eadu_test",
                "email": "bob_b@example.com",
                "company_id": self.company_b.id,
                "company_ids": [(6, 0, [self.company_b.id])],
                "group_ids": [(6, 0, [group_user.id, group_partner_manager.id, group_system.id])],
            }
        )

        self._eadu_call_patcher = patch.object(
            res_partner_model.ResPartner,
            "_eadu_call",
            autospec=True,
            side_effect=self._local_cross_company_call,
        )
        self._eadu_call_patcher.start()
        self.addCleanup(self._eadu_call_patcher.stop)

    def _get_eadu_portal_user_from_apikey(self, eadu_contact):
        if not eadu_contact.eadu_apikey:
            raise AssertionError("Missing eadu_apikey for mocked _eadu_call")

        user_id = self.env["res.users.apikeys"].sudo()._check_credentials(
            scope="rpc",
            key=eadu_contact.eadu_apikey,
        )
        if not user_id:
            raise AssertionError("No user found for eadu_apikey in mocked _eadu_call")

        eadu_user = self.ResUsers.browse(user_id)
        if not eadu_user.has_group("eadu.group_portal_eadu"):
            raise AssertionError("Resolved user is not in eadu.group_portal_eadu")
        return eadu_user

    def _local_cross_company_call(self, eadu_contact, model, method, params):
        """Route cross-db RPC calls locally using the user identified by eadu_apikey."""
        remote_user = self._get_eadu_portal_user_from_apikey(eadu_contact)
        remote_model = self.env[model].with_user(remote_user)
        return getattr(remote_model, method)(**params)

    def test_exchange_token_flow_via_multicompany(self):
        # Company A starts the handshake and emits the exchange token.
        exchange_token = self.partner_a.with_user(self.user_a)._generate_eadu_exchange()
        # Validate token format so test fails early with useful signal.
        base64.b64decode(exchange_token.encode()).decode()

        eadu_contact_a = self.partner_a.child_ids.filtered(
            lambda p: p.name == "EADU" + self.partner_a.name and p.type == "other"
        )[:1]
        self.assertTrue(eadu_contact_a, "EADU contact should be created for company A")
        eadu_user_a = eadu_contact_a.user_ids.filtered(
            lambda u: u.has_group("eadu.group_portal_eadu")
        )[:1]
        self.assertTrue(eadu_user_a, "EADU portal user should exist for company A")

        partner_b_as_user_b = self.partner_b.with_user(self.user_b)
        partner_b_as_user_b.eadu_exchanged = exchange_token
        partner_b_as_user_b.button_process_eadu_exchanged()

        # Both sides should now contain one child contact linked during exchange.
        self.assertTrue(
            self.partner_a.child_ids.filtered(lambda p: p.name == self.user_b.name),
            "Company A should have a child contact for user B",
        )
        self.assertTrue(
            self.partner_b.child_ids.filtered(lambda p: p.name == self.user_a.name),
            "Company B should have a child contact for user A",
        )

        # Confirm EADU mapping records were created on both sides.
        mapping_b = self.env["eadu.partner.any"].sudo().search(
            [
                ("partner_id", "=", self.partner_b._get_eadu_partner().id),
                ("res_model", "=", "res.partner"),
            ]
        )
        mapping_a = self.env["eadu.partner.any"].sudo().search(
            [
                ("partner_id", "=", self.partner_a._get_eadu_partner().id),
                ("res_model", "=", "res.partner"),
            ]
        )
        self.assertTrue(mapping_a)
        self.assertTrue(mapping_b)

        # Use contacts generated by the exchange flow in company A:
        # - Alice: the regular local partner linked to user A.
        # - Bob: the exchanged child contact coming from company B.
        alice_contact_a = self.user_a.partner_id
        bob_contact_a = self.partner_a.child_ids.filtered(
            lambda p: p.name == self.user_b.name and p.type == "contact"
        )[:1]
        self.assertTrue(
            bob_contact_a,
            "Expected exchanged Bob contact under company A before channel creation",
        )

        channel_name = "EADU Company A Channel"
        channel_a = self.env["discuss.channel"].with_user(self.user_a).create(
            {
                "name": channel_name,
                "channel_type": "channel",
                "channel_partner_ids": [
                    (4, alice_contact_a.id),
                    (4, bob_contact_a.id),
                ],
            }
        )

        body = "Message sent from company A"
        channel_a.with_user(self.user_a).message_post(body=body)

        # No company_id exists on discuss.channel, so the mirrored copy is
        # another discuss.channel record in the same database during this test.
        mirrored_channels = self.env["discuss.channel"].sudo().search(
            [("name", "=", channel_name), ("id", "!=", channel_a.id)]
        )
        self.assertTrue(
            mirrored_channels,
            "Posting in the exchanged channel should create a mirrored discuss channel",
        )
        mirrored_channel = mirrored_channels[0]

        local_eadu_contact = self.partner_a._get_eadu_partner()
        channel_mapping = self.env["eadu.partner.any"].sudo().search(
            [
                ("partner_id", "=", local_eadu_contact.id),
                ("res_model", "=", "discuss.channel"),
                ("res_id", "=", channel_a.id),
            ],
            limit=1,
        )
        self.assertTrue(channel_mapping, "A discuss.channel mapping should be created")
        self.assertEqual(
            channel_mapping.eadu_ident,
            mirrored_channel.id,
            "discuss.channel mapping should point to the mirrored channel id",
        )

        mirrored_message = self.env["mail.message"].sudo().search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", mirrored_channel.id),
                ("body", "ilike", body),
            ],
            limit=1,
        )
        self.assertTrue(
            mirrored_message,
            "The posted message should be replicated to the mirrored channel",
        )

        # Reply from the mirrored side with an attachment, then ensure this
        # message (and its attachment) is synced back to the original channel.
        remote_user = mirrored_channel.channel_partner_ids.user_ids.filtered(
            lambda u: u.company_id == self.company_b
        )[:1]
        self.assertTrue(
            remote_user,
            "Expected a company B user in mirrored channel members",
        )
        attachment_remote = self.env["ir.attachment"].with_user(remote_user).create(
            {
                "name": "reply-from-b.txt",
                "datas": base64.b64encode(b"reply from company B"),
                "mimetype": "text/plain",
            }
        )
        reply_body = "Message with attachment from company B"
        mirrored_channel.with_user(remote_user).message_post(
            body=reply_body,
            attachment_ids=[attachment_remote.id],
        )

        mirrored_reply_local = self.env["mail.message"].sudo().search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", channel_a.id),
                ("body", "ilike", reply_body),
            ],
            limit=1,
        )
        self.assertTrue(
            mirrored_reply_local,
            "Reply from company B should be replicated to company A channel",
        )
        self.assertTrue(
            mirrored_reply_local.attachment_ids,
            "Replicated reply should include attachment",
        )
        self.assertIn(
            "reply-from-b.txt",
            mirrored_reply_local.attachment_ids.mapped("name"),
            "Replicated attachment should keep original filename",
        )

    def test_exchange_token_flow_three_companies_discuss_triplication(self):
        # Create company C and user C.
        company_c = self.ResCompany.create({"name": "EADU Test Company C"})
        partner_c = self.ResPartner.create(
            {
                "name": "DB C",
                "company_type": "company",
                "company_id": company_c.id,
            }
        )
        group_user = self.env.ref("base.group_user")
        group_partner_manager = self.env.ref("base.group_partner_manager")
        group_system = self.env.ref("base.group_system")
        user_c = self.ResUsers.create(
            {
                "name": "Carol C",
                "login": "carol_c_eadu_test",
                "email": "carol_c@example.com",
                "company_id": company_c.id,
                "company_ids": [(6, 0, [company_c.id])],
                "group_ids": [(6, 0, [group_user.id, group_partner_manager.id, group_system.id])],
            }
        )

        # First exchange A <-> B.
        exchange_token_ab = self.partner_a.with_user(self.user_a)._generate_eadu_exchange()
        self.partner_b.with_user(self.user_b).write({"eadu_exchanged": exchange_token_ab})
        self.partner_b.with_user(self.user_b).button_process_eadu_exchanged()

        # Then exchange A <-> C.
        exchange_token_ac = self.partner_a.with_user(self.user_a)._generate_eadu_exchange()
        partner_c.with_user(user_c).write({"eadu_exchanged": exchange_token_ac})
        partner_c.with_user(user_c).button_process_eadu_exchanged()

        # Use one exchanged contact on B side and one on C side so message fan-out
        # resolves to two distinct EADU endpoints (B and C).
        alice_contact_b = self.partner_b.child_ids.filtered(
            lambda p: p.name == self.user_a.name and p.type == "contact"
        )[:1]
        alice_contact_c = partner_c.child_ids.filtered(
            lambda p: p.name == self.user_a.name and p.type == "contact"
        )[:1]
        self.assertTrue(alice_contact_b, "Expected exchanged Alice contact under company B")
        self.assertTrue(alice_contact_c, "Expected exchanged Alice contact under company C")

        # Company A sends one message to a channel involving A/B/C participants.
        channel_name = "EADU Three Companies Channel"
        channel_a = self.env["discuss.channel"].with_user(self.user_a).create(
            {
                "name": channel_name,
                "channel_type": "channel",
                "channel_partner_ids": [
                    (4, self.user_a.partner_id.id),
                    (4, alice_contact_b.id),
                    (4, alice_contact_c.id),
                ],
            }
        )
        body = "Message sent from company A to B and C"
        channel_a.with_user(self.user_a).message_post(body=body)

        # With 3 companies in the exchange graph, we expect 3 discuss channels
        # in total: original + one mirrored copy per remote company.
        all_channels = self.env["discuss.channel"].sudo().search([("name", "=", channel_name)])
        self.assertEqual(
            len(all_channels),
            3,
            "Three-company exchange should result in exactly three discuss channels",
        )

        mirrored_channels = all_channels.filtered(lambda ch: ch.id != channel_a.id)
        mirrored_messages = self.env["mail.message"].sudo().search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "in", mirrored_channels.ids),
                ("body", "ilike", body),
            ]
        )
        self.assertEqual(
            len(mirrored_messages),
            2,
            "The original message should be replicated once in each mirrored channel",
        )

        # Check discuss.channel mappings and their master status for B and C.
        eadu_contact_b = self.partner_b._get_eadu_partner()
        eadu_contact_c = partner_c._get_eadu_partner()
        mapping_a_to_b = self.env["eadu.partner.any"].sudo().search(
            [
                ("partner_id", "=", eadu_contact_b.id),
                ("res_model", "=", "discuss.channel"),
                ("res_id", "=", channel_a.id),
            ],
            limit=1,
        )
        mapping_a_to_c = self.env["eadu.partner.any"].sudo().search(
            [
                ("partner_id", "=", eadu_contact_c.id),
                ("res_model", "=", "discuss.channel"),
                ("res_id", "=", channel_a.id),
            ],
            limit=1,
        )
        self.assertTrue(mapping_a_to_b, "Missing A->B discuss.channel mapping")
        self.assertTrue(mapping_a_to_c, "Missing A->C discuss.channel mapping")
        self.assertEqual(mapping_a_to_b.master_status, "me")
        self.assertEqual(mapping_a_to_c.master_status, "me")

        channel_b = self.env["discuss.channel"].browse(mapping_a_to_b.eadu_ident)
        channel_c = self.env["discuss.channel"].browse(mapping_a_to_c.eadu_ident)
        self.assertTrue(channel_b in all_channels)
        self.assertTrue(channel_c in all_channels)

        mapping_b_to_a = self.env["eadu.partner.any"].sudo().search(
            [
                ("partner_id", "=", eadu_contact_b.id),
                ("res_model", "=", "discuss.channel"),
                ("res_id", "=", channel_b.id),
            ],
            limit=1,
        )
        mapping_c_to_a = self.env["eadu.partner.any"].sudo().search(
            [
                ("partner_id", "=", eadu_contact_c.id),
                ("res_model", "=", "discuss.channel"),
                ("res_id", "=", channel_c.id),
            ],
            limit=1,
        )
        import pdb; pdb.set_trace() 
        self.assertTrue(mapping_b_to_a, "Missing B->A discuss.channel mapping")
        self.assertTrue(mapping_c_to_a, "Missing C->A discuss.channel mapping")
        self.assertEqual(mapping_b_to_a.master_status, "partner")
        self.assertEqual(mapping_c_to_a.master_status, "partner")
        self.assertEqual(mapping_b_to_a.eadu_ident, channel_a.id)
        self.assertEqual(mapping_c_to_a.eadu_ident, channel_a.id)

        # Post from company C mirror and ensure fan-out to all channel copies.
        # In this mocked single-db setup, mirrored channels may only have EADU
        # portal users as effective senders. Prefer C's EADU user and fallback
        # to any channel member user if needed.
        company_c_sender = eadu_contact_c.user_ids.filtered(
            lambda u: u.has_group("eadu.group_portal_eadu")
        )[:1]
        if not company_c_sender:
            company_c_sender = channel_c.channel_partner_ids.user_ids[:1]
        self.assertTrue(
            company_c_sender,
            "Expected a sender user for the company C mirrored channel",
        )

        body_from_c = "Message sent from company C and relayed to A and B"
        channel_c.with_user(company_c_sender).message_post(body=body_from_c)

        for expected_channel in (channel_a, channel_b, channel_c):
            message = self.env["mail.message"].sudo().search(
                [
                    ("model", "=", "discuss.channel"),
                    ("res_id", "=", expected_channel.id),
                    ("body", "ilike", body_from_c),
                ],
                limit=1,
            )
            self.assertTrue(
                message,
                "Message posted in company C channel should appear in all three channels",
            )
