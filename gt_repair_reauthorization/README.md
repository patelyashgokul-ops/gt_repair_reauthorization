# Repair Reauthorization

**Golok Technology** — Phase 1 foundation for Odoo 20 Community.

This add-on records a customer's original approval of chargeable work linked to a Repair quotation. An adviser records the approval evidence, verifies the customer, repair, quotation revision, and displayed scope, and creates a frozen copy of the linked quotation lines and amounts.

## Phase 1 scope

- Repair Parts and Services are included only when their Odoo 20 quotation lines map directly to that repair through the originating Repair line, a linked `stock.move`, or a linked `repair.service.line`.
- A shared quotation can serve multiple repairs. Each authorization copies only lines owned by its repair; lines that cannot be attributed safely block capture.
- The original quotation can be draft, sent, or confirmed, but a confirmed quotation alone is not customer evidence. Cancelled quotations and direct invoices without a linked quotation cannot be captured.
- Email text or a file up to 10 MB is stored on the protected authorization record. Evidence is not posted to Chatter or stored as a public attachment. Access to evidence fields is limited to advisers and managers.
- Finalized authorization headers and lines reject ORM edits, copies, and deletion. Corrections and revised approvals are future-phase work.

This records the staff member's report of customer approval; it does not authenticate the sender's identity or provide a digital signature. A manager's authority is not customer consent.

## Access roles

- **Repair Authorization Technician / Viewer:** view authorized repair snapshots and lines; no evidence-field access or capture rights.
- **Repair Authorization Service Adviser:** open the capture form for repairs and quotations permitted by native Odoo access; record approval evidence.
- **Repair Authorization Manager:** review within allowed companies; no historical edit or force-release permission.

Company filtering is enforced by Odoo 20 `ir.access` domains and company-consistent ORM relationships.

## Development checks

The development test class is `tests/test_authorization_capture.py`. Against an Odoo 20 Community checkout with this add-on on `addons_path`, the focused suite can be run using:

```powershell
python odoo-bin -c odoo.conf -d <disposable_test_database> -i gt_repair_reauthorization --stop-after-init --test-enable --test-tags /gt_repair_reauthorization --without-demo=all
```

This is a Phase 1 development foundation. Revised-scope comparisons, partial/declined decisions, execution holds, the management dashboard, and commercial release packaging are not included.

## Commercial metadata

- Publisher: Golok Technology
- Target price: USD 49.00
- Proposed license: OPL-1, subject to final commercial and dependency-license review
