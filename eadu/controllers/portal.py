# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from odoo.addons.eadu.exceptions import EaduConnectionError
from odoo.addons.portal.controllers.portal import CustomerPortal


class EaduCustomerPortal(CustomerPortal):
    def _get_eadu_portal_partner(self):
        user = request.env.user
        if user._is_public() or not user.has_group("base.group_portal"):
            raise AccessError(_("Only portal users can connect EADU from the portal."))
        return user.partner_id.commercial_partner_id.sudo()

    @http.route(["/my/eadu"], type="http", auth="user", website=True, methods=["GET", "POST"])
    def portal_my_eadu(self, token=None, **kw):
        partner = self._get_eadu_portal_partner()
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "page_name": "eadu",
                "partner": partner,
                "eadu_partner": partner.eadu_connection_partner_id,
                "token": token or "",
            }
        )

        if request.httprequest.method == "POST":
            if partner.eadu_connection_partner_id:
                values["success"] = _("This portal account is already connected with EADU.")
            elif not (token or "").strip():
                values["error"] = _("Paste an EADU token before connecting.")
            else:
                try:
                    partner._process_eadu_exchange_token_from_portal(token, request.env.user)
                except (AccessError, EaduConnectionError, UserError) as error:
                    values["error"] = str(error)
                else:
                    values["success"] = _("Your EADU connection is active.")
                    values["eadu_partner"] = partner.eadu_connection_partner_id
                    values["token"] = ""

        return request.render("eadu.portal_my_eadu", values)
