from odoo import _, api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    repair_authorization_ids = fields.Many2many(
        "gt.repair.authorization",
        compute="_compute_repair_authorizations",
        string="Repair Authorizations",
        groups="gt_repair_reauthorization.group_repair_authorization_technician",
    )
    repair_authorization_count = fields.Integer(
        compute="_compute_repair_authorizations",
        groups="gt_repair_reauthorization.group_repair_authorization_technician",
    )

    @api.depends("repair_order_ids.authorization_ids")
    def _compute_repair_authorizations(self):
        for order in self:
            authorizations = order.repair_order_ids.mapped("authorization_ids")
            order.repair_authorization_ids = authorizations
            order.repair_authorization_count = len(authorizations)

    def action_view_repair_authorizations(self):
        self.ensure_one()
        self.check_access("read")
        authorizations = self.repair_authorization_ids
        return {
            "type": "ir.actions.act_window",
            "name": _("Repair Authorizations"),
            "res_model": "gt.repair.authorization",
            "view_mode": "list,form",
            "domain": [("id", "in", authorizations.ids)],
        }
