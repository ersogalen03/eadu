# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class EaduExchangeWizard(models.TransientModel):
    _name = "eadu.exchange.wizard"
    _description = "EADU Connection Exchange"

    mode = fields.Selection(
        [
            ("generate", "Generate a code"),
            ("receive", "Receive a code"),
        ],
        required=True,
        default="generate",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        domain=[("is_company", "=", True)],
    )
    token = fields.Text(string="Received Code")
    generated_token = fields.Text(string="Generated Code", readonly=True)
    guessed_partner_id = fields.Many2one(
        "res.partner",
        string="Suggested Partner",
        readonly=True,
    )
    remote_url = fields.Char(readonly=True)
    remote_db = fields.Char(readonly=True)
    remote_user_name = fields.Char(readonly=True)
    remote_partner_name = fields.Char(readonly=True)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if self.env.context.get("active_model") == "res.partner" and self.env.context.get("active_id"):
            vals["partner_id"] = self.env.context["active_id"]
        return vals

    def _check_exchange_access(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only users with Settings access can manage EADU connections."))

    @api.onchange("token", "mode")
    def _onchange_token(self):
        self.remote_url = False
        self.remote_db = False
        self.remote_user_name = False
        self.remote_partner_name = False
        self.guessed_partner_id = False
        if self.mode != "receive" or not self.token:
            return

        try:
            token_data = self.env["res.partner"]._decode_eadu_exchange_token(self.token)
        except UserError:
            return

        self.remote_url = token_data["url"]
        self.remote_db = token_data["db"]
        self.remote_user_name = token_data["user_name"]
        self.remote_partner_name = token_data.get("partner_name")

        guessed_partner = self._guess_partner_from_token(token_data)
        self.guessed_partner_id = guessed_partner
        if guessed_partner and not self.partner_id:
            self.partner_id = guessed_partner

    def _guess_partner_from_token(self, token_data):
        Partner = self.env["res.partner"]
        remote_url = token_data.get("url")
        if remote_url:
            eadu_contact = Partner.search([("eadu_url", "=", remote_url)], limit=1)
            if eadu_contact:
                return eadu_contact.commercial_partner_id

        partner_name = token_data.get("partner_name")
        if partner_name:
            partner = Partner.search([
                ("is_company", "=", True),
                ("name", "=ilike", partner_name),
            ], limit=1)
            if partner:
                return partner
            partner = Partner.search([
                ("is_company", "=", True),
                ("name", "ilike", partner_name),
            ], limit=1)
            if partner:
                return partner

        remote_db = token_data.get("db")
        if remote_db:
            return Partner.search([
                ("is_company", "=", True),
                ("name", "ilike", remote_db),
            ], limit=1)
        return Partner.browse()

    def action_generate(self):
        self.ensure_one()
        self._check_exchange_access()
        if not self.partner_id:
            raise UserError(_("Choose the partner that should receive this EADU code."))
        self.generated_token = self.partner_id._generate_eadu_exchange()
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect EADU"),
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def action_receive(self):
        self.ensure_one()
        self._check_exchange_access()
        if not self.partner_id:
            raise UserError(_("Choose the partner that sent this EADU code."))
        if not self.token:
            raise UserError(_("Paste the EADU code you received."))

        self.partner_id._process_eadu_exchange_token(self.token)
        return {
            "type": "ir.actions.act_window",
            "name": _("Partner"),
            "res_model": "res.partner",
            "view_mode": "form",
            "res_id": self.partner_id.id,
        }
