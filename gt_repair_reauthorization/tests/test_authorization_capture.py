import base64

from odoo import Command, fields
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import float_compare


@tagged("post_install", "-at_install")
class TestRepairAuthorizationCapture(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.customer = cls.env["res.partner"].create({"name": "Authorization Test Customer"})
        cls.other_customer = cls.env["res.partner"].create({"name": "Different Test Customer"})
        cls.unit = cls.env.ref("uom.product_uom_unit")
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.adviser = cls._create_user(
            "reauth_adviser",
            [
                cls.env.ref("gt_repair_reauthorization.group_repair_authorization_adviser"),
                cls.env.ref("sales_team.group_sale_salesman"),
                cls.env.ref("stock.group_stock_user"),
            ],
            cls.company,
        )
        cls.technician = cls._create_user(
            "reauth_technician",
            [
                cls.env.ref("gt_repair_reauthorization.group_repair_authorization_technician"),
                cls.env.ref("stock.group_stock_user"),
            ],
            cls.company,
        )
        cls.manager = cls._create_user(
            "reauth_manager",
            [
                cls.env.ref("gt_repair_reauthorization.group_repair_authorization_manager"),
                cls.env.ref("sales_team.group_sale_salesman"),
                cls.env.ref("stock.group_stock_user"),
            ],
            cls.company,
        )
        cls.part = cls.env["product.product"].create({
            "name": "Snapshot Test Part",
            "type": "consu",
            "is_storable": True,
            "list_price": 100.0,
            "default_code": "SNAP-PART",
        })
        cls.service = cls.env["product.product"].create({
            "name": "Snapshot Test Service",
            "type": "service",
            "list_price": 55.0,
            "default_code": "SNAP-SERVICE",
        })
        cls.device = cls.env["product.product"].create({
            "name": "Device Under Repair",
            "type": "consu",
            "is_storable": True,
        })
        cls.tax_excluded = cls.env["account.tax"].create({
            "name": "Authorization test 10% excluded",
            "amount_type": "percent",
            "amount": 10.0,
            "type_tax_use": "sale",
            "price_include_override": "tax_excluded",
            "company_id": cls.company.id,
        })
        cls.tax_included = cls.env["account.tax"].create({
            "name": "Authorization test 10% included",
            "amount_type": "percent",
            "amount": 10.0,
            "type_tax_use": "sale",
            "price_include_override": "tax_included",
            "company_id": cls.company.id,
        })

    @classmethod
    def _create_user(cls, login, groups, company):
        base_group = cls.env.ref("base.group_user")
        group_ids = list({group.id for group in [base_group, *groups]})
        return cls.env["res.users"].create({
            "name": login,
            "login": login,
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "group_ids": [Command.set(group_ids)],
        })

    def _create_repair_and_quote(self, company=None, customer=None, adviser=None):
        company = company or self.company
        customer = customer or self.customer
        adviser = adviser or self.adviser
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        self.assertTrue(warehouse, "The test company needs a warehouse with a repair operation type.")
        repair = self.env["repair.order"].with_company(company).create({
            "company_id": company.id,
            "product_id": self.device.id,
            "uom_id": self.unit.id,
            "picking_type_id": warehouse.repair_type_id.id,
            "partner_id": customer.id,
            "move_ids": [Command.create({
                "product_id": self.part.id,
                "product_uom_qty": 2.0,
                "uom_id": self.unit.id,
                "state": "draft",
                "repair_line_type": "add",
                "company_id": company.id,
            })],
            "repair_service_line_ids": [Command.create({
                "product_id": self.service.id,
                "quantity": 1.5,
                "uom_id": self.unit.id,
                "description": "Replace and calibrate the component",
            })],
        })
        repair.with_company(company).action_create_sale_order()
        quote = repair.sale_order_id
        quote.write({"user_id": adviser.id})
        self.assertEqual(len(quote.order_line), 2)
        part_line = quote.order_line.filtered("move_ids")
        service_line = quote.order_line.filtered("repair_service_line_id")
        self.assertEqual(len(part_line), 1)
        self.assertEqual(len(service_line), 1)
        taxes = self.company == company
        part_line.write({
            "price_unit": 100.0,
            "discount": 10.0,
            "tax_ids": [Command.set([self.tax_excluded.id] if taxes else [])],
        })
        service_line.write({
            "price_unit": 55.0,
            "discount": 0.0,
            "tax_ids": [Command.set([self.tax_included.id] if taxes else [])],
        })
        return repair, quote, part_line, service_line

    def _open_capture(self, repair, user=None):
        user = user or self.adviser
        context = {"allowed_company_ids": user.company_ids.ids}
        action = repair.with_user(user).with_context(**context).action_open_original_authorization_capture()
        return self.env[action["res_model"]].with_user(user).with_context(**context).browse(action["res_id"])

    def _record_approval(self, repair, user=None, evidence_type="email"):
        user = user or self.adviser
        wizard = self._open_capture(repair, user)
        values = {
            "evidence_type": evidence_type,
            "evidence_reference": f"Customer response for repair {repair.name}, quotation {repair.sale_order_id.name}, revision reviewed by service adviser.",
            "evidence_scope_confirmed": True,
            "customer_response_datetime": fields.Datetime.now(),
        }
        if evidence_type == "email":
            values["evidence_email"] = "Please proceed with the repair parts and service listed in this quotation."
        else:
            values.update({
                "evidence_file": base64.b64encode(b"customer approval file").decode("ascii"),
                "evidence_filename": "customer-approval.txt",
            })
        wizard.write(values)
        action = wizard.action_record_authorization()
        return self.env[action["res_model"]].browse(action["res_id"])

    def test_original_snapshot_preserves_scope_taxes_discounts_and_totals(self):
        repair, quote, part_line, service_line = self._create_repair_and_quote()
        authorization = self._record_approval(repair)
        currency = quote.currency_id

        self.assertEqual(authorization.state, "authorized")
        self.assertEqual(authorization.version, 1)
        self.assertEqual(authorization.authorized_by_id, self.adviser)
        self.assertEqual(authorization.partner_id, self.customer)
        self.assertEqual(authorization.company_id, self.company)
        self.assertEqual(authorization.currency_id, currency)
        self.assertEqual(len(authorization.line_ids), 2)

        part_snapshot = authorization.line_ids.filtered(lambda line: line.scope_type == "part")
        service_snapshot = authorization.line_ids.filtered(lambda line: line.scope_type == "service")
        self.assertEqual(len(part_snapshot), 1)
        self.assertEqual(len(service_snapshot), 1)
        self.assertEqual(part_snapshot.product_name, self.part.display_name)
        self.assertEqual(part_snapshot.product_default_code, "SNAP-PART")
        self.assertEqual(part_snapshot.description, part_line.name or self.part.display_name)
        self.assertEqual(part_snapshot.quantity, 2.0)
        self.assertEqual(part_snapshot.uom_id, self.unit)
        self.assertEqual(part_snapshot.price_unit, 100.0)
        self.assertEqual(part_snapshot.discount, 10.0)
        self.assertEqual(part_snapshot.tax_names, self.tax_excluded.name)
        self.assertEqual(part_snapshot.source_repair_move_db_ids, repair.move_ids.ids)
        self.assertEqual(service_snapshot.source_repair_service_line_db_id, repair.repair_service_line_ids.id)
        self.assertEqual(service_snapshot.discount, 0.0)
        self.assertEqual(service_snapshot.tax_names, self.tax_included.name)
        self.assertEqual(service_snapshot.untaxed_subtotal, 75.0)
        self.assertEqual(service_snapshot.tax_amount, 7.5)
        self.assertEqual(service_snapshot.customer_total, 82.5)
        self.assertTrue(part_snapshot.tax_snapshot["configured_taxes"])
        self.assertTrue(service_snapshot.tax_snapshot["computed_taxes"])
        self.assertEqual(part_snapshot.currency_code, currency.name)
        self.assertEqual(part_snapshot.company_id, self.company)
        self.assertEqual(
            float_compare(authorization.approved_untaxed_total, 255.0, precision_rounding=currency.rounding),
            0,
        )
        self.assertEqual(
            float_compare(authorization.approved_tax_total, 25.5, precision_rounding=currency.rounding),
            0,
        )
        self.assertEqual(
            float_compare(authorization.approved_total, 280.5, precision_rounding=currency.rounding),
            0,
        )

    def test_source_edits_and_deletion_do_not_change_finalized_snapshot(self):
        repair, quote, part_line, _service_line = self._create_repair_and_quote()
        authorization = self._record_approval(repair)
        snapshot = authorization.line_ids.filtered(lambda line: line.scope_type == "part")
        original = (snapshot.product_name, snapshot.description, snapshot.quantity, snapshot.price_unit, snapshot.discount, snapshot.customer_total)

        part_line.write({"name": "Changed later", "price_unit": 999.0, "discount": 80.0})
        self.assertEqual(
            (snapshot.product_name, snapshot.description, snapshot.quantity, snapshot.price_unit, snapshot.discount, snapshot.customer_total),
            original,
        )
        source_id = part_line.id
        part_line.unlink()
        self.assertFalse(snapshot.source_sale_line_id)
        self.assertEqual(snapshot.source_sale_line_db_id, source_id)
        self.assertEqual(snapshot.customer_total, original[-1])

    def test_capture_requires_customer_evidence_and_explicit_scope_confirmation(self):
        repair, _quote, _part, _service = self._create_repair_and_quote()
        wizard = self._open_capture(repair)
        with self.assertRaisesRegex(ValidationError, "Customer approval evidence is required"):
            wizard.action_record_authorization()
        self.assertFalse(repair.authorization_ids)

        wizard.write({
            "evidence_email": "Please proceed.",
            "evidence_reference": "Email received from the customer for the current estimate.",
        })
        with self.assertRaisesRegex(ValidationError, "Confirm that the evidence"):
            wizard.action_record_authorization()
        self.assertFalse(repair.authorization_ids)

    def test_stale_capture_is_rejected_without_creating_snapshot(self):
        repair, _quote, part_line, _service = self._create_repair_and_quote()
        wizard = self._open_capture(repair)
        wizard.write({
            "evidence_email": "Please proceed with this quotation.",
            "evidence_reference": "Customer response to the estimate revision shown in the form.",
            "evidence_scope_confirmed": True,
        })
        part_line.write({"price_unit": 101.0})
        with self.assertRaisesRegex(UserError, "quotation has changed"):
            wizard.action_record_authorization()
        self.assertFalse(repair.authorization_ids)

    def test_unrelated_quotation_and_customer_mismatch_are_rejected(self):
        repair, _quote, _part, _service = self._create_repair_and_quote()
        unrelated = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "company_id": self.company.id,
        })
        from ..models.authorization import _validated_scope_lines
        with self.assertRaisesRegex(UserError, "does not belong to the selected repair"):
            _validated_scope_lines(repair, unrelated)
        repair.sale_order_id = unrelated
        with self.assertRaisesRegex(UserError, "No identifiable chargeable repair lines"):
            repair.with_user(self.adviser).action_open_original_authorization_capture()

        repair, quote, _part, _service = self._create_repair_and_quote()
        quote.partner_id = self.other_customer
        with self.assertRaisesRegex(ValidationError, "customer does not match"):
            repair.with_user(self.adviser).action_open_original_authorization_capture()

    def test_unmapped_and_cancelled_quotation_lines_are_rejected(self):
        repair, quote, _part, _service = self._create_repair_and_quote()
        quote.order_line.create({
            "order_id": quote.id,
            "product_id": self.part.id,
            "product_uom_qty": 1.0,
            "product_uom_id": self.unit.id,
            "price_unit": 20.0,
            "name": "Unmapped extra work",
        })
        with self.assertRaisesRegex(UserError, "cannot be matched to this repair"):
            repair.with_user(self.adviser).action_open_original_authorization_capture()

        repair, quote, _part, _service = self._create_repair_and_quote()
        quote.action_cancel()
        with self.assertRaisesRegex(UserError, "quotation is cancelled"):
            repair.with_user(self.adviser).action_open_original_authorization_capture()

    def test_confirmed_quotation_alone_is_not_customer_evidence(self):
        repair, quote, _part, _service = self._create_repair_and_quote()
        quote.action_confirm()
        wizard = self._open_capture(repair)
        with self.assertRaisesRegex(ValidationError, "Customer approval evidence is required"):
            wizard.action_record_authorization()
        self.assertFalse(repair.authorization_ids)

    def test_duplicate_capture_is_prevented_and_snapshots_cannot_be_edited_or_deleted(self):
        repair, _quote, _part, _service = self._create_repair_and_quote()
        first_wizard = self._open_capture(repair)
        second_wizard = self._open_capture(repair)
        first_wizard.write({
            "evidence_email": "Please proceed with the estimate.",
            "evidence_reference": "Customer approval email for this repair and quotation.",
            "evidence_scope_confirmed": True,
        })
        first_wizard.action_record_authorization()
        second_wizard.write({
            "evidence_email": "Please proceed with the same estimate.",
            "evidence_reference": "Customer approval email for the same repair and quotation.",
            "evidence_scope_confirmed": True,
        })
        with self.assertRaisesRegex(UserError, "already been recorded"):
            second_wizard.action_record_authorization()
        authorization = repair.authorization_ids
        self.assertEqual(len(authorization), 1)
        with self.assertRaisesRegex(UserError, "cannot be modified"):
            authorization.write({"approved_total": 0.0})
        with self.assertRaisesRegex(UserError, "cannot be modified"):
            authorization.line_ids.write({"quantity": 0.0})
        with self.assertRaisesRegex(UserError, "cannot be deleted"):
            authorization.unlink()
        with self.assertRaisesRegex(UserError, "cannot be deleted"):
            authorization.line_ids.unlink()

    def test_technician_can_view_but_cannot_capture_edit_delete_or_read_evidence(self):
        repair, _quote, _part, _service = self._create_repair_and_quote()
        authorization = self._record_approval(repair)
        technician_repair = repair.with_user(self.technician)
        with self.assertRaises(AccessError):
            technician_repair.action_open_original_authorization_capture()
        self.assertEqual(authorization.with_user(self.technician).read(["name"])[0]["name"], authorization.name)
        self.assertEqual(len(authorization.line_ids.with_user(self.technician)), 2)
        with self.assertRaises(UserError):
            authorization.with_user(self.technician).write({"approved_total": 1.0})
        with self.assertRaises(AccessError):
            authorization.with_user(self.technician).unlink()
        with self.assertRaises(AccessError):
            authorization.with_user(self.technician).read(["evidence_email"])
        with self.assertRaises(AccessError):
            self.env["gt.repair.authorization"].with_user(self.technician).create({"repair_id": repair.id})

    def test_file_evidence_is_stored_on_restricted_snapshot_without_public_attachment(self):
        repair, _quote, _part, _service = self._create_repair_and_quote()
        authorization = self._record_approval(repair, evidence_type="file")
        self.assertEqual(authorization.evidence_filename, "customer-approval.txt")
        self.assertEqual(authorization.evidence_file.content, b"customer approval file")
        self.assertFalse(self.env["ir.attachment"].search_count([
            ("res_model", "=", "gt.repair.authorization"),
            ("res_id", "=", authorization.id),
        ]))
        with self.assertRaises(AccessError):
            authorization.with_user(self.technician).read(["evidence_file"])

    def test_multi_company_isolation_and_cross_company_link_rejection(self):
        company_b = self.env["res.company"].create({
            "name": "Authorization Test Company B",
            "currency_id": self.currency.id,
        })
        self.env.user.write({"company_ids": [Command.link(company_b.id)]})
        warehouse_b = self.env["stock.warehouse"].search([("company_id", "=", company_b.id)], limit=1)
        if not warehouse_b:
            warehouse_b = self.env["stock.warehouse"].create({
                "name": "Authorization Test Warehouse B",
                "code": "ATB",
                "company_id": company_b.id,
            })
        adviser_b = self._create_user("reauth_adviser_b", [
            self.env.ref("gt_repair_reauthorization.group_repair_authorization_adviser"),
            self.env.ref("sales_team.group_sale_salesman"),
            self.env.ref("stock.group_stock_user"),
        ], company_b)
        repair_b, _quote_b, _part_b, _service_b = self._create_repair_and_quote(
            company=company_b,
            customer=self.customer,
            adviser=adviser_b,
        )
        authorization_b = self._record_approval(repair_b, adviser_b)
        self.assertEqual(authorization_b.company_id, company_b)
        self.assertFalse(self.env["gt.repair.authorization"].with_user(self.technician).search([
            ("id", "=", authorization_b.id),
        ]))
        with self.assertRaises((AccessError, MissingError)):
            authorization_b.with_user(self.technician).read(["name"])
        with self.assertRaises(AccessError):
            authorization_b.with_user(self.technician).read(["evidence_email"])

        repair_a, _quote_a, _part_a, _service_a = self._create_repair_and_quote()
        with self.assertRaises(UserError):
            repair_a.write({"sale_order_id": repair_b.sale_order_id.id})

    def test_manager_can_review_but_cannot_override_immutability(self):
        repair, _quote, _part, _service = self._create_repair_and_quote(adviser=self.manager)
        authorization = self._record_approval(repair, self.manager)
        self.assertEqual(authorization.with_user(self.manager).read(["name"])[0]["name"], authorization.name)
        with self.assertRaises(UserError):
            authorization.with_user(self.manager).write({"approved_total": 0.0})

