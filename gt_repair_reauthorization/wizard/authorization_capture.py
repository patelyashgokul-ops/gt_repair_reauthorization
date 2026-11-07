from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.authorization import (
    ADVISER_GROUP,
    _build_original_snapshot,
    _fingerprint_repair_scope,
)


class RepairAuthorizationCaptureWizard(models.TransientModel):
    _name = "gt.repair.authorization.capture.wizard"
    _description = "Record Original Repair Customer Authorization"

    repair_id = fields.Many2one("repair.order", required=True, readonly=True, check_company=True)
    sale_order_id = fields.Many2one("sale.order", string="Quotation", required=True, readonly=True, check_company=True)
    company_id = fields.Many2one(related="repair_id.company_id", readonly=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id", readonly=True)
    source_fingerprint = fields.Char(required=True, readonly=True, groups=ADVISER_GROUP)
    scope_preview = fields.Text(readonly=True)
    evidence_type = fields.Selection(
        [("email", "Recorded Customer Email"), ("file", "Customer Evidence File")],
        required=True, default="email",
    )
    evidence_reference = fields.Text(
        string="Evidence Context", required=True,
        help="Record the sender or file source, response date, and quotation revision. This records staff-reported evidence and does not authenticate the sender's identity.",
    )
    evidence_email = fields.Text(string="Customer Email Text")
    evidence_file = fields.Binary(string="Evidence File", attachment=False)
    evidence_filename = fields.Char(string="Filename")
    evidence_scope_confirmed = fields.Boolean(
        string="I verified that this evidence explicitly approves the customer, quotation revision, and displayed repair scope.",
        required=True,
    )
    customer_response_datetime = fields.Datetime(string="Customer Response Date")
    note = fields.Text(string="Internal Note")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Use the repair action to open the validated authorization capture form."))
        return super().create(vals_list)

    def write(self, vals):
        protected = {"repair_id", "sale_order_id", "company_id", "currency_id", "source_fingerprint", "scope_preview"}
        if protected.intersection(vals):
            raise UserError(_("The repair, quotation, and displayed scope cannot be changed in this capture form."))
        return super().write(vals)

    def action_record_authorization(self):
        self.ensure_one()
        if not self.env.user.has_group(ADVISER_GROUP):
            raise AccessError(_("You are not authorized to record customer approval."))
        repair, sale_order, scope_lines, totals = _build_original_snapshot(self)
        authorization = self.env["gt.repair.authorization"]._capture_original(
            repair,
            sale_order,
            self.read([
                "evidence_type", "evidence_reference", "evidence_email", "evidence_file",
                "evidence_filename", "customer_response_datetime", "note",
            ])[0],
            scope_lines,
            totals,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Customer Authorization"),
            "res_model": "gt.repair.authorization",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": authorization.id,
            "target": "current",
        }
