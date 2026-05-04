# Part of Eadu. See LICENSE file for full copyright and licensing details.
"""
Tests for multi-company Eadu exchange and message synchronisation.

Testing strategy
----------------
Cross-database RPC calls (``res.partner._eadu_call``) are intercepted by
``_local_cross_company_call_or_timeout``.  The mock resolves the portal API key
stored on the calling Eadu contact to a real ``res.users`` record in the *same*
database, so the "remote" call executes locally under that user's access rights.
This avoids any network dependency while exercising the full application code path.

To simulate a connection failure, add the Eadu contact's id to
``self._blocked_partners`` before the call.  The mock will then raise
``EaduConnectionError``, causing the queue logic to activate.

Test scenarios
--------------
- test_exchange_token_flow_via_multicompany
    Full A ↔ B handshake: token generation, contact and portal-user setup,
    discuss channel mirroring, message and attachment replication.

- test_exchange_token_flow_three_companies_discuss_triplication
    A ↔ B and A ↔ C exchanges.  One message from A fans out to both B and C.
    A message posted in C's mirrored channel is relayed back to A and B.

- test_queue_on_timeout_and_retry_two_companies
    B's connection is blocked; messages are queued.  After retry they arrive
    in send order and the queue is cleared.

- test_queue_on_timeout_three_companies_with_retry
    Same scenario for a 3-company channel: B receives messages immediately
    while C is blocked, then C catches up after retry with order preserved.

- test_exchange_idempotent
    Repeating the exchange does not create duplicate EADU contacts or users.

- test_rewrite_oe_links_remaps_known_ids
    ``_rewrite_oe_links`` replaces ``data-oe-id`` values with the remote
    counterpart when a mapping exists.

- test_malformed_exchange_token_raises
    A corrupt exchange token raises an error before any state is changed.
"""

import base64
from unittest.mock import patch

from odoo.tests import common, tagged

from odoo.addons.eadu.exceptions import EaduConnectionError
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

        self._blocked_partners = set()  # set of eadu_contact.id → raises EaduConnectionError
        self._eadu_call_patcher = patch.object(
            res_partner_model.ResPartner,
            "_eadu_call",
            autospec=True,
            side_effect=self._local_cross_company_call_or_timeout,
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

    def _local_cross_company_call_or_timeout(self, eadu_contact, model, method, params):
        """Like _local_cross_company_call but raises EaduConnectionError for blocked partners."""
        if eadu_contact.id in self._blocked_partners:
            raise EaduConnectionError("mocked connection failure")
        return self._local_cross_company_call(eadu_contact, model, method, params)

    # ── Fixture helpers ───────────────────────────────────────────────────────

    def _create_company_with_user(self, company_name, db_name, user_name, login):
        """Create a company + top-level partner + user triple.

        Returns ``(company, partner, user)``.
        """
        group_user = self.env.ref("base.group_user")
        group_partner_manager = self.env.ref("base.group_partner_manager")
        group_system = self.env.ref("base.group_system")
        company = self.ResCompany.create({"name": company_name})
        partner = self.ResPartner.create({
            "name": db_name,
            "company_type": "company",
            "company_id": company.id,
        })
        user = self.ResUsers.create({
            "name": user_name,
            "login": login,
            "email": f"{login}@example.com",
            "company_id": company.id,
            "company_ids": [(6, 0, [company.id])],
            "group_ids": [(6, 0, [group_user.id, group_partner_manager.id, group_system.id])],
        })
        return company, partner, user

    def _do_exchange(self, partner_a, user_a, partner_b, user_b):
        """Perform the Eadu exchange: A generates a token and B processes it."""
        token = partner_a.with_user(user_a)._generate_eadu_exchange()
        partner_b.with_user(user_b).write({"eadu_exchanged": token})
        partner_b.with_user(user_b).button_process_eadu_exchanged()

    # ── Exchange flow tests ───────────────────────────────────────────────────

    def test_exchange_token_flow_via_multicompany(self):
        """
        Full two-company handshake followed by channel creation, message
        replication and attachment sync in both directions.
        """
        # ── Step 1: A generates the exchange token ────────────────────────────
        exchange_token = self.partner_a.with_user(self.user_a)._generate_eadu_exchange()
        # Validate token format so test fails early with a clear signal.
        base64.b64decode(exchange_token.encode()).decode()

        eadu_contact_a = self.partner_a.child_ids.filtered(
            lambda p: p.name == "EADU" + self.partner_a.name and p.type == "other"
        )[:1]
        self.assertTrue(eadu_contact_a, "EADU contact should be created for company A")
        eadu_user_a = eadu_contact_a.user_ids.filtered(
            lambda u: u.has_group("eadu.group_portal_eadu")
        )[:1]
        self.assertTrue(eadu_user_a, "EADU portal user should exist for company A")

        # ── Step 2: B processes the token ─────────────────────────────────────
        partner_b_as_user_b = self.partner_b.with_user(self.user_b)
        partner_b_as_user_b.eadu_exchanged = exchange_token
        partner_b_as_user_b.button_process_eadu_exchanged()

        # ── Step 3: verify post-exchange state ────────────────────────────────
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
        self.assertTrue(mapping_a, "eadu.partner.any record should exist on A's side")
        self.assertTrue(mapping_b, "eadu.partner.any record should exist on B's side")

        # ── Step 4: create a channel and post a message ───────────────────────
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

        # ── Step 5: verify channel and message mirroring ──────────────────────
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

        # ── Step 6: reply from B with an attachment; verify sync back to A ────
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
        """
        Three-company scenario: A ↔ B and A ↔ C exchanges.  A message from A
        fans out to B and C; a reply from C is relayed back to A and B.
        """
        # ── Setup: add company C ──────────────────────────────────────────────
        company_c, partner_c, user_c = self._create_company_with_user(
            company_name="EADU Test Company C",
            db_name="DB C",
            user_name="Carol C",
            login="carol_c_eadu_test",
        )

        # ── Step 1: exchange A ↔ B and A ↔ C ─────────────────────────────────
        self._do_exchange(self.partner_a, self.user_a, self.partner_b, self.user_b)
        self._do_exchange(self.partner_a, self.user_a, partner_c, user_c)

        # ── Step 2: verify contacts were created on each side ─────────────────
        # Alice's contact should exist under both B and C after the exchanges.
        alice_contact_b = self.partner_b.child_ids.filtered(
            lambda p: p.name == self.user_a.name and p.type == "contact"
        )[:1]
        alice_contact_c = partner_c.child_ids.filtered(
            lambda p: p.name == self.user_a.name and p.type == "contact"
        )[:1]
        self.assertTrue(alice_contact_b, "Expected exchanged Alice contact under company B")
        self.assertTrue(alice_contact_c, "Expected exchanged Alice contact under company C")

        # ── Step 3: create a channel spanning all three companies ─────────────
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

        # ── Step 4: verify fan-out: 3 channel copies, 2 mirrored messages ─────
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
        self.assertTrue(channel_b in all_channels, "B's channel should be among the three")
        self.assertTrue(channel_c in all_channels, "C's channel should be among the three")

        # ── Step 5: reply from C and verify relay back to A and B ─────────────
        # Post from company C's mirror; the master (A) should relay it to B.
        # Prefer a regular internal user for the post so access rights are broad.
        company_c_sender = channel_c.channel_partner_ids.user_ids.filtered(
            lambda u: u.has_group("base.group_user")
        )[:1]

        if not company_c_sender:
            company_c_sender = channel_c.channel_partner_ids.user_ids[:1]
        self.assertTrue(
            company_c_sender,
            "Expected a sender user for the company C mirrored channel",
        )

        body_from_c = "Message sent from company C and relayed to A and B"
        attachment_name = "from-company-c.txt"
        attachment_from_c = self.env["ir.attachment"].with_user(company_c_sender).sudo().create(
            {
                "name": attachment_name,
                "datas": base64.b64encode(b"attachment payload from company C"),
                "mimetype": "text/plain",
            }
        )
        channel_c.with_user(company_c_sender).message_post(
            body=body_from_c,
            attachment_ids=[attachment_from_c.id],
        )

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
            self.assertTrue(
                message.attachment_ids,
                "Message posted in company C channel should keep its attachment in all three channels",
            )
            self.assertIn(
                attachment_name,
                message.attachment_ids.mapped("name"),
                "Replicated message should keep the attachment filename",
            )

    # ── Queue / retry tests ───────────────────────────────────────────────────

    def _do_ab_exchange(self):
        """Perform the A ↔ B exchange and return (alice_contact_a, bob_contact_a)."""
        self._do_exchange(self.partner_a, self.user_a, self.partner_b, self.user_b)
        alice_contact_a = self.user_a.partner_id
        bob_contact_a = self.partner_a.child_ids.filtered(
            lambda p: p.name == self.user_b.name and p.type == "contact"
        )[:1]
        return alice_contact_a, bob_contact_a

    def test_queue_on_timeout_and_retry_two_companies(self):
        """
        When B's connection times out, outgoing calls are queued on the
        eadu.partner.any records (with eadu_ident=0 as placeholders).  After
        the connection is restored, _retry_all_queued_calls() delivers the
        queued messages in the correct order.
        """
        alice_contact_a, bob_contact_a = self._do_ab_exchange()
        # The eadu contact A uses when calling B (child of partner_a pointing at B's URL).
        eadu_contact_a_to_b = self.partner_a._get_eadu_partner()

        channel_name = "Queue Retry Test Channel A-B"
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

        # Block B and post two messages.
        self._blocked_partners.add(eadu_contact_a_to_b.id)

        body1 = "First queued message"
        body2 = "Second queued message"
        channel_a.with_user(self.user_a).message_post(body=body1)
        channel_a.with_user(self.user_a).message_post(body=body2)

        # B should have pending_calls but no mirrored messages yet.
        pending = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_contact_a_to_b.id),
            ("pending_calls", "!=", False),
        ])
        self.assertTrue(pending, "Queued calls should be stored on eadu.partner.any records")

        mirrored_channels_while_blocked = self.env["discuss.channel"].sudo().search(
            [("name", "=", channel_name), ("id", "!=", channel_a.id)]
        )
        # The channel creation itself was also queued, so no mirrored channel yet.
        for ch in mirrored_channels_while_blocked:
            has_msg = self.env["mail.message"].sudo().search([
                ("model", "=", "discuss.channel"),
                ("res_id", "=", ch.id),
                ("body", "ilike", body1),
            ], limit=1)
            self.assertFalse(has_msg, "Messages should not have arrived at B while blocked")

        # Restore B and retry.
        self._blocked_partners.discard(eadu_contact_a_to_b.id)
        self.env["eadu.partner.any"].sudo()._retry_all_queued_calls()

        # All pending_calls should now be cleared.
        still_pending = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_contact_a_to_b.id),
            ("pending_calls", "!=", False),
        ])
        self.assertFalse(
            still_pending,
            "All queued calls should be sent after retry",
        )

        # Both messages should now appear in B's mirrored channel.
        mirrored_channels = self.env["discuss.channel"].sudo().search(
            [("name", "=", channel_name), ("id", "!=", channel_a.id)]
        )
        self.assertTrue(mirrored_channels, "Mirrored channel should exist in B after retry")
        mirrored_channel = mirrored_channels[0]

        for body in (body1, body2):
            msg = self.env["mail.message"].sudo().search(
                [
                    ("model", "=", "discuss.channel"),
                    ("res_id", "=", mirrored_channel.id),
                    ("body", "ilike", body),
                ],
                limit=1,
            )
            self.assertTrue(msg, f"Message '{body}' should arrive at B after retry")

        # Messages must arrive in the right order (body1 before body2).
        msg1 = self.env["mail.message"].sudo().search(
            [("res_id", "=", mirrored_channel.id), ("body", "ilike", body1)], limit=1
        )
        msg2 = self.env["mail.message"].sudo().search(
            [("res_id", "=", mirrored_channel.id), ("body", "ilike", body2)], limit=1
        )
        self.assertLess(msg1.id, msg2.id, "Messages should be replicated in send order")

    def test_queue_on_timeout_three_companies_with_retry(self):
        """
        In a 3-company channel (A, B, C), when C times out, messages are
        queued for C while B receives them normally.  After retry, C also
        receives all missing messages in order.
        """
        # ── Setup: add company C and exchange A↔B, A↔C ───────────────────────
        _company_c, partner_c, user_c = self._create_company_with_user(
            company_name="EADU Test Company C (retry)",
            db_name="DB C (retry)",
            user_name="Carol C (retry)",
            login="carol_c_retry_eadu_test",
        )
        self._do_exchange(self.partner_a, self.user_a, self.partner_b, self.user_b)
        self._do_exchange(self.partner_a, self.user_a, partner_c, user_c)

        alice_contact_b = self.partner_b.child_ids.filtered(
            lambda p: p.name == self.user_a.name and p.type == "contact"
        )[:1]
        alice_contact_c = partner_c.child_ids.filtered(
            lambda p: p.name == self.user_a.name and p.type == "contact"
        )[:1]
        eadu_contact_c = partner_c._get_eadu_partner()

        channel_name = "Three Company Retry Channel"
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

        # Block C and send two messages.
        self._blocked_partners.add(eadu_contact_c.id)

        body_queued_1 = "Message 1 queued for C"
        body_queued_2 = "Message 2 queued for C"
        channel_a.with_user(self.user_a).message_post(body=body_queued_1)
        channel_a.with_user(self.user_a).message_post(body=body_queued_2)

        # B should have received both messages, C should have pending_calls.
        all_channels = self.env["discuss.channel"].sudo().search([("name", "=", channel_name)])
        channel_b = all_channels.filtered(
            lambda ch: ch.id != channel_a.id and
            self.env["eadu.partner.any"].sudo().search([
                ("partner_id", "=", self.partner_b._get_eadu_partner().id),
                ("res_model", "=", "discuss.channel"),
                ("eadu_ident", "=", ch.id),
            ], limit=1)
        )[:1]

        if channel_b:
            for body in (body_queued_1, body_queued_2):
                msg_b = self.env["mail.message"].sudo().search(
                    [("res_id", "=", channel_b.id), ("body", "ilike", body)], limit=1
                )
                self.assertTrue(msg_b, f"B should have received '{body}' immediately")

        pending_c = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_contact_c.id),
            ("pending_calls", "!=", False),
        ])
        self.assertTrue(pending_c, "C should have pending queued calls while blocked")

        # Restore C and retry.
        self._blocked_partners.discard(eadu_contact_c.id)
        self.env["eadu.partner.any"].sudo()._retry_all_queued_calls()

        still_pending_c = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_contact_c.id),
            ("pending_calls", "!=", False),
        ])
        self.assertFalse(still_pending_c, "C's queue should be empty after retry")

        # C's mirrored channel should now have both messages.
        mapping_c = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_contact_c.id),
            ("res_model", "=", "discuss.channel"),
            ("res_id", "=", channel_a.id),
        ], limit=1)
        self.assertTrue(mapping_c, "discuss.channel mapping to C should exist after retry")
        channel_c = self.env["discuss.channel"].sudo().browse(mapping_c.eadu_ident)

        for body in (body_queued_1, body_queued_2):
            msg_c = self.env["mail.message"].sudo().search(
                [
                    ("model", "=", "discuss.channel"),
                    ("res_id", "=", channel_c.id),
                    ("body", "ilike", body),
                ],
                limit=1,
            )
            self.assertTrue(
                msg_c,
                f"Message '{body}' should have arrived at C after retry",
            )

        # Order must be preserved.
        msg_c_1 = self.env["mail.message"].sudo().search(
            [("res_id", "=", channel_c.id), ("body", "ilike", body_queued_1)], limit=1
        )
        msg_c_2 = self.env["mail.message"].sudo().search(
            [("res_id", "=", channel_c.id), ("body", "ilike", body_queued_2)], limit=1
        )
        self.assertLess(msg_c_1.id, msg_c_2.id, "Messages should arrive at C in send order")

    # ── Additional tests ──────────────────────────────────────────────────────

    def test_exchange_idempotent(self):
        """
        Performing the exchange a second time must not create duplicate EADU
        child contacts or portal users on either side.

        The first exchange creates one EADU child contact per partner.
        The second exchange should reuse those existing records rather than
        creating new ones.
        """
        # First exchange.
        self._do_exchange(self.partner_a, self.user_a, self.partner_b, self.user_b)

        eadu_children_a_before = self.partner_a.child_ids.filtered(lambda p: p.eadu_url)
        eadu_children_b_before = self.partner_b.child_ids.filtered(lambda p: p.eadu_url)
        self.assertEqual(len(eadu_children_a_before), 1,
                         "Exactly one EADU child contact should exist on A after first exchange")
        self.assertEqual(len(eadu_children_b_before), 1,
                         "Exactly one EADU child contact should exist on B after first exchange")

        # Second exchange (A generates a fresh token, B processes it again).
        self._do_exchange(self.partner_a, self.user_a, self.partner_b, self.user_b)

        eadu_children_a_after = self.partner_a.child_ids.filtered(lambda p: p.eadu_url)
        eadu_children_b_after = self.partner_b.child_ids.filtered(lambda p: p.eadu_url)
        self.assertEqual(
            len(eadu_children_a_after), 1,
            "Second exchange must not create a second EADU child contact on A",
        )
        self.assertEqual(
            len(eadu_children_b_after), 1,
            "Second exchange must not create a second EADU child contact on B",
        )

        # Each EADU child contact must have exactly one portal user.
        portal_users_a = eadu_children_a_after.user_ids.filtered(
            lambda u: u.has_group("eadu.group_portal_eadu")
        )
        portal_users_b = eadu_children_b_after.user_ids.filtered(
            lambda u: u.has_group("eadu.group_portal_eadu")
        )
        self.assertEqual(len(portal_users_a), 1,
                         "A's EADU contact must have exactly one portal user after two exchanges")
        self.assertEqual(len(portal_users_b), 1,
                         "B's EADU contact must have exactly one portal user after two exchanges")

    def test_rewrite_oe_links_remaps_known_ids(self):
        """
        After an exchange, ``_rewrite_oe_links`` replaces ``data-oe-id``
        attribute values in message bodies with the remote counterpart ids
        recorded in ``eadu.partner.any``.
        """
        self._do_exchange(self.partner_a, self.user_a, self.partner_b, self.user_b)

        # Find the eadu.partner.any record for user_a's partner on B's side.
        eadu_contact_b = self.partner_a._get_eadu_partner()
        mapping = self.env["eadu.partner.any"].sudo().search([
            ("partner_id", "=", eadu_contact_b.id),
            ("res_model", "=", "res.partner"),
            ("res_id", "=", self.user_a.partner_id.id),
        ], limit=1)
        self.assertTrue(
            mapping,
            "Exchange should have created a res.partner mapping for user A on B's eadu contact",
        )
        self.assertTrue(
            mapping.eadu_ident,
            "Mapping must have a non-zero eadu_ident before testing link rewriting",
        )

        local_id = self.user_a.partner_id.id
        remote_id = mapping.eadu_ident

        # Build a minimal chatter body referencing user_a's partner by local id.
        body = (
            f'<a data-oe-model="res.partner" data-oe-id="{local_id}">'
            f'Alice</a>'
        )

        rewritten = self.env["mail.message"].sudo()._rewrite_oe_links(body, eadu_contact_b)

        self.assertIn(
            f'data-oe-id="{remote_id}"',
            rewritten,
            "_rewrite_oe_links should replace the local id with the remote eadu_ident",
        )
        self.assertNotIn(
            f'data-oe-id="{local_id}"',
            rewritten,
            "_rewrite_oe_links should not leave the original local id in the rewritten body",
        )

    def test_malformed_exchange_token_raises(self):
        """
        Processing a corrupt or incomplete exchange token must raise an
        exception before any partner records or users are created.
        """
        malformed_tokens = [
            "not-valid-base64!!!",
            base64.b64encode(b"too-few-fields").decode(),  # valid base64 but missing # segments
            "",
        ]
        for token in malformed_tokens:
            self.partner_b.eadu_exchanged = token
            with self.assertRaises(Exception,
                                   msg=f"Should raise for malformed token: {token!r}"):
                self.partner_b.with_user(self.user_b).button_process_eadu_exchanged()

