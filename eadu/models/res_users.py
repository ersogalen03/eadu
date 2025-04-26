# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # We probably still need this for the message users
    eadu_ident = fields.Integer("Other Instance ID", copy=False)

    is_eadu_user = fields.Boolean("Is Eadu User", copy=False, groups="base.group_system") # Security measure
    