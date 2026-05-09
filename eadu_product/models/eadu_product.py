# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class EaduProduct(models.Model):
    _name = "eadu.product"
    _description = "Product imported from an Eadu partner database"
    _order = "state, name"

    # ── Origin ──────────────────────────────────────────────────────────────

    partner_id = fields.Many2one(
        'res.partner', 'Eadu Contact', required=True, index=True, ondelete='restrict',
        help="The Eadu contact (child partner with eadu_url) that represents the remote database.",
    )
    eadu_ident = fields.Integer(
        'Remote Product ID', required=True,
        help="The product.product ID in the remote database.",
    )
    company_id = fields.Many2one(
        'res.company', 'Company',
        compute='_compute_company_id', store=True,
    )

    # ── Remote product data ──────────────────────────────────────────────────

    name = fields.Char('Product Name', required=True)
    barcode = fields.Char('Barcode')
    default_code = fields.Char('Internal Reference')
    standard_price = fields.Float('Cost')

    # ── Local mapping ────────────────────────────────────────────────────────

    product_id = fields.Many2one(
        'product.product', 'Local Product',
        help="The local product.product this import has been linked to.",
    )
    state = fields.Selection(
        [('pending', 'Pending'), ('linked', 'Linked')],
        string='State', default='pending', required=True, index=True,
    )
    suggested_product_id = fields.Many2one(
        'product.product', 'Suggested Product',
        compute='_compute_suggested_product_id',
        help="Automatically suggested local product based on barcode or name.",
    )

    # ── Computes ─────────────────────────────────────────────────────────────

    @api.depends('partner_id')
    def _compute_company_id(self):
        for rec in self:
            rec.company_id = rec.partner_id.company_id if rec.partner_id else False

    @api.depends('barcode', 'name')
    def _compute_suggested_product_id(self):
        for rec in self:
            product = self.env['product.product']
            if rec.barcode:
                product = self.env['product.product'].search(
                    [('barcode', '=', rec.barcode)], limit=1
                )
            if not product and rec.name:
                product = self.env['product.product'].search(
                    [('name', '=ilike', rec.name)], limit=1
                )
            rec.suggested_product_id = product

    # ── User actions ─────────────────────────────────────────────────────────

    def action_create_product(self):
        """Create a new product.product from the imported data and link it."""
        self.ensure_one()
        if self.state == 'linked':
            raise UserError(_("This product import is already linked."))

        product_vals = {'name': self.name}
        if self.partner_id.company_id:
            product_vals['company_id'] = self.partner_id.company_id.id
        if self.barcode:
            product_vals['barcode'] = self.barcode
        if self.default_code:
            product_vals['default_code'] = self.default_code
        if self.standard_price:
            product_vals['standard_price'] = self.standard_price

        template = self.env['product.template'].sudo().create(product_vals)
        product = template.product_variant_ids[:1]
        self._do_link(product)

    def action_link_product(self):
        """Link this import to the product_id that has been set on the record."""
        self.ensure_one()
        if self.state == 'linked':
            raise UserError(_("This product import is already linked."))
        if not self.product_id:
            raise UserError(_("Please set a local product before linking."))
        self._do_link(self.product_id)

    # ── Core linking logic ───────────────────────────────────────────────────

    def _do_link(self, product):
        """
        Shared logic executed both by action_create_product and action_link_product:

        1. Create product.supplierinfo using the remote product data.
        2. Create the local eadu.partner.any mapping (remote db is master).
        3. Notify the remote partner so it creates its own eadu.partner.any.
        4. Mark this record as linked.
        """
        self.ensure_one()
        eadu_contact = self.partner_id
        # The vendor for supplierinfo is the commercial partner of the eadu contact.
        vendor_partner = eadu_contact.commercial_partner_id

        # 1. Supplier pricelist entry
        self.env['product.supplierinfo'].sudo().create({
            'partner_id': vendor_partner.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_id': product.id,
            'product_name': self.name,
            'product_code': self.default_code or False,
            'price': self.standard_price or 0.0,
            'company_id': self.env.company.id,
        })

        # 2. Local eadu.partner.any (partner/remote db is the master)
        EaduAny = self.env['eadu.partner.any'].sudo()
        eadu_any = EaduAny._search_create_for_eadu_partner(
            eadu_contact, 'product.product', product.id,
            self.eadu_ident, partner_master=True,
        )

        # 3. Notify remote db – attach queued call to the mapping record so it
        #    survives a connection failure and is retried automatically.
        EaduAny._send_or_queue(
            eadu_contact,
            'eadu.product',
            'action_eadu_create_product_link',
            {
                'eadu_ident': self.eadu_ident,
                'partner_product_id': product.id,
            },
            eadu_any_ref=eadu_any,
        )

        # 4. Update this record
        self.sudo().write({'product_id': product.id, 'state': 'linked'})

    # ── Remote-callable endpoints ────────────────────────────────────────────

    def action_eadu_import_product(
        self, eadu_ident, name, barcode=None, default_code=None,
        standard_price=0.0,
    ):
        """
        Called by a remote Eadu partner to push a product into this database.

        The calling user must be an Eadu portal user; its partner_id is used as the
        eadu contact for the created record (avoids needing extra parameters).

        Returns ``{'result': eadu_product_id}`` so the caller can store our ID.
        """
        if not self.env.user.has_group('eadu.group_portal_eadu'):
            raise UserError(_("Not authorized"))

        eadu_contact = self.env.user.partner_id

        # Avoid duplicates for repeated pushes.
        existing = self.sudo().search([
            ('partner_id', '=', eadu_contact.id),
            ('eadu_ident', '=', eadu_ident),
        ], limit=1)
        if existing:
            return {'result': existing.id}

        record = self.sudo().create({
            'partner_id': eadu_contact.id,
            'eadu_ident': eadu_ident,
            'name': name,
            'barcode': barcode or False,
            'default_code': default_code or False,
            'standard_price': standard_price or 0.0,
        })
        return {'result': record.id}

    def action_eadu_create_product_link(self, eadu_ident, partner_product_id):
        """
        Called by a remote Eadu partner after they have linked one of our products
        (identified here by ``eadu_ident`` = our product.product ID) to their local
        product (``partner_product_id`` = their product.product ID).

        Creates the symmetric eadu.partner.any mapping on our side.

        Returns ``{'result': True}`` on success.
        """
        if not self.env.user.has_group('eadu.group_portal_eadu'):
            raise UserError(_("Not authorized"))

        eadu_contact = self.env.user.partner_id

        local_product = self.env['product.product'].sudo().browse(eadu_ident)
        if not local_product.exists():
            return {'result': False}

        # We are the master (remote db imported from us), so partner_master=False.
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            eadu_contact,
            'product.product',
            local_product.id,
            partner_product_id,
            partner_master=False,
        )
        return {'result': True}
