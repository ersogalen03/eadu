# Part of Eadu. See LICENSE file for full copyright and licensing details.
"""
Tests for the eadu_product module.

Two companies (A and B) simulate two separate Odoo instances connected via Eadu.
The test shares the multicompany mock infrastructure from the base eadu module:
_eadu_call is patched so that cross-company RPC calls are routed locally using
the portal API key stored on the eadu contact.

Test scenarios
--------------
1. test_create_product
   B pushes a product to A (via action_eadu_import_product).
   A creates a new product with action_create_product.
   Verified: product.supplierinfo, eadu.partner.any on BOTH sides, state=linked.

2. test_link_existing_product
   B pushes a product to A.
   A already has a matching local product; A sets product_id and calls action_link_product.
   Verified: same set of records as above.
"""

from unittest.mock import patch

from odoo.tests import common, tagged

from odoo.addons.eadu.exceptions import EaduConnectionError
from odoo.addons.eadu.models import res_partner as res_partner_model


@tagged("post_install", "-at_install")
class TestEaduProduct(common.TransactionCase):
    # ── Test fixtures ─────────────────────────────────────────────────────

    def setUp(self):
        super().setUp()
        self.ResCompany = self.env["res.company"].sudo()
        self.ResPartner = self.env["res.partner"].sudo()
        self.ResUsers = self.env["res.users"].sudo()

        # Two companies representing two independent Odoo instances.
        self.company_a = self.ResCompany.create({"name": "EADU Prod Test Company A"})
        self.company_b = self.ResCompany.create({"name": "EADU Prod Test Company B"})

        # One top-level partner per company that represents the remote database.
        self.partner_a = self.ResPartner.create({
            "name": "DB-A",
            "company_type": "company",
            "company_id": self.company_a.id,
        })
        self.partner_b = self.ResPartner.create({
            "name": "DB-B",
            "company_type": "company",
            "company_id": self.company_b.id,
        })

        group_user = self.env.ref("base.group_user")
        group_pm = self.env.ref("base.group_partner_manager")
        group_sys = self.env.ref("base.group_system")

        self.user_a = self.ResUsers.create({
            "name": "Alice A",
            "login": "alice_a_eadup_test",
            "email": "alice_a_eadup@example.com",
            "company_id": self.company_a.id,
            "company_ids": [(6, 0, [self.company_a.id])],
            "group_ids": [(6, 0, [group_user.id, group_pm.id, group_sys.id])],
        })
        self.user_b = self.ResUsers.create({
            "name": "Bob B",
            "login": "bob_b_eadup_test",
            "email": "bob_b_eadup@example.com",
            "company_id": self.company_b.id,
            "company_ids": [(6, 0, [self.company_b.id])],
            "group_ids": [(6, 0, [group_user.id, group_pm.id, group_sys.id])],
        })

        # Patch _eadu_call to route calls locally via the portal API key.
        self._blocked_partners = set()
        self._eadu_call_patcher = patch.object(
            res_partner_model.ResPartner,
            "_eadu_call",
            autospec=True,
            side_effect=self._local_cross_company_call_or_timeout,
        )
        self._eadu_call_patcher.start()
        self.addCleanup(self._eadu_call_patcher.stop)

        # Perform the Eadu exchange so both sides have portal users + API keys.
        self._do_exchange()

    # ── Mock helpers ──────────────────────────────────────────────────────

    def _get_eadu_portal_user_from_apikey(self, eadu_contact):
        if not eadu_contact.eadu_apikey:
            raise AssertionError("Missing eadu_apikey on eadu contact")
        user_id = self.env["res.users.apikeys"].sudo()._check_credentials(
            scope="rpc", key=eadu_contact.eadu_apikey,
        )
        if not user_id:
            raise AssertionError("No user found for eadu_apikey")
        eadu_user = self.ResUsers.browse(user_id)
        if not eadu_user.has_group("eadu.group_portal_eadu"):
            raise AssertionError("Resolved user is not in eadu.group_portal_eadu")
        return eadu_user

    def _local_cross_company_call(self, eadu_contact, model, method, params):
        remote_user = self._get_eadu_portal_user_from_apikey(eadu_contact)
        remote_model = self.env[model].with_user(remote_user)
        return getattr(remote_model, method)(**params)

    def _local_cross_company_call_or_timeout(self, eadu_contact, model, method, params):
        if eadu_contact.id in self._blocked_partners:
            raise EaduConnectionError("mocked connection failure")
        return self._local_cross_company_call(eadu_contact, model, method, params)

    # ── Exchange helper ───────────────────────────────────────────────────

    def _do_exchange(self):
        """Perform the Eadu exchange so both companies are fully connected."""
        exchange_token = self.partner_a.with_user(self.user_a)._generate_eadu_exchange()
        partner_b_as_b = self.partner_b.with_user(self.user_b)
        partner_b_as_b.eadu_exchanged = exchange_token
        partner_b_as_b.button_process_eadu_exchanged()

    # ── Product push helper ───────────────────────────────────────────────

    def _create_product_in_b(self, name, barcode=None, default_code=None,
                              standard_price=0.0):
        """
        Create a product in company B and push it to company A via the
        action_eadu_import_product endpoint, returning (eadu_product_a, product_b).
        """
        product_b = self.env['product.product'].sudo().create({
            'name': name,
            'barcode': barcode,
            'default_code': default_code,
            'standard_price': standard_price,
            'company_id': self.company_b.id,
        })

        # B's eadu contact (the child of partner_b that has eadu_url pointing to A).
        eadu_child_b = self.partner_b._get_eadu_partner()
        self.assertTrue(eadu_child_b, "B must have an eadu contact after exchange")

        # B pushes the product to A.  The mock routes this as A's portal user for B,
        # so action_eadu_import_product will store partner_id = A's eadu child.
        result = eadu_child_b._eadu_call(
            'eadu.product',
            'action_eadu_import_product',
            {
                'eadu_ident': product_b.id,
                'name': name,
                'barcode': barcode,
                'default_code': default_code,
                'standard_price': standard_price,
            },
        )
        eadu_product_a = self.env['eadu.product'].sudo().browse(result['result'])
        self.assertTrue(eadu_product_a.exists(), "eadu.product should have been created on A's side")
        return eadu_product_a, product_b

    # ── Shared assertion helper ───────────────────────────────────────────

    def _assert_linked(self, eadu_product_a, product_b, local_product):
        """
        Assert all expected records exist on both sides after a successful link.

        eadu_product_a  : the eadu.product record on A's side
        product_b       : the product.product in company B
        local_product   : the product.product created/linked in company A
        """
        EaduAny = self.env['eadu.partner.any'].sudo()
        eadu_child_a = self.partner_a._get_eadu_partner()
        eadu_child_b = self.partner_b._get_eadu_partner()

        # eadu.product state
        self.assertEqual(eadu_product_a.state, 'linked', "eadu.product should be linked")
        self.assertEqual(eadu_product_a.product_id, local_product,
                         "eadu.product.product_id should point to the local product")

        # product.supplierinfo on A's side
        supplier_info = self.env['product.supplierinfo'].sudo().search([
            ('product_tmpl_id', '=', local_product.product_tmpl_id.id),
            ('partner_id', '=', eadu_child_a.commercial_partner_id.id),
        ])
        self.assertTrue(supplier_info,
                        "product.supplierinfo should exist for the linked product on A's side")

        # eadu.partner.any on A's side
        mapping_a = EaduAny.search([
            ('partner_id', '=', eadu_child_a.id),
            ('res_model', '=', 'product.product'),
            ('res_id', '=', local_product.id),
            ('eadu_ident', '=', product_b.id),
        ])
        self.assertTrue(mapping_a, "eadu.partner.any should exist on A's side")
        self.assertEqual(mapping_a.master_status, 'partner',
                         "B is master on A's side (A imported from B)")

        # eadu.partner.any on B's side
        mapping_b = EaduAny.search([
            ('partner_id', '=', eadu_child_b.id),
            ('res_model', '=', 'product.product'),
            ('res_id', '=', product_b.id),
            ('eadu_ident', '=', local_product.id),
        ])
        self.assertTrue(mapping_b, "eadu.partner.any should exist on B's side")
        self.assertEqual(mapping_b.master_status, 'me',
                         "B is master on B's side (B originally had the product)")

    # ── Tests ────────────────────────────────────────────────────────────

    def test_create_product(self):
        """
        A receives an imported product from B and creates a brand-new local
        product.product via action_create_product.
        """
        eadu_product_a, product_b = self._create_product_in_b(
            name="Widget",
            barcode="4006381333931",
            default_code="WGT-001",
            standard_price=12.50,
        )

        # Verify the imported data was captured correctly.
        self.assertEqual(eadu_product_a.name, "Widget")
        self.assertEqual(eadu_product_a.barcode, "4006381333931")
        self.assertEqual(eadu_product_a.state, 'pending')

        # A creates a new local product from the imported data.
        eadu_product_a.with_user(self.user_a).action_create_product()

        local_product = eadu_product_a.product_id
        self.assertTrue(local_product.exists(),
                        "A new product.product should have been created")
        self.assertEqual(local_product.name, "Widget")
        self.assertEqual(local_product.barcode, "4006381333931")

        self._assert_linked(eadu_product_a, product_b, local_product)

    def test_link_existing_product(self):
        """
        A receives an imported product from B and links it to an existing local
        product.product via action_link_product.
        """
        # A already has a local product (e.g. from a previous catalogue import).
        existing_product = self.env['product.product'].sudo().create({
            'name': "Gadget",
            'default_code': "GDG-001",
            'company_id': self.company_a.id,
        })

        eadu_product_a, product_b = self._create_product_in_b(
            name="Gadget",
            default_code="GDG-001",
            standard_price=8.00,
        )

        self.assertEqual(eadu_product_a.state, 'pending')

        # A's user sets product_id to the existing product and triggers the link.
        eadu_product_a.sudo().product_id = existing_product
        eadu_product_a.with_user(self.user_a).action_link_product()

        self._assert_linked(eadu_product_a, product_b, existing_product)

    def test_import_deduplication(self):
        """
        Calling action_eadu_import_product twice for the same remote product
        must not create a duplicate eadu.product record.
        """
        eadu_product_a, product_b = self._create_product_in_b(name="Duplicate Widget")

        eadu_child_b = self.partner_b._get_eadu_partner()
        result2 = eadu_child_b._eadu_call(
            'eadu.product',
            'action_eadu_import_product',
            {
                'eadu_ident': product_b.id,
                'name': "Duplicate Widget",
            },
        )
        self.assertEqual(
            result2['result'], eadu_product_a.id,
            "Second push should return the existing eadu.product id",
        )
        count = self.env['eadu.product'].sudo().search_count([
            ('eadu_ident', '=', product_b.id),
        ])
        self.assertEqual(count, 1, "Only one eadu.product record should exist")

    def test_suggested_product_by_barcode(self):
        """
        The suggested_product_id computation should prefer barcode over name.
        """
        local = self.env['product.product'].sudo().create({
            'name': "Some Other Name",
            'barcode': "0000000000001",
            'company_id': self.company_a.id,
        })
        eadu_product_a, _ = self._create_product_in_b(
            name="Different Name",
            barcode="0000000000001",
        )
        self.assertEqual(
            eadu_product_a.with_user(self.user_a).suggested_product_id, local,
            "Barcode match should be used for suggestion",
        )

    def test_suggested_product_by_name(self):
        """
        When no barcode is available, a name match is used as suggestion.
        """
        local = self.env['product.product'].sudo().create({
            'name': "Named Product",
            'company_id': self.company_a.id,
        })
        eadu_product_a, _ = self._create_product_in_b(name="Named Product")
        self.assertEqual(
            eadu_product_a.with_user(self.user_a).suggested_product_id, local,
            "Name match should be used for suggestion when no barcode",
        )
