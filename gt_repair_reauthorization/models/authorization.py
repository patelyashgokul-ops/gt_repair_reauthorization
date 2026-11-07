import base64
import binascii
import hashlib
import json

from psycopg2 import IntegrityError

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


ADVISER_GROUP = "gt_repair_reauthorization.group_repair_authorization_adviser"
MANAGER_GROUP = "gt_repair_reauthorization.group_repair_authorization_manager"
VIEWER_GROUP = "gt_repair_reauthorization.group_repair_authorization_technician"
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024


class RepairAuthorization(models.Model):
    _name = "gt.repair.authorization"
    _description = "Repair Customer Authorization Snapshot"
    _order = "authorization_datetime desc, id desc"
    _check_company_auto = True
    _rec_name = "name"
    _copy = False

    name = fields.Char(required=True, readonly=True, copy=False, index=True)
    repair_id = fields.Many2one(
        "repair.order", required=True, readonly=True, index=True,
        check_company=True, ondelete="restrict",
    )
    sale_order_id = fields.Many2one(
        "sale.order", string="Quotation", required=True, readonly=True, index=True,
        check_company=True, ondelete="restrict",
    )
    partner_id = fields.Many2one(
        "res.partner", string="Customer", required=True, readonly=True,
        check_company=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True, ondelete="restrict",
    )
    repair_reference = fields.Char(required=True, readonly=True)
    quotation_reference = fields.Char(required=True, readonly=True)
    customer_name = fields.Char(required=True, readonly=True)
    company_name = fields.Char(required=True, readonly=True)
    currency_id = fields.Many2one("res.currency", required=True, readonly=True, ondelete="restrict")
    currency_code = fields.Char(required=True, readonly=True)
    version = fields.Integer(required=True, readonly=True, default=1, copy=False)
    state = fields.Selection(
        [("authorized", "Customer Authorized")],
        required=True, readonly=True, default="authorized", copy=False,
    )
    authorization_datetime = fields.Datetime(required=True, readonly=True, copy=False)
    customer_response_datetime = fields.Datetime(readonly=True, copy=False)
    authorized_by_id = fields.Many2one(
        "res.users", string="Recorded By", required=True, readonly=True,
        ondelete="restrict", copy=False,
    )
    approved_untaxed_total = fields.Monetary(
        currency_field="currency_id", required=True, readonly=True, copy=False,
    )
    approved_tax_total = fields.Monetary(
        currency_field="currency_id", required=True, readonly=True, copy=False,
    )
    approved_total = fields.Monetary(
        currency_field="currency_id", required=True, readonly=True, copy=False,
    )
    evidence_type = fields.Selection(
        [("email", "Recorded Customer Email"), ("file", "Customer Evidence File")],
        required=True, readonly=True, copy=False,
    )
    evidence_reference = fields.Text(
        string="Evidence Context", required=True, readonly=True, copy=False,
        groups=f"{ADVISER_GROUP},{MANAGER_GROUP}",
    )
    evidence_email = fields.Text(
        string="Recorded Email", readonly=True, copy=False,
        groups=f"{ADVISER_GROUP},{MANAGER_GROUP}",
    )
    evidence_file = fields.Binary(
        string="Evidence File", attachment=False, readonly=True, copy=False,
        groups=f"{ADVISER_GROUP},{MANAGER_GROUP}",
    )
    evidence_filename = fields.Char(
        string="Evidence Filename", readonly=True, copy=False,
        groups=f"{ADVISER_GROUP},{MANAGER_GROUP}",
    )
    note = fields.Text(readonly=True, copy=False)
    previous_authorization_id = fields.Many2one(
        "gt.repair.authorization", string="Previous Authorization", readonly=True,
        check_company=True, ondelete="restrict", copy=False,
    )
    line_ids = fields.One2many(
        "gt.repair.authorization.line", "authorization_id", string="Approved Lines",
        readonly=True, copy=False,
    )

    _sql_constraints = [
        (
            "repair_version_unique",
            "unique(repair_id, version)",
            "An authorization version can only be recorded once for a repair.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Authorization snapshots can only be created by the validated capture workflow."))
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(_("A finalized authorization snapshot cannot be modified."))

    def unlink(self):
        raise UserError(_("A finalized authorization snapshot cannot be deleted."))

    def copy(self, default=None):
        raise UserError(_("Authorization snapshots cannot be copied to another repair."))

    @api.model
    def _capture_original(self, repair, sale_order, wizard_values, line_values, totals):
        """Persist only already validated values, using sudo for the write only."""
        repair.ensure_one()
        sale_order.ensure_one()
        vals = {
            "name": _("%s / Authorization v1", repair.display_name),
            "repair_id": repair.id,
            "sale_order_id": sale_order.id,
            "partner_id": repair.partner_id.id,
            "company_id": repair.company_id.id,
            "repair_reference": repair.name,
            "quotation_reference": sale_order.name,
            "customer_name": repair.partner_id.display_name,
            "company_name": repair.company_id.display_name,
            "currency_id": sale_order.currency_id.id,
            "currency_code": sale_order.currency_id.name,
            "version": 1,
            "state": "authorized",
            "authorization_datetime": fields.Datetime.now(),
            "customer_response_datetime": wizard_values.get("customer_response_datetime") or False,
            "authorized_by_id": self.env.uid,
            "approved_untaxed_total": totals["untaxed"],
            "approved_tax_total": totals["tax"],
            "approved_total": totals["total"],
            "evidence_type": wizard_values["evidence_type"],
            "evidence_reference": wizard_values["evidence_reference"].strip(),
            "evidence_email": wizard_values.get("evidence_email") or False,
            "evidence_file": wizard_values.get("evidence_file") or False,
            "evidence_filename": wizard_values.get("evidence_filename") or False,
            "note": wizard_values.get("note") or False,
            "line_ids": [Command.create(line) for line in line_values],
        }
        try:
            with self.env.cr.savepoint():
                return self.sudo().with_context({}).create(vals)
        except IntegrityError as error:
            raise UserError(_("An original authorization has already been recorded for this repair.")) from error


class RepairAuthorizationLine(models.Model):
    _name = "gt.repair.authorization.line"
    _description = "Repair Customer Authorization Snapshot Line"
    _order = "sequence, id"
    _check_company_auto = True
    _copy = False

    authorization_id = fields.Many2one(
        "gt.repair.authorization", required=True, readonly=True, index=True,
        check_company=True, ondelete="restrict",
    )
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True, ondelete="restrict")
    currency_id = fields.Many2one("res.currency", required=True, readonly=True, ondelete="restrict")
    currency_code = fields.Char(required=True, readonly=True)
    sequence = fields.Integer(required=True, readonly=True)
    source_model = fields.Char(required=True, readonly=True)
    source_sale_line_db_id = fields.Integer(string="Original Quotation Line ID", required=True, readonly=True, index=True)
    source_line_sequence = fields.Integer(readonly=True)
    source_sale_line_id = fields.Many2one(
        "sale.order.line", string="Original Quotation Line", readonly=True,
        check_company=True, ondelete="set null",
    )
    source_repair_move_ids = fields.Many2many(
        "stock.move", "gt_repair_auth_line_move_rel", "auth_line_id", "move_id",
        string="Repair Parts", readonly=True, check_company=True,
    )
    source_repair_service_line_id = fields.Many2one(
        "repair.service.line", string="Repair Service", readonly=True,
        check_company=True, ondelete="set null",
    )
    source_repair_move_db_ids = fields.Json(readonly=True)
    source_repair_service_line_db_id = fields.Integer(readonly=True)
    source_line_reference = fields.Char(required=True, readonly=True)
    scope_type = fields.Selection(
        [("part", "Part"), ("service", "Service"), ("other", "Other Quoted Work")],
        required=True, readonly=True,
    )
    product_id = fields.Many2one(
        "product.product", string="Product", readonly=True, check_company=True, ondelete="set null",
    )
    product_db_id = fields.Integer(required=True, readonly=True)
    product_name = fields.Char(required=True, readonly=True)
    product_default_code = fields.Char(readonly=True)
    description = fields.Text(required=True, readonly=True)
    quantity = fields.Float(required=True, readonly=True, digits="Product Unit")
    uom_id = fields.Many2one("uom.uom", string="Unit of Measure", readonly=True, ondelete="set null")
    uom_db_id = fields.Integer(required=True, readonly=True)
    uom_name = fields.Char(required=True, readonly=True)
    price_unit = fields.Monetary(currency_field="currency_id", required=True, readonly=True)
    discount = fields.Float(readonly=True, digits="Discount")
    tax_names = fields.Char(readonly=True)
    tax_snapshot = fields.Json(readonly=True)
    untaxed_subtotal = fields.Monetary(currency_field="currency_id", required=True, readonly=True)
    tax_amount = fields.Monetary(currency_field="currency_id", required=True, readonly=True)
    customer_total = fields.Monetary(currency_field="currency_id", required=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Authorization snapshot lines can only be created by the validated capture workflow."))
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(_("A finalized authorization snapshot cannot be modified."))

    def unlink(self):
        raise UserError(_("A finalized authorization snapshot cannot be deleted."))

    def copy(self, default=None):
        raise UserError(_("Authorization snapshot lines cannot be copied."))


def _line_tax_snapshot(line):
    tax_ids = line.tax_ids.sorted(key=lambda tax: (tax.sequence, tax.id))
    currency = line.currency_id
    discounted_unit = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
    computed = tax_ids.compute_all(
        discounted_unit,
        currency=currency,
        quantity=line.product_uom_qty,
        product=line.product_id,
        partner=line.order_id.partner_id,
        document_tax_mode=getattr(line.order_id, "document_tax_mode", None),
    )
    return {
        "configured_taxes": [
            {
                "id": tax.id,
                "name": tax.name,
                "amount": tax.amount,
                "amount_type": tax.amount_type,
                "price_include": tax.price_include,
                "include_base_amount": tax.include_base_amount,
                "sequence": tax.sequence,
                "tax_group": tax.tax_group_id.name,
                "company": tax.company_id.name or False,
                "children": [child.name for child in tax.children_tax_ids.sorted(key=lambda child: (child.sequence, child.id))],
            }
            for tax in tax_ids
        ],
        "computed_taxes": [
            {
                "name": tax.get("name"),
                "amount": tax.get("amount", 0.0),
                "base": tax.get("base", 0.0),
                "sequence": tax.get("sequence", 0),
            }
            for tax in computed.get("taxes", [])
        ],
        "computed_total_excluded": computed.get("total_excluded", 0.0),
        "computed_total_included": computed.get("total_included", 0.0),
        "source_price_subtotal": line.price_subtotal,
        "source_price_tax": line.price_tax,
        "source_price_total": line.price_total,
    }


def _fingerprint_repair_scope(repair, sale_order):
    lines = []
    for line in sale_order.order_line.sorted(key=lambda item: (item.sequence, item.id)):
        lines.append({
            "id": line.id,
            "sequence": line.sequence,
            "display_type": line.display_type or "",
            "name": line.name or "",
            "product": line.product_id.id or 0,
            "product_name": line.product_id.display_name or "" if line.product_id else "",
            "default_code": line.product_id.default_code or "" if line.product_id else "",
            "quantity": line.product_uom_qty,
            "uom": line.product_uom_id.id or 0,
            "uom_name": line.product_uom_id.name or "" if line.product_uom_id else "",
            "price_unit": line.price_unit,
            "discount": line.discount,
            "taxes": [
                [tax.id, tax.name, tax.amount, tax.amount_type, tax.price_include, tax.sequence]
                for tax in line.tax_ids.sorted(key=lambda tax: (tax.sequence, tax.id))
            ],
            "subtotal": line.price_subtotal,
            "tax": line.price_tax,
            "total": line.price_total,
            "downpayment": bool(line.is_downpayment),
            "discount_line": bool(line._is_discount_line()),
            "move_links": sorted(line.move_ids.ids),
            "service_link": line.repair_service_line_id.id or 0,
        })
    payload = {
        "repair": repair.id,
        "repair_name": repair.name,
        "repair_company": repair.company_id.id,
        "repair_partner": repair.partner_id.id,
        "origin_sale_line": repair.sale_order_line_id.id,
        "sale_order": sale_order.id,
        "sale_order_name": sale_order.name,
        "sale_order_state": sale_order.state,
        "sale_order_company": sale_order.company_id.id,
        "sale_order_partner": sale_order.partner_id.id,
        "currency": sale_order.currency_id.id,
        "currency_rounding": sale_order.currency_id.rounding,
        "amount_untaxed": sale_order.amount_untaxed,
        "amount_tax": sale_order.amount_tax,
        "amount_total": sale_order.amount_total,
        "lines": lines,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validated_scope_lines(repair, sale_order):
    repair.ensure_one()
    sale_order.ensure_one()
    if not repair.sale_order_id or repair.sale_order_id != sale_order or repair not in sale_order.repair_order_ids:
        raise UserError(_("This quotation does not belong to the selected repair."))
    if repair.company_id != sale_order.company_id:
        raise ValidationError(_("The selected repair and quotation belong to different companies."))
    if repair.partner_id != sale_order.partner_id:
        raise ValidationError(_("The quotation customer does not match the repair customer."))
    if sale_order.state == "cancel":
        raise UserError(_("The quotation is cancelled; choose a current customer-facing quotation."))
    if sale_order.state not in ("draft", "sent", "sale"):
        raise UserError(_("This quotation is not in a state that can be recorded as customer-authorized."))

    repair_records = sale_order.repair_order_ids
    scope = []
    for line in sale_order.order_line.sorted(key=lambda item: (item.sequence, item.id)):
        if line.display_type:
            continue
        if line.is_downpayment:
            raise UserError(_("Down-payment lines cannot be assigned to one repair authorization safely."))
        if not line.product_id or not line.product_uom_id:
            raise UserError(_("A quotation line has no identifiable product or unit of measure."))
        if line.product_uom_id.compare(line.product_uom_qty, 0.0) <= 0:
            raise UserError(_("A quotation line has a zero or negative quantity; review the current scope."))

        moves = line.move_ids
        service_line = line.repair_service_line_id
        owners = moves.repair_id | service_line.repair_id
        if line == repair.sale_order_line_id:
            owners |= repair
        if any(owner.sale_order_id != sale_order or owner.company_id != sale_order.company_id for owner in owners):
            raise UserError(_("A quotation line is linked to a repair outside this quotation or company."))
        if repair in owners and len(owners) > 1:
            raise UserError(_("A quotation line is linked to more than one repair and cannot be assigned safely."))

        if repair not in owners:
            if owners and all(owner in repair_records for owner in owners):
                continue
            if not owners and line._is_discount_line() and len(repair_records) == 1 and repair in repair_records:
                scope_type = "other"
            else:
                raise UserError(_("A quotation line cannot be matched to this repair. Remove or map the ambiguous line before recording approval."))
        elif service_line and service_line.repair_id == repair:
            scope_type = "service"
        elif moves.filtered(lambda move: move.repair_id == repair and move.repair_line_type == "add"):
            scope_type = "part"
        elif line == repair.sale_order_line_id:
            scope_type = "service" if line.product_id.type == "service" else "other"
        else:
            raise UserError(_("A quotation line is not a chargeable repair part or service for this repair."))

        if moves.filtered(lambda move: move.repair_id == repair and move.repair_line_type != "add"):
            raise UserError(_("A quotation line is linked to a removed or recycled part and cannot be authorized as a charge."))

        tax_snapshot = _line_tax_snapshot(line)
        scope.append({
            "sequence": len(scope) + 1,
            "source_model": "sale.order.line",
            "source_sale_line_db_id": line.id,
            "source_line_sequence": line.sequence,
            "source_sale_line_id": line.id,
            "source_repair_move_ids": [Command.set(moves.filtered(lambda move: move.repair_id == repair).ids)],
            "source_repair_service_line_id": service_line.id if service_line and service_line.repair_id == repair else False,
            "source_repair_move_db_ids": moves.filtered(lambda move: move.repair_id == repair).ids,
            "source_repair_service_line_db_id": service_line.id if service_line and service_line.repair_id == repair else 0,
            "source_line_reference": f"{sale_order.name or sale_order.id} / line {line.sequence} / record {line.id}",
            "company_id": repair.company_id.id,
            "currency_id": sale_order.currency_id.id,
            "currency_code": sale_order.currency_id.name,
            "scope_type": scope_type,
            "product_id": line.product_id.id,
            "product_db_id": line.product_id.id,
            "product_name": line.product_id.display_name,
            "product_default_code": line.product_id.default_code or False,
            "description": line.name or line.product_id.display_name,
            "quantity": line.product_uom_qty,
            "uom_id": line.product_uom_id.id,
            "uom_db_id": line.product_uom_id.id,
            "uom_name": line.product_uom_id.display_name,
            "price_unit": line.price_unit,
            "discount": line.discount,
            "tax_names": ", ".join(line.tax_ids.sorted(key=lambda tax: (tax.sequence, tax.id)).mapped("name")),
            "tax_snapshot": tax_snapshot,
            "untaxed_subtotal": line.price_subtotal,
            "tax_amount": line.price_tax,
            "customer_total": line.price_total,
        })
    if not scope:
        raise UserError(_("No identifiable chargeable repair lines are linked to this quotation."))
    return scope


def _scope_totals(currency, scope_lines):
    return {
        "untaxed": currency.round(sum(line["untaxed_subtotal"] for line in scope_lines)),
        "tax": currency.round(sum(line["tax_amount"] for line in scope_lines)),
        "total": currency.round(sum(line["customer_total"] for line in scope_lines)),
    }


def _format_scope_preview(currency, scope_lines):
    rows = []
    for line in scope_lines:
        rows.append(
            _("%(description)s — %(quantity)s %(uom)s; unit %(unit)s; discount %(discount)s%%; total %(total)s",
              description=line["description"], quantity=line["quantity"], uom=line["uom_name"],
              unit=currency.format(line["price_unit"]), discount=line["discount"],
              total=currency.format(line["customer_total"]),)
        )
    return "\n".join(rows)


def _check_evidence(wizard):
    if not wizard.evidence_reference or not wizard.evidence_reference.strip():
        raise ValidationError(_("Customer approval evidence is required."))
    if not wizard.evidence_scope_confirmed:
        raise ValidationError(_("Confirm that the evidence explicitly approves this customer, quotation revision, and displayed repair scope."))
    if wizard.evidence_type == "email":
        if not wizard.evidence_email or not wizard.evidence_email.strip():
            raise ValidationError(_("Customer approval evidence is required: record the approving email."))
        if wizard.evidence_file:
            raise ValidationError(_("Choose either recorded email evidence or an evidence file."))
    elif wizard.evidence_type == "file":
        if not wizard.evidence_file or not wizard.evidence_filename:
            raise ValidationError(_("Customer approval evidence is required: attach the approval file."))
        if wizard.evidence_email:
            raise ValidationError(_("Choose either recorded email evidence or an evidence file."))
        try:
            raw = base64.b64decode(wizard.evidence_file, validate=True)
        except (binascii.Error, ValueError, TypeError) as error:
            raise ValidationError(_("The evidence file is not valid base64 data.")) from error
        if not raw or len(raw) > MAX_EVIDENCE_BYTES:
            raise ValidationError(_("The evidence file must be non-empty and no larger than 10 MB."))
    else:
        raise ValidationError(_("Customer approval evidence is required."))
    if wizard.customer_response_datetime and wizard.customer_response_datetime > fields.Datetime.now():
        raise ValidationError(_("The customer response date cannot be in the future."))


def _build_original_snapshot(wizard):
    if not wizard.env.user.has_group(ADVISER_GROUP):
        raise AccessError(_("You are not authorized to record customer approval."))
    repair = wizard.repair_id
    sale_order = wizard.sale_order_id
    repair.ensure_one()
    sale_order.ensure_one()
    repair.check_access("read")
    sale_order.check_access("read")
    if repair.company_id not in wizard.env.companies or sale_order.company_id not in wizard.env.companies:
        raise AccessError(_("You are not allowed to record authorization for this company."))
    if repair.sale_order_id != sale_order or repair not in sale_order.repair_order_ids:
        raise UserError(_("This quotation does not belong to the selected repair."))
    if repair.company_id != sale_order.company_id:
        raise ValidationError(_("The selected repair and quotation belong to different companies."))
    if repair.partner_id != sale_order.partner_id:
        raise ValidationError(_("The quotation customer does not match the repair customer."))
    if sale_order.state == "cancel":
        raise UserError(_("The quotation is cancelled; choose a current customer-facing quotation."))

    existing = wizard.env["gt.repair.authorization"].search([("repair_id", "=", repair.id)], limit=1)
    if existing:
        raise UserError(_("An original authorization has already been recorded for this repair."))
    _check_evidence(wizard)
    scope_lines = _validated_scope_lines(repair, sale_order)
    if _fingerprint_repair_scope(repair, sale_order) != wizard.source_fingerprint:
        raise UserError(_("The quotation has changed; review the current scope before recording customer approval."))
    totals = _scope_totals(sale_order.currency_id, scope_lines)
    return repair, sale_order, scope_lines, totals

