# Part of Eadu. See LICENSE file for full copyright and licensing details.

from unittest.mock import patch

from odoo.tests import common, tagged

from odoo.addons.eadu.exceptions import EaduConnectionError
from odoo.addons.eadu.models import res_partner as res_partner_model


@tagged("post_install", "-at_install")
class TestEaduProduct(common.TransactionCase):

    def setUp(self):
        super().setUp()
        self.ResCompany = self.env["res.company"].sudo()
        self.ResPartner = self.env["res.partner"].sudo()
        self.ResUsers = self.env["res.users"].sudo()

        self.company_a = self.ResCompany.create({"name": "EADU Prod Test Company A"})
        self.company_b = self.ResCompany.create({"name": "EADU Prod Test Company B"})

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

        self._blocked_partners = set()
        self._eadu_call_patcher = patch.object(
            res_partner_model.ResPartner,
            "_eadu_call",
            autospec=True,
            side_effect=self._local_cross_company_call_or_timeout,
        )
        self._eadu_call_patcher.start()
        self.addCleanup(self._eadu_call_patcher.stop)

        self._do_exchange()

    def _get_eadu_portal_user_from_apikey(self, eadu_contact):
        user_id = self.env["res.users.apikeys"].sudo()._check_credentials(
            scope="rpc", key=eadu_contact.eadu_apikey,
        )
        self.assertTrue(user_id, "No user found for eadu_apikey")
        eadu_user = self.ResUsers.browse(user_id)
        self.assertTrue(eadu_user.has_group("eadu.group_portal_eadu"))
        return eadu_user

    def _local_cross_company_call(self, eadu_contact, model, method, params):
        remote_user = self._get_eadu_portal_user_from_apikey(eadu_contact)
        remote_model = self.env[model].with_user(remote_user)
        return getattr(remote_model, method)(**params)

    def _local_cross_company_call_or_timeout(self, eadu_contact, model, method, params):
        if eadu_contact.id in self._blocked_partners:
            raise EaduConnectionError("mocked connection failure")
        return self._local_cross_company_call(eadu_contact, model, method, params)

    def _do_exchange(self):
        exchange_token = self.partner_a.with_user(self.user_a)._generate_eadu_exchange()
        partner_b_as_b = self.partner_b.with_user(self.user_b)
        partner_b_as_b.eadu_exchanged = exchange_token
        partner_b_as_b.button_process_eadu_exchanged()

    def _create_product_in_b(self, name, barcode=None, default_code=None, list_price=0.0):
        return self.env['product.product'].sudo().create({
            'name': name,
            'barcode': barcode,
            'default_code': default_code,
            'list_price': list_price,
            'company_id': self.company_b.id,
        })

    def _push_product_to_a(self, product_b, **overrides):
        eadu_child_b = self.partner_b._get_eadu_partner()
        self.assertTrue(eadu_child_b, "B must have an eadu contact after exchange")
        params = {
            'eadu_ident': product_b.id,
            'name': product_b.name,
            'barcode': product_b.barcode or None,
            'default_code': product_b.default_code or None,
            'sale_price': product_b.lst_price,
            'currency_id': product_b.currency_id.id if product_b.currency_id else None,
        }
        params.update(overrides)
        result = eadu_child_b._eadu_call(
            'product.product',
            'action_eadu_import_product',
            params,
        )
        product_a = self.env['product.product'].sudo().with_context(active_test=False).browse(result['result'])
        self.assertTrue(product_a.exists(), "Native product.product should be returned")
        return product_a

    def _assert_product_mapping(self, product_a, product_b, state='partner'):
        eadu_child_a = self.partner_a._get_eadu_partner()
        mapping = self.env['eadu.partner.any'].sudo().search([
            ('partner_id', '=', eadu_child_a.id),
            ('res_model', '=', 'product.product'),
            ('res_id', '=', product_a.id),
            ('eadu_ident', '=', product_b.id),
        ])
        self.assertTrue(mapping, "A-side product mapping should exist")
        self.assertEqual(mapping.master_status, state)

    def _assert_remote_mapping(self, product_a, product_b):
        eadu_child_b = self.partner_b._get_eadu_partner()
        mapping = self.env['eadu.partner.any'].sudo().search([
            ('partner_id', '=', eadu_child_b.id),
            ('res_model', '=', 'product.product'),
            ('res_id', '=', product_b.id),
            ('eadu_ident', '=', product_a.id),
        ])
        self.assertTrue(mapping, "B-side symmetric product mapping should exist")
        self.assertEqual(mapping.master_status, 'me')

    def _supplierinfo_for(self, product_a):
        eadu_child_a = self.partner_a._get_eadu_partner()
        return self.env['product.supplierinfo'].sudo().search([
            ('product_tmpl_id', '=', product_a.product_tmpl_id.id),
            ('product_id', '=', product_a.id),
            ('partner_id', '=', eadu_child_a.commercial_partner_id.id),
        ])

    def test_import_creates_inactive_native_product_when_no_match(self):
        product_b = self._create_product_in_b(
            name="Remote Widget",
            barcode="4006381333931",
            default_code="WGT-001",
            list_price=12.50,
        )

        product_a = self._push_product_to_a(product_b)

        self.assertEqual(product_a.name, "Remote Widget")
        self.assertFalse(product_a.active)
        self.assertTrue(product_a.eadu_catalog)
        self.assertEqual(product_a.eadu_merge_state, 'needs_review')
        self.assertEqual(product_a.eadu_origin_ident, product_b.id)
        self.assertEqual(product_a.eadu_remote_barcode, "4006381333931")
        self.assertIn('EADU catalog', product_a.all_product_tag_ids.mapped('name'))
        self._assert_product_mapping(product_a, product_b)
        self._assert_remote_mapping(product_a, product_b)

    def test_import_auto_links_single_local_barcode_match(self):
        local_a = self.env['product.product'].sudo().create({
            'name': "Local Widget",
            'barcode': "0000000000001",
            'company_id': self.company_a.id,
        })
        product_b = self._create_product_in_b(
            name="Remote Widget",
            barcode="0000000000001",
            list_price=8.75,
        )

        product_a = self._push_product_to_a(product_b)

        self.assertEqual(product_a, local_a)
        self.assertTrue(product_a.active)
        self.assertTrue(product_a.eadu_catalog)
        self.assertEqual(product_a.eadu_merge_state, 'auto_linked')
        self._assert_product_mapping(product_a, product_b)
        self._assert_remote_mapping(product_a, product_b)

    def test_import_deduplicates_by_eadu_mapping(self):
        product_b = self._create_product_in_b(name="Duplicate Widget", list_price=5.0)

        product_a = self._push_product_to_a(product_b)
        product_a_2 = self._push_product_to_a(product_b, name="Duplicate Widget Updated", sale_price=6.0)

        self.assertEqual(product_a_2, product_a)
        mappings = self.env['eadu.partner.any'].sudo().search([
            ('res_model', '=', 'product.product'),
            ('res_id', '=', product_a.id),
            ('eadu_ident', '=', product_b.id),
        ])
        self.assertEqual(len(mappings), 1)
        self.assertEqual(self._supplierinfo_for(product_a).price, 6.0)

    def test_publisher_sale_price_becomes_supplierinfo_price(self):
        product_b = self._create_product_in_b(
            name="Priced Widget",
            barcode="1234567890123",
            list_price=19.99,
        )

        product_a = self._push_product_to_a(product_b)
        supplierinfo = self._supplierinfo_for(product_a)

        self.assertTrue(supplierinfo)
        self.assertEqual(supplierinfo.price, 19.99)
        self.assertEqual(supplierinfo.product_name, "Priced Widget")
        self.assertFalse(product_a.active)

    def test_tiered_sale_prices_become_supplierinfo_quantity_breaks(self):
        product_b = self._create_product_in_b(name="Tiered Widget")

        product_a = self._push_product_to_a(product_b, tiered_prices=[
            {'min_qty': 0.0, 'price': 10.0},
            {'min_qty': 10.0, 'price': 8.5},
            {'min_qty': 50.0, 'price': 7.0},
        ])

        prices_by_qty = {
            seller.min_qty: seller.price
            for seller in self._supplierinfo_for(product_a)
        }
        self.assertEqual(prices_by_qty[0.0], 10.0)
        self.assertEqual(prices_by_qty[10.0], 8.5)
        self.assertEqual(prices_by_qty[50.0], 7.0)

    def test_remote_attributes_block_auto_barcode_merge_until_mapping_exists(self):
        local_a = self.env['product.product'].sudo().create({
            'name': "Local Shirt Blue",
            'barcode': "2222222222222",
            'company_id': self.company_a.id,
        })
        product_b = self._create_product_in_b(
            name="Remote Shirt Blue",
            barcode="2222222222222",
            list_price=15.0,
        )

        product_a = self._push_product_to_a(product_b, attributes=[
            {'name': 'Colour', 'value': 'Blue'},
        ])

        self.assertNotEqual(product_a, local_a)
        self.assertFalse(product_a.active)
        self.assertFalse(product_a.barcode, "Unreviewed duplicate barcode must not be written locally")
        self.assertEqual(product_a.eadu_remote_barcode, "2222222222222")
        self.assertEqual(product_a.eadu_merge_state, 'needs_review')
        self.assertIn('attribute mapping', product_a.eadu_review_reason)
        self._assert_product_mapping(product_a, product_b)

    def test_merge_wizard_moves_mapping_and_supplierinfo_to_target_product(self):
        target_a = self.env['product.product'].sudo().create({
            'name': "Accepted Widget",
            'company_id': self.company_a.id,
        })
        product_b = self._create_product_in_b(
            name="Remote Widget",
            barcode="3333333333333",
            list_price=21.0,
        )
        source_a = self._push_product_to_a(product_b)

        wizard = self.env['eadu.product.merge.wizard'].with_user(self.user_a).create({
            'source_product_id': source_a.id,
            'target_product_id': target_a.id,
        })
        wizard.action_merge()

        self.assertEqual(target_a.barcode, "3333333333333")
        self.assertEqual(target_a.eadu_merge_state, 'accepted')
        self.assertEqual(source_a.eadu_merge_state, 'ignored')
        self._assert_product_mapping(target_a, product_b)
        self.assertFalse(self.env['eadu.partner.any'].sudo().search([
            ('res_model', '=', 'product.product'),
            ('res_id', '=', source_a.id),
            ('eadu_ident', '=', product_b.id),
        ]))
        supplierinfo = self._supplierinfo_for(target_a)
        self.assertTrue(supplierinfo)
        self.assertEqual(supplierinfo.price, 21.0)
