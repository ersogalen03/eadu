# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


class ProductProduct(models.Model):
    _inherit = 'product.product'

    eadu_catalog = fields.Boolean(
        string="Eadu Catalog",
        index=True,
        help="Product was imported from an Eadu partner catalog.",
    )
    eadu_merge_state = fields.Selection(
        [
            ('native', 'Native'),
            ('auto_linked', 'Auto-linked'),
            ('needs_review', 'Needs Review'),
            ('accepted', 'Accepted'),
            ('ignored', 'Ignored'),
        ],
        string="Eadu Merge State",
        default='native',
        index=True,
    )
    eadu_review_reason = fields.Text(string="Eadu Review Reason")
    eadu_origin_partner_id = fields.Many2one(
        'res.partner',
        string="Eadu Origin",
        index=True,
        help="Eadu contact that published this catalog product.",
    )
    eadu_origin_ident = fields.Integer(
        string="Remote Product ID",
        index=True,
        help="product.product ID in the publishing Eadu database.",
    )
    eadu_remote_barcode = fields.Char(
        string="Remote Barcode",
        index=True,
        help="Publisher barcode. Kept separate when it cannot safely become the local barcode.",
    )
    eadu_remote_sale_price = fields.Float(
        string="Remote Sale Price",
        help="Publisher's sale price, stored as context for vendor pricing.",
    )

    def _eadu_check_portal(self):
        if not self.env.user.has_group('eadu.group_portal_eadu'):
            raise UserError(_("Not authorized"))

    def _eadu_catalog_tag(self):
        tag = self.env['product.tag'].sudo().search([('name', '=', 'EADU catalog')], limit=1)
        if not tag:
            tag = self.env['product.tag'].sudo().create({
                'name': 'EADU catalog',
                'visible_to_customers': False,
                'color': '#6B7280',
            })
        return tag

    def _eadu_existing_mapping(self, eadu_contact, eadu_ident):
        return self.env['eadu.partner.any'].sudo().search([
            ('partner_id', '=', eadu_contact.id),
            ('res_model', '=', 'product.product'),
            ('eadu_ident', '=', eadu_ident),
        ], limit=1)

    def _eadu_find_barcode_match(self, barcode):
        if not barcode:
            return self.env['product.product']
        company = self.env.company
        return self.env['product.product'].sudo().with_context(active_test=False).search([
            ('barcode', '=', barcode),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', company.id),
        ])

    def _eadu_remote_price(self, sale_price=None, standard_price=0.0, list_price=None):
        """Temporary compatibility: older callers sent standard_price as the offer price."""
        if sale_price is not None:
            return sale_price
        if list_price is not None:
            return list_price
        return standard_price or 0.0

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
        return [
            'name', 'barcode', 'default_code', 'list_price', 'lst_price',
            'image_1920', 'image_variant_1920',
        ]

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
                'product.product',
                'action_eadu_update',
                {
                    'eadu_ident': eadu_any.res_id,
                    'fields': vals,
                },
                eadu_any_ref=eadu_any,
            )

    def _eadu_prepare_product_review(self, barcode=None, attributes=None):
        reasons = []
        barcode_matches = self._eadu_find_barcode_match(barcode)
        if barcode and len(barcode_matches) > 1:
            reasons.append(_("Barcode %(barcode)s matches multiple local variants.", barcode=barcode))
        if attributes:
            reasons.append(_("Remote attributes were received but attribute mapping is not resolved yet."))
        return reasons

    def _eadu_create_catalog_product(
        self, eadu_contact, eadu_ident, name, barcode=None, default_code=None,
        remote_sale_price=0.0, attributes=None,
    ):
        tag = self._eadu_catalog_tag()
        reasons = self._eadu_prepare_product_review(barcode=barcode, attributes=attributes)
        barcode_matches = self._eadu_find_barcode_match(barcode)
        safe_barcode = barcode if barcode and not barcode_matches else False

        tmpl = self.env['product.template'].sudo().create({
            'name': name,
            'active': False,
            'company_id': (eadu_contact.company_id or self.env.company).id,
            'sale_ok': False,
            'purchase_ok': True,
            'product_tag_ids': [Command.link(tag.id)],
        })
        product = tmpl.with_context(active_test=False).product_variant_ids[:1]
        product.sudo().write({
            'active': False,
            'barcode': safe_barcode,
            'default_code': default_code or False,
            'eadu_catalog': True,
            'eadu_merge_state': 'needs_review',
            'eadu_review_reason': "\n".join(reasons) or _("Imported from Eadu catalog; needs acceptance or merge."),
            'eadu_origin_partner_id': eadu_contact.id,
            'eadu_origin_ident': eadu_ident,
            'eadu_remote_barcode': barcode or False,
            'eadu_remote_sale_price': remote_sale_price,
        })
        return product

    def _eadu_upsert_supplierinfo(
        self, product, eadu_contact, name, default_code=None, price=0.0,
        currency_id=None, min_qty=0.0, tiered_prices=None,
    ):
        vendor = eadu_contact.commercial_partner_id
        SupplierInfo = self.env['product.supplierinfo'].sudo()

        def upsert_tier(qty, tier_price):
            domain = [
                ('partner_id', '=', vendor.id),
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ('product_id', '=', product.id),
                ('min_qty', '=', qty or 0.0),
            ]
            vals = {
                'partner_id': vendor.id,
                'product_tmpl_id': product.product_tmpl_id.id,
                'product_id': product.id,
                'product_name': name,
                'product_code': default_code or False,
                'price': tier_price or 0.0,
                'min_qty': qty or 0.0,
                'company_id': self.env.company.id,
            }
            if currency_id:
                vals['currency_id'] = currency_id
            seller = SupplierInfo.search(domain, limit=1)
            if seller:
                seller.write(vals)
            else:
                SupplierInfo.create(vals)

        if tiered_prices:
            for tier in tiered_prices:
                upsert_tier(tier.get('min_qty', 0.0), tier.get('price', 0.0))
        else:
            upsert_tier(min_qty, price)

    def _eadu_notify_remote_product_link(self, eadu_contact, remote_product_id, local_product):
        EaduAny = self.env['eadu.partner.any'].sudo()
        eadu_any = EaduAny._search_create_for_eadu_partner(
            eadu_contact,
            'product.product',
            local_product.id,
            remote_product_id,
            partner_master=True,
        )
        EaduAny._send_or_queue(
            eadu_contact,
            'product.product',
            'action_eadu_create_product_link',
            {
                'eadu_ident': remote_product_id,
                'partner_product_id': local_product.id,
            },
            eadu_any_ref=eadu_any,
        )
        return eadu_any

    def action_eadu_import_product(
        self, eadu_ident, name, barcode=None, default_code=None,
        standard_price=0.0, sale_price=None, list_price=None, currency_id=None,
        min_qty=0.0, tiered_prices=None, attributes=None, fields=None,
    ):
        """Import a remote product directly as/mapped to native product.product."""
        self._eadu_check_portal()
        eadu_contact = self.env.user.partner_id
        remote_sale_price = self._eadu_remote_price(
            sale_price=sale_price,
            standard_price=standard_price,
            list_price=list_price,
        )

        mapping = self._eadu_existing_mapping(eadu_contact, eadu_ident)
        if mapping:
            product = self.sudo().with_context(active_test=False).browse(mapping.res_id)
        else:
            barcode_matches = self._eadu_find_barcode_match(barcode)
            if barcode and len(barcode_matches) == 1 and not attributes:
                product = barcode_matches
                product.sudo().with_context(eadu_message=True).write({
                    'eadu_catalog': True,
                    'eadu_merge_state': 'auto_linked',
                    'eadu_review_reason': False,
                    'eadu_origin_partner_id': eadu_contact.id,
                    'eadu_origin_ident': eadu_ident,
                    'eadu_remote_barcode': barcode,
                    'eadu_remote_sale_price': remote_sale_price,
                })
            else:
                product = self._eadu_create_catalog_product(
                    eadu_contact,
                    eadu_ident,
                    name,
                    barcode=barcode,
                    default_code=default_code,
                    remote_sale_price=remote_sale_price,
                    attributes=attributes,
                )
            self._eadu_notify_remote_product_link(eadu_contact, eadu_ident, product)

        vals = self._eadu_update_values_from_fields(fields)
        if vals:
            product.sudo().with_context(eadu_message=True).write(vals)

        self._eadu_upsert_supplierinfo(
            product,
            eadu_contact,
            name,
            default_code=default_code,
            price=remote_sale_price,
            currency_id=currency_id,
            min_qty=min_qty,
            tiered_prices=tiered_prices,
        )
        return {'result': product.id}

    def action_eadu_update(self, eadu_ident, fields):
        self._eadu_check_portal()
        eadu_contact = self.env.user.partner_id
        eadu_any = self.env['eadu.partner.any'].sudo()._search_for_eadu_ident(
            eadu_contact, 'product.product', eadu_ident
        )
        if not eadu_any:
            return {'result': False}
        vals = self._eadu_update_values_from_fields(fields)
        if vals:
            eadu_any._get_record().sudo().with_context(eadu_message=True).write(vals)
        return {'result': True}

    def action_eadu_create_product_link(self, eadu_ident, partner_product_id):
        """Remote callback: map one of our products to the partner's local product."""
        self._eadu_check_portal()
        eadu_contact = self.env.user.partner_id
        local_product = self.sudo().with_context(active_test=False).browse(eadu_ident)
        if not local_product.exists():
            return {'result': False}
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            eadu_contact,
            'product.product',
            local_product.id,
            partner_product_id,
            partner_master=False,
        )
        return {'result': True}

    def action_eadu_open_merge_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Merge Eadu Product"),
            'res_model': 'eadu.product.merge.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_source_product_id': self.id,
                'active_test': False,
            },
        }

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message'):
            changed_fields = set(vals) & set(self._eadu_update_field_names())
            if not changed_fields:
                return res
            EaduAny = self.env['eadu.partner.any'].sudo()
            for product in self:
                eadu_anys = EaduAny.search([
                    ('res_model', '=', 'product.product'),
                    ('res_id', '=', product.id),
                ])
                for eadu_any in eadu_anys:
                    product._eadu_send_update(eadu_any, changed_fields)
        return res
