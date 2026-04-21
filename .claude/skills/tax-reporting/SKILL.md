---
name: tax-reporting
description: Generates a Form-8949-ready CSV from the trade ledger, including wash-sale flags and ST/LT classification. Use whenever taxes, realized P&L, 1099-B reconciliation, or "what will I owe" is mentioned. Informational only.
---

# Tax reporting (Form 8949 export)

> **Informational only — not tax advice.** File with a qualified preparer.

## Steps

1. **Choose lot method.** `FIFO` (default) or `HIFO`.
2. **Run.** `uv run python -m src.tax.report --year <YYYY> --method <FIFO|HIFO> --out reports/form_8949_<YYYY>.csv`.
3. **Verify.** Open the CSV. Confirm: every realized lot has both `acquired_date` and `sold_date`; wash-sale dollars are populated where applicable; ST/LT split is sensible.
4. **Reconcile** against the broker 1099-B when issued. Reasonable mismatches: rounding, lot-method differences. Unreasonable: missing trades. Investigate.

## Columns
`symbol, acquired_date, sold_date, proceeds, cost_basis, wash_sale_loss_disallowed, gain_loss, term`.

## Caveats
- Wash-sale = sell-at-loss within 30 days of a same-symbol buy.
- Long-term = held > 1 year (366 days inclusive of acquisition).
- Options on the same underlying as wash-saled equity may also trigger — flag and let the operator decide.
