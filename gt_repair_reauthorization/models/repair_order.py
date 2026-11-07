from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .authorization import (
    ADVISER_GROUP,
    _fingerprint_repair_scope,
    _format_scope_preview,
    _scope_totals,
    _validated_scope_lines,
)


class RepairOrder(models.Model):
    _inherit = "repair.order"

    authorization_ids = fields.One2many(
        "gt.repair.authorization", "repair_id", string="Customer Authorizations",
        groups="gt_repair_reauthorization.group_repair_authorization_technician",
    )
    authorization_count = fields.Integer(
        compute="_compute_authorization_status",
        groups="gt_repair_reauthorization.group_repair_authorization_technician",
    )
    authorization_status = fields.Selection(
        [("none", "Not Recorded"), ("authorized", "Original Authorization Recorded")],
        compute="_compute_authorization_status", string="Customer Authorization",
        groups="gt_repair_reauthorization.group_repair_authorization_technician",
    )

    @api.depends("authorization_ids", "authorization_ids.state")
    def _compute_authorization_status(self):
        for repair in self:
            repair.authorization_count = len(repair.authorization_ids)
            repair.authorization_status = "authorized" if repair.authorization_ids else "none"

    def action_open_original_authorization_capture(self):
        self.ensure_one()
        if not self.env.user.has_group(ADVISER_GROUP):
            raise AccessError(_("You are not authorized to record customer approval."))
        self.check_access("read")
        if not self.sale_order_id:
            raise UserError(_("Link a customer-facing quotation to this repair before recording authorization."))
        if self.company_id not in self.env.companies:
            raise AccessError(_("You are not allowed to record authorization for this company."))
        if self.authorization_ids:
            raise UserError(_("An original authorization has already been recorded for this repair."))
        sale_order = self.sale_order_id
        sale_order.check_access("read")
        scope_lines = _validated_scope_lines(self, sale_order)
        fingerprint = _fingerprint_repair_scope(self, sale_order)
        wizard = self.env["gt.repair.authorization.capture.wizard"].sudo().with_context({}).create({
            "repair_id": self.id,
            "sale_order_id": sale_order.id,
            "source_fingerprint": fingerprint,
            "scope_preview": _format_scope_preview(sale_order.currency_id, scope_lines),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Record Original Customer Authorization"),
            "res_model": "gt.repair.authorization.capture.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": wizard.id,
            "target": "new",
        }

    def action_view_authorizations(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Customer Authorizations"),
            "res_model": "gt.repair.authorization",
            "view_mode": "list,form",
            "domain": [("repair_id", "=", self.id)],
            "context": {"default_repair_id": self.id},
        }
