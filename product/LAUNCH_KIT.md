# Launch kit – everything is ready except your accounts and details

Anything in `[SQUARE BRACKETS]` is yours to fill in. Nothing here has been published, and no sales are guaranteed.

## 1. What to upload (in `deliverables/`)
| Product | File | Suggested price | Cover image (`images/`) |
|---|---|---|---|
| UK Sole Trader Tax Tracker | `tracker.zip` | £9.99 | `tracker_1_cover.png` + `tracker_2_quarterly.png`, `tracker_3_tax.png` |
| UK Freelancer Invoice Pack | `invoice.zip` | £6.99 | `invoice_1_cover.png` + `invoice_2_invoice.png`, `invoice_3_log.png` |
| Starter Bundle (both + guide) | `bundle.zip` | £13.99 | `bundle_cover.png` (+ any of the above) |

Prices follow the £5–10 range I saw competitors using; adjust freely. Do NOT upload the `*_SAMPLE.xlsx` files – they are only for screenshots.

## 2. Accounts to set up (only you can do this)
- **Payhip or Gumroad** (fastest, no per-listing fee): create account → connect PayPal/Stripe for payouts → add each product, upload the zip as the file.
- **Etsy** (built-in shoppers, but £0.15 listing fee per item and it takes time to get found): open shop → "Digital" listing type.
- Check each platform's rules on UK VAT/tax for digital sales, and register as self-employed with HMRC if you haven't (selling for profit counts). [YOUR TAX POSITION]

## 3. Tracker listing copy
**Title:** UK Sole Trader Tax Tracker 2026/27 – Income & Expenses Spreadsheet with MTD Quarterly Summary & Tax Estimate (Excel + Google Sheets)

**Description:**
Know roughly what you owe HMRC before January – and have your Making Tax Digital quarterly figures ready in seconds.

Log your income and expenses once. The workbook does the rest:
- Income and Expenses logs (1,000 rows each) with auto-assigned tax quarters
- Quarterly Summary: income, expenses by category and profit for each quarter (6 Apr–5 Jul, 6 Jul–5 Oct, 6 Oct–5 Jan, 6 Jan–5 Apr)
- Tax Estimate: Income Tax + Class 4 NIC, personal allowance taper, trading allowance, plus a "set aside per month" figure
- Drop-down expense categories, a check for entries outside the tax year, editable rates on a Settings sheet
- PDF user guide included. Works in Excel and Google Sheets. No macros, no subscription.

Please note: England/Wales/NI rates (Scottish rates differ). Estimates for planning only – not tax advice. It does not submit to HMRC; use HMRC-recognised software for MTD submissions.

**Etsy tags (13):** sole trader spreadsheet, self assessment, uk tax tracker, MTD spreadsheet, freelancer budget, income expense tracker, self employed uk, bookkeeping template, tax estimate, excel template, google sheets template, small business uk, making tax digital

## 4. Invoice pack listing copy
**Title:** UK Freelancer Invoice Template Pack – Printable Invoice with Overdue Payment Tracker (Excel + Google Sheets)

**Description:**
Send professional invoices and always know who still owes you money.
- One-time setup of your business and bank details
- Printable invoice: 10 line items, auto totals, optional VAT, automatic due date
- Invoice Log: status updates itself (Paid / Awaiting / OVERDUE), days overdue, outstanding totals
- Works in Excel and Google Sheets. No macros.

**Etsy tags:** invoice template uk, freelancer invoice, excel invoice, google sheets invoice, invoice tracker, self employed invoice, small business invoice, printable invoice, vat invoice template, overdue invoice tracker, sole trader invoice, invoice spreadsheet, freelance template

## 5. Bundle listing
**Title:** Self-Employed Starter Bundle UK – Tax Tracker + Invoice Pack (Excel & Google Sheets)
**Description:** Both templates above plus the PDF guide, at a lower price than buying separately. Use the two descriptions above.

## 6. Landing page
`landing.html` is a one-page site. Replace the `[..._BUY_LINK]` and `[..._PRICE]` placeholders and host it free on GitHub Pages / Netlify, or skip it and just use the platform listing.

## 7. Posts (edit to sound like you; follow each community's self-promotion rules)
**Reddit / forum (value-first, no hard sell):**
> Self-employed in the UK? I put together a simple spreadsheet that splits income/expenses into the MTD quarters and estimates income tax + Class 4 NIC. It's an estimate only (England/Wales/NI rates). Happy to answer questions about how the quarters work – and if it's useful, it's £[PRICE] here: [LINK]

**X / LinkedIn:**
> Self-employed in the UK? Quarterly MTD updates are coming for more people. I built a spreadsheet that totals each quarter and estimates your tax, so January isn't a shock. Excel + Google Sheets. [LINK] #selfemployed #sidehustle #UKtax

**Facebook groups:** use the Reddit text; ask admins before posting links.

## 8. Do this before going live
- [ ] Confirm the tax figures against GOV.UK (rates are on the Settings sheet and in the guide).
- [ ] Open each xlsx in Excel and Google Sheets once yourself.
- [ ] Add support email, refund policy and your name to the guide and landing page.
- [ ] Don't invent reviews or "only X left" urgency – it breaks platform rules.

## 9. Realistic expectations
Cold start listings often make zero to a handful of sales in the first week. Income depends on traffic, not on the files. The first £100 more likely takes weeks of consistent posting than one night.

## 10. Regenerating
`python3 build.py`, `python3 build_invoice.py` (add `sample` for sample files), `python3 make_images.py && node shot.js`, `node pdf.js`.
