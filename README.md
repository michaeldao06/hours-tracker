# hours-tracker

A single-file terminal tool for anyone who bills by the hour. `log_hours.py` keeps a plain-text log of your daily hours, shows a dashboard with weekly totals, streaks and a consistency grid, and generates PDF invoices for any date range.

It uses only the Python standard library.

## Requirements

- Python 3.8 or newer
- Google Chrome, Chromium or Microsoft Edge (optional, used for PDF export; without one the invoice is saved as HTML for you to print to PDF yourself)
- A terminal with UTF-8 support (the dashboard uses block-drawing characters)

## Quick start

```
git clone https://github.com/michaeldao06/hours-tracker.git
cd hours-tracker
python3 log_hours.py
```

The first run walks you through setup (your name, client, rate, work days and so on) and saves the answers to `config.json` next to the script. Then the main menu appears. Change settings at any time with:

```
python3 log_hours.py setup
```

`config.example.json` shows every setting with sample values.

## Commands

| Command | What it does |
|---|---|
| `python3 log_hours.py` | Interactive menu |
| `python3 log_hours.py log` | Log (or overwrite) today's hours |
| `python3 log_hours.py edit` | Pick a previous entry and change it |
| `python3 log_hours.py stats` | Dashboard (`dash` and `dashboard` also work) |
| `python3 log_hours.py invoice` | Generate an invoice for a date range (`inv` also works) |
| `python3 log_hours.py setup` | Create or change `config.json` |

## The hours log

Entries live in `hours.txt` next to the script. It is created on first run, and it is plain text you can edit by hand:

```
ACME WEBSITE — HOURS LOG
Jane | Summer 2026 | Acme Corp
==========================================

WEEK 1 — June 1–5, 2026
------------------------------------------
Mon Jun 1 | 9am-12pm | 3.0 hrs | Set up the project and CI
Wed Jun 3 | 1pm-3:30pm | 2.5 hrs | Built the login page
Week 1 Total: 5.5 hrs

==========================================
RUNNING TOTAL: 5.5 hrs
```

Each entry is `Day Mon D | time range | X.X hrs | description`. A new week block is added automatically the first time you log in a new week. The script recalculates the `Week N Total:` and `RUNNING TOTAL:` lines whenever it saves, so if you edit the file by hand keep those lines and the `WEEK N — Month D–D, YYYY` headers intact.

## Configuration

All keys in `config.json` are written by `setup`:

| Key | Default | Purpose |
|---|---|---|
| `contractor_name` | required | Your name, shown on invoices and used in the PDF file name |
| `contractor_title` | `Independent Contractor` | Shown under your name on invoices |
| `address_line1`, `address_line2` | blank | Your address on invoices; blank lines are omitted |
| `client_name` | required | Who the invoice is billed to |
| `client_attn`, `client_email`, `client_phone` | blank | Extra "Bill To" lines; blank lines are omitted |
| `project_name` | required | Used in the dashboard title and the log header |
| `period_label` | blank | For example `Summer 2026`; shown in the dashboard subtitle |
| `work_days` | `Mon` to `Fri` | Days that count for streaks, the dashboard grids and week header ranges |
| `service_description`, `engagement_description` | see example | The "For" lines on invoices |
| `hourly_rate` | required | Your rate |
| `currency_symbol` | `$` | Placed before amounts |
| `payment_method`, `payment_terms` | see example | The "Payment" lines on invoices |
| `invoice_footer` | see example | Closing line on invoices |
| `next_invoice_number` | `1` | First invoice number, used until the ledger has entries |
| `invoices_dir` | `Invoices` | Where invoices and the ledger go; relative paths are resolved against the script folder and `~` is expanded |
| `chrome_path` | `null` | Path to a Chrome, Chromium or Edge executable; `null` auto-detects |

Setup keeps the current value when you press Enter, so to blank an optional field edit `config.json` directly. If you point `invoices_dir` at another folder inside the repository, add it to `.gitignore`.

## Invoices

`invoice` asks for a billing period (defaulting to the day after your last invoice through today), lists the matching entries, and writes `Invoice_NNN_Your_Name.pdf` to the invoices folder along with `invoices.json`, a ledger of every invoice (number, dates, hours, total, file name). Numbers continue from the highest number found in the ledger or the folder. Running `invoice` again for the same period as the latest invoice reuses its number, so you can regenerate one.

If no Chromium-based browser is found, the invoice is saved as HTML and opened in your default browser; use Print, then Save as PDF.

## Notes

- Weeks start on Monday. Week headers span your first to last configured work day. Hours logged on other days still count toward totals and invoices but do not appear in the CONSISTENCY and DAY POWER grids.
- Day and month names are in English.
- Set `NO_COLOR=1` to disable colors, or `FORCE_COLOR=1` to keep them when piping output.

## License

MIT, see `LICENSE`.
