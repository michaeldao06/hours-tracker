#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, date, timedelta
from html import escape
from pathlib import Path
from string import Template

SCRIPT_DIR = Path(__file__).resolve().parent
HOURS_FILE = SCRIPT_DIR / "hours.txt"
CONFIG_FILE = SCRIPT_DIR / "config.json"
DAY_PAT = re.compile(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w+\s+\d+\s*\|')
RULE = "------------------------------------------"
WEEK_HEADER_PAT = re.compile(r'WEEK\s+(\d+)\s+—\s+(.+?),\s*(\d{4})')

WIDTH = 78
USE_COLOR = bool(
    (sys.stdout.isatty() or os.environ.get('FORCE_COLOR'))
    and not os.environ.get('NO_COLOR')
)
TRUECOLOR = os.environ.get('COLORTERM', '') in ('truecolor', '24bit')
GRADIENT = ((0, 229, 255), (150, 110, 255), (255, 64, 200))
SPARK = '▁▂▃▄▅▆▇█'

FONT = {
    '0': ("███", "█ █", "█ █", "█ █", "███"),
    '1': (" █ ", "██ ", " █ ", " █ ", "███"),
    '2': ("███", "  █", "███", "█  ", "███"),
    '3': ("███", "  █", " ██", "  █", "███"),
    '4': ("█ █", "█ █", "███", "  █", "  █"),
    '5': ("███", "█  ", "███", "  █", "███"),
    '6': ("███", "█  ", "███", "█ █", "███"),
    '7': ("███", "  █", "  █", "  █", "  █"),
    '8': ("███", "█ █", "███", "█ █", "███"),
    '9': ("███", "█ █", "███", "  █", "███"),
    '.': (" ", " ", " ", " ", "█"),
}
HEAT_GLYPHS = ('░░', '▒▒', '▓▓', '██')

# ── User configuration (config.json, written by `setup`) ──
DAY_NAMES = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
REQUIRED = ('contractor_name', 'client_name', 'project_name', 'hourly_rate')
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

CONFIG_FIELDS = (
    ('CONTRACTOR', (
        ('contractor_name', 'Your name', ''),
        ('contractor_title', 'Your title', 'Independent Contractor'),
        ('address_line1', 'Address line 1 (blank to omit)', ''),
        ('address_line2', 'Address line 2 (blank to omit)', ''),
    )),
    ('CLIENT', (
        ('client_name', 'Client name', ''),
        ('client_attn', 'Client contact, e.g. Attn: Jane Smith, CFO (blank to omit)', ''),
        ('client_email', 'Client email (blank to omit)', ''),
        ('client_phone', 'Client phone (blank to omit)', ''),
    )),
    ('PROJECT', (
        ('project_name', 'Project name (used in titles)', ''),
        ('period_label', 'Period label, e.g. Summer 2026 (blank to omit)', ''),
        ('work_days', 'Work days (comma-separated)', ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']),
    )),
    ('INVOICE', (
        ('service_description', 'Service description', 'Software development services'),
        ('engagement_description', 'Engagement description (blank to omit)', ''),
        ('hourly_rate', 'Hourly rate', None),
        ('currency_symbol', 'Currency symbol', '$'),
        ('payment_method', 'Payment method line',
         'Preferred payment method: Direct deposit. '
         'Bank account details provided separately upon request.'),
        ('payment_terms', 'Payment terms line', 'Terms: Due upon receipt'),
        ('invoice_footer', 'Invoice footer',
         'Thank you! Please reach out with any questions about this invoice.'),
        ('next_invoice_number', 'Next invoice number (used until the ledger has entries)', 1),
    )),
    ('FILES', (
        ('invoices_dir', 'Invoices folder (relative to the script folder)', 'Invoices'),
        ('chrome_path', 'Chrome/Chromium/Edge executable (blank = auto-detect)', None),
    )),
)
DEFAULTS = {key: default for _, fields in CONFIG_FIELDS for key, _, default in fields}
CFG = {}


def _c256(r, g, b):
    def q(v):
        return int(round(v / 255 * 5))
    return 16 + 36 * q(r) + 6 * q(g) + q(b)


def fg(rgb):
    if not USE_COLOR:
        return ''
    r, g, b = rgb
    if TRUECOLOR:
        return f"\033[38;2;{r};{g};{b}m"
    return f"\033[38;5;{_c256(r, g, b)}m"


def sgr(code):
    return f"\033[{code}m" if USE_COLOR else ''


RESET = sgr('0')
BOLD = sgr('1')
DIM = sgr('2')


def grad(t):
    t = max(0.0, min(1.0, t))
    seg = t * (len(GRADIENT) - 1)
    i = min(int(seg), len(GRADIENT) - 2)
    f = seg - i
    a, b = GRADIENT[i], GRADIENT[i + 1]
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def gradient_text(text, bold=False):
    if not USE_COLOR:
        return text
    n = max(len(text) - 1, 1)
    prefix = BOLD if bold else ''
    out = [prefix]
    for i, ch in enumerate(text):
        out.append(fg(grad(i / n)) + ch)
    out.append(RESET)
    return ''.join(out)


def gradient_rule(char='━'):
    if not USE_COLOR:
        return char * WIDTH
    n = WIDTH - 1
    return ''.join(fg(grad(i / n)) + char for i in range(WIDTH)) + RESET


def section(title):
    plain = f"─── {title} "
    dashes = '─' * max(WIDTH - len(plain), 0)
    return f"{DIM}───{RESET} {fg(grad(0.1))}{BOLD}{title}{RESET} {DIM}{dashes}{RESET}"


def render_bar(value, vmax, width):
    chars = ''
    if vmax > 0 and value > 0:
        cells = max(int(min(value / vmax, 1.0) * width + 0.5), 1)
        chars = '▆' * cells
    track = '·' * (width - len(chars))
    if not USE_COLOR:
        return chars + track
    n = max(width - 1, 1)
    bar = ''.join(fg(grad(i / n)) + ch for i, ch in enumerate(chars))
    return bar + RESET + DIM + track + RESET


def render_big(text):
    rows = ['', '', '', '', '']
    for ch in text:
        glyph = FONT.get(ch)
        if not glyph:
            continue
        for r in range(5):
            rows[r] += ''.join('██' if c == '█' else '  ' for c in glyph[r]) + '  '
    rows = [r[:-2] for r in rows]
    w = max(len(r) for r in rows)
    pad = ' ' * max((WIDTH - w) // 2, 0)
    n = max(w - 1, 1)
    out = []
    for row in rows:
        if USE_COLOR:
            line = ''.join(
                fg(grad(i / n)) + c if c == '█' else c for i, c in enumerate(row)
            ) + RESET
        else:
            line = row
        out.append(pad + line.rstrip() if not USE_COLOR else pad + line)
    return '\n'.join(out)


def heat_cell(hours):
    if hours <= 0:
        return DIM + '··' + RESET
    level = 1 if hours <= 1.5 else 2 if hours <= 2.5 else 3 if hours <= 3.5 else 4
    if USE_COLOR:
        return fg(grad(level / 4.0)) + '██' + RESET
    return HEAT_GLYPHS[level - 1]


def sparkline(values):
    vmax = max(values) if values else 0
    if vmax <= 0:
        return ''
    out = []
    for v in values:
        idx = min(int(v / vmax * (len(SPARK) - 1) + 0.5), len(SPARK) - 1)
        out.append(fg(grad(v / vmax)) + SPARK[idx])
    return ''.join(out) + RESET


def parse_last_week(lines):
    """Return (week_num, end_date) for the last WEEK header, or None."""
    for line in reversed(lines):
        m = WEEK_HEADER_PAT.search(line)
        if not m:
            continue
        num = int(m.group(1))
        year = int(m.group(3))
        start_tok, _, end_tok = m.group(2).partition('–')
        start_tok, end_tok = start_tok.strip(), end_tok.strip()
        start_month = datetime.strptime(start_tok.split()[0], '%B').month
        end_parts = end_tok.split()
        if len(end_parts) == 2:
            end_month = datetime.strptime(end_parts[0], '%B').month
            end_day = int(end_parts[1])
        else:
            end_month = start_month
            end_day = int(end_parts[0])
        return num, date(year, end_month, end_day)
    return None


def parse_hours(line):
    m = re.search(r'\|\s*([\d.]+)\s*hrs', line)
    return float(m.group(1)) if m else None


def parse_entry_fields(line):
    """Return (time_range, hours_str, description) from a filled entry line."""
    parts = [p.strip() for p in line.strip().split('|')]
    if len(parts) >= 4:
        # New format: date | time_range | X.X hrs | description
        hours = re.sub(r'\s*hrs$', '', parts[2]).strip()
        return parts[1], hours, ' | '.join(parts[3:])
    elif len(parts) == 3:
        # Old format: date | X.X hrs | description
        hours = re.sub(r'\s*hrs$', '', parts[1]).strip()
        return '', hours, parts[2]
    return '', '', ''


def filled_entries(lines):
    """Return list of (line_index, stripped_line) for all filled day entries."""
    result = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if DAY_PAT.match(stripped):
            parts = stripped.split('|', 1)
            if len(parts) > 1 and parts[1].strip():
                result.append((i, stripped))
    return result


def collect_stats(lines):
    """Parse entries and per-week aggregates from the raw file lines."""
    entries = []
    weeks = {}
    cur_week = None
    cur_year = None
    for line in lines:
        m = WEEK_HEADER_PAT.search(line)
        if m:
            cur_week = int(m.group(1))
            cur_year = int(m.group(3))
            weeks[cur_week] = {'range': m.group(2), 'hours': 0.0, 'days': 0}
            continue
        stripped = line.strip()
        if not DAY_PAT.match(stripped):
            continue
        parts = stripped.split('|', 1)
        if len(parts) < 2 or not parts[1].strip():
            continue
        if cur_week is None:
            continue
        time_range, hours_str, desc = parse_entry_fields(stripped)
        try:
            hours = float(hours_str)
        except ValueError:
            continue
        tok = stripped.split('|')[0].split()
        entry_date = None
        if cur_year is not None and len(tok) >= 3:
            try:
                entry_date = datetime.strptime(
                    f"{tok[1]} {tok[2]} {cur_year}", "%b %d %Y"
                ).date()
            except ValueError:
                pass
        entries.append({
            'date': entry_date,
            'week': cur_week,
            'hours': hours,
            'desc': desc,
            'time': time_range,
            'label': ' '.join(tok),
        })
        if cur_week in weeks:
            weeks[cur_week]['hours'] += hours
            weeks[cur_week]['days'] += 1
    return entries, weeks


def next_business_day(d):
    nd = d + timedelta(days=1)
    while DAY_NAMES[nd.weekday()] not in CFG['work_days']:
        nd += timedelta(days=1)
    return nd


def business_streaks(dates):
    """Return (longest_streak, streak_ending_at_latest_entry) in workdays."""
    days = sorted(set(d for d in dates if d))
    if not days:
        return 0, 0
    longest = run = 1
    prev = days[0]
    for d in days[1:]:
        run = run + 1 if d == next_business_day(prev) else 1
        longest = max(longest, run)
        prev = d
    return longest, run


def show_dashboard(lines):
    entries, weeks = collect_stats(lines)
    if not entries:
        print("No entries yet. Run `log_hours.py log` to add one.")
        return

    total = sum(e['hours'] for e in entries)
    week_nums = sorted(weeks)
    weekly_hours = [weeks[n]['hours'] for n in week_nums]
    best_week = max(weekly_hours)
    best_entry = max(entries, key=lambda e: e['hours'])
    avg_week = total / len(week_nums)
    longest, current = business_streaks([e['date'] for e in entries])
    last_date = max((e['date'] for e in entries if e['date']), default=None)
    if last_date and date.today() > next_business_day(last_date):
        current = 0

    print()
    print(gradient_rule())
    title = ' '.join(f"{CFG['project_name']} hours".upper())
    if len(title) > WIDTH:
        title = f"{CFG['project_name']} hours".upper()
    subtitle = ' · '.join(p for p in (
        CFG['contractor_name'].split(' ', 1)[0], CFG['period_label'], CFG['client_name']
    ) if p)
    print(gradient_text(title.center(WIDTH).rstrip(), bold=True))
    print(DIM + subtitle.center(WIDTH).rstrip() + RESET)
    print(gradient_rule())
    print()
    print(render_big(f"{total:.1f}"))
    print(DIM + 'TOTAL HOURS'.center(WIDTH).rstrip() + RESET)
    print()

    tiles = [
        (f"{len(entries)}", 'days logged'),
        (f"{current}d", 'streak'),
        (f"{longest}d", 'best run'),
        (f"{avg_week:.1f}h", 'avg/week'),
        (f"{best_entry['hours']:.1f}h", 'best day'),
        (f"{best_week:.1f}h", 'best week'),
    ]
    col = WIDTH // len(tiles)
    values = ''.join(
        (BOLD + fg(grad(i / (len(tiles) - 1))) + t[0] + RESET).ljust(
            col + len(BOLD + fg(grad(i / (len(tiles) - 1))) + RESET)
        )
        for i, t in enumerate(tiles)
    )
    labels = ''.join((DIM + t[1] + RESET).ljust(col + len(DIM + RESET)) for t in tiles)
    print(('  ' + values).rstrip())
    print(('  ' + labels).rstrip())
    print()

    print(section('WEEKLY'))
    range_w = max(max(len(weeks[n]['range']) for n in week_nums), 14)
    bar_w = min(40, WIDTH - range_w - 15)
    this_week = week_nums[-1]
    for n in week_nums:
        w = weeks[n]
        marker = fg(grad(0.9)) + '▸' + RESET if n == this_week else ' '
        label = f"W{n:<3}{w['range']:<{range_w}}"
        label = (BOLD + label + RESET) if n == this_week else (DIM + label + RESET)
        bar = render_bar(w['hours'], best_week, bar_w)
        print(f" {marker} {label} {bar} {BOLD}{w['hours']:>4.1f}{RESET}")
    print()

    print(section('CONSISTENCY'))
    work_days = [(DAY_NAMES.index(n), n) for n in CFG['work_days']]
    grid = {}
    for e in entries:
        if e['date'] and e['week'] is not None:
            key = (e['date'].weekday(), e['week'])
            grid[key] = grid.get(key, 0.0) + e['hours']
    header = '        ' + ' '.join(f"{n:>2}" for n in week_nums)
    print(DIM + header + RESET)
    for wd, name in work_days:
        cells = [heat_cell(grid.get((wd, n), 0.0)) for n in week_nums]
        print(f"   {DIM}{name}{RESET}  " + ' '.join(cells))
    legend = f"   {heat_cell(0)} 0h  " + '  '.join(
        f"{heat_cell(lvl)} {lvl}h{'+' if lvl == 4 else ''}" for lvl in (1, 2, 3, 4)
    )
    print(legend)
    print()

    print(section('DAY POWER'))
    by_wd = {}
    for e in entries:
        if e['date']:
            by_wd[e['date'].weekday()] = by_wd.get(e['date'].weekday(), 0.0) + e['hours']
    wd_max = max(by_wd.values()) if by_wd else 0
    for wd, name in work_days:
        h = by_wd.get(wd, 0.0)
        bar = render_bar(h, wd_max, 30)
        print(f"   {DIM}{name}{RESET}  {bar} {BOLD}{h:>4.1f}{RESET}")
    print()

    print(section('RECENT'))
    for e in entries[-3:][::-1]:
        desc = e['desc']
        room = WIDTH - 22
        if len(desc) > room:
            cut = desc[:room - 1]
            if ' ' in cut:
                cut = cut.rsplit(' ', 1)[0]
            desc = cut + '…'
        print(
            f"   {fg(grad(0.2))}{e['label']:<11}{RESET}"
            f"{BOLD}{e['hours']:>4.1f}h{RESET}  {DIM}{desc}{RESET}"
        )
    print()
    print(f"   {DIM}trend{RESET}  " + sparkline(weekly_hours))
    print()
    print(DIM + 'log_hours.py  log · edit · stats · invoice · setup'.center(WIDTH).rstrip() + RESET)
    print()


def save_summary(lines, idx):
    entries, weeks = collect_stats(lines)
    total = sum(e['hours'] for e in entries)
    week_nums = sorted(weeks)
    weekly_hours = [weeks[n]['hours'] for n in week_nums]
    week_num = None
    for i in range(idx, -1, -1):
        m = re.match(r'\s*WEEK\s+(\d+)', lines[i])
        if m:
            week_num = int(m.group(1))
            break
    print()
    print(f" {fg(grad(0.0))}{BOLD}✓ Saved{RESET}  {lines[idx].strip()}")
    if week_num in weeks:
        print(
            f"   {DIM}Week {week_num}:{RESET} {BOLD}{weeks[week_num]['hours']:.1f}h{RESET}"
            f"   {DIM}·{RESET}   {DIM}Total:{RESET} {BOLD}{total:.1f}h{RESET}"
            f"   {sparkline(weekly_hours)}"
        )
    print()


def day_prefix(dt):
    return f"{dt.strftime('%a %b')} {dt.day}"


def format_week_range(start, end):
    if start.month == end.month:
        return f"{start.strftime('%B')} {start.day}–{end.day}, {start.year}"
    return f"{start.strftime('%B')} {start.day}–{end.strftime('%B')} {end.day}, {start.year}"


def update_file(lines, idx):
    """Recompute week total and running total, then write the file."""
    week_start = next(
        (i for i in range(idx, -1, -1) if re.match(r'\s*WEEK\s+\d+', lines[i])),
        None,
    )
    week_total_idx = next(
        (i for i in range(idx, len(lines)) if re.match(r'\s*Week\s+\d+\s+Total:', lines[i])),
        None,
    )

    if week_start is not None and week_total_idx is not None:
        week_hours = sum(
            h for i in range(week_start, week_total_idx)
            if (h := parse_hours(lines[i])) is not None
        )
        lines[week_total_idx] = re.sub(
            r'(Week\s+\d+\s+Total:).*',
            rf'\1 {week_hours:.1f} hrs',
            lines[week_total_idx],
        )

    running_total = 0.0
    running_total_idx = None
    for i, line in enumerate(lines):
        m = re.match(r'\s*Week\s+\d+\s+Total:\s*([\d.]+)', line)
        if m:
            running_total += float(m.group(1))
        if re.match(r'\s*RUNNING TOTAL:', line):
            running_total_idx = i

    if running_total_idx is not None:
        lines[running_total_idx] = re.sub(
            r'(RUNNING TOTAL:).*',
            rf'\1 {running_total:.1f} hrs',
            lines[running_total_idx],
        )

    HOURS_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def ask(prompt, default):
    tag = f"{fg(grad(0.5))}◆{RESET} "
    hint = f" {DIM}[{default}]{RESET}" if default else ''
    val = input(f"{tag}{prompt}{hint}: ").strip()
    return val if val else default


def prompt_fields(cur_time='', cur_hours='', cur_desc=''):
    """Prompt for entry fields, showing current values as defaults."""
    hours_str = ask("Hours worked (e.g. 3.0)", cur_hours)
    try:
        hours_val = float(hours_str)
    except ValueError:
        print(f"Invalid hours: {hours_str}")
        sys.exit(1)

    time_range = ask("Time range (e.g. 9am-5pm)", cur_time)
    description = ask("What did you work on?", cur_desc)
    return hours_val, time_range, description


def log_today(lines):
    today = datetime.today()
    prefix = day_prefix(today)

    idx = None
    for i, line in enumerate(lines):
        if re.match(rf'^{re.escape(prefix)}[\s|]', line.strip()):
            idx = i
            break

    if idx is None:
        print(f"No entry found for {prefix}. Creating a new entry.")
        hours_val, time_range, description = prompt_fields()
        new_line = f"{prefix} | {time_range} | {hours_val:.1f} hrs | {description}"

        last_week = parse_last_week(lines)
        last_total_idx = next(
            (i for i in range(len(lines) - 1, -1, -1) if re.match(r'\s*Week\s+\d+\s+Total:', lines[i])),
            None,
        )
        monday = today.date() - timedelta(days=today.weekday())

        if last_week is None or monday > last_week[1]:
            num = last_week[0] + 1 if last_week else 1
            offsets = [DAY_NAMES.index(d) for d in CFG['work_days']]
            first = monday + timedelta(days=offsets[0])
            last = monday + timedelta(days=offsets[-1])
            block = [
                f"WEEK {num} — {format_week_range(first, last)}",
                RULE,
                new_line,
                f"Week {num} Total: 0.0 hrs",
            ]
            if last_total_idx is None:
                at = next(
                    (i for i in range(len(lines) - 1, -1, -1) if lines[i].startswith('=')),
                    len(lines),
                )
                block.append("")
            else:
                at = last_total_idx + 1
                block[:0] = ["", RULE, ""]
            lines[at:at] = block
            entry_idx = at + block.index(new_line)
            update_file(lines, entry_idx)
            save_summary(lines, entry_idx)
        elif last_total_idx is None:
            print("Could not find a Week Total line to insert before.")
            sys.exit(1)
        else:
            lines.insert(last_total_idx, new_line)
            update_file(lines, last_total_idx)
            save_summary(lines, last_total_idx)
        return

    parts = lines[idx].strip().split('|', 1)
    if len(parts) > 1 and parts[1].strip():
        print(f"Entry already exists: {lines[idx].strip()}")
        if input("Overwrite? (y/n): ").strip().lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    hours_val, time_range, description = prompt_fields()
    indent = len(lines[idx]) - len(lines[idx].lstrip())
    lines[idx] = f"{' ' * indent}{prefix} | {time_range} | {hours_val:.1f} hrs | {description}"

    update_file(lines, idx)
    save_summary(lines, idx)


def edit_previous(lines):
    entries = filled_entries(lines)

    if not entries:
        print("No filled entries found to edit.")
        sys.exit(0)

    print("Previous entries:")
    for n, (_, text) in enumerate(entries, 1):
        print(f"  {DIM}{n:>2}.{RESET} {text}")

    raw = input(f"\nSelect entry (1-{len(entries)}): ").strip()
    try:
        choice = int(raw)
        if not 1 <= choice <= len(entries):
            raise ValueError
    except ValueError:
        print("Invalid selection.")
        sys.exit(1)

    idx, text = entries[choice - 1]
    cur_time, cur_hours, cur_desc = parse_entry_fields(text)
    prefix = text.split('|')[0].strip()

    print(f"\nEditing: {text}")
    print("Press Enter to keep the current value.\n")

    hours_val, time_range, description = prompt_fields(cur_time, cur_hours, cur_desc)
    indent = len(lines[idx]) - len(lines[idx].lstrip())
    lines[idx] = f"{' ' * indent}{prefix} | {time_range} | {hours_val:.1f} hrs | {description}"

    update_file(lines, idx)
    save_summary(lines, idx)


def parse_user_date(text):
    text = text.strip()
    year = date.today().year
    for fmt in ('%m/%d', '%b %d', '%B %d'):
        try:
            return datetime.strptime(f"{text} {year}", f"{fmt} %Y").date()
        except ValueError:
            continue
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def ask_date(prompt, default_date):
    default = f"{default_date.strftime('%b')} {default_date.day}"
    while True:
        raw = ask(prompt, default)
        parsed = parse_user_date(raw)
        if parsed:
            return parsed
        print(f"  {DIM}Could not parse '{raw}' — try e.g. Aug 8, 8/8, or 2026-08-08{RESET}")


def format_full_date(d):
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def format_period(start, end):
    if start.year == end.year:
        return f"{start.strftime('%B')} {start.day} – {end.strftime('%B')} {end.day}, {end.year}"
    return f"{format_full_date(start)} – {format_full_date(end)}"


def invoices_dir():
    return (SCRIPT_DIR / Path(CFG['invoices_dir']).expanduser()).resolve()


def name_slug():
    return re.sub(r'\W+', '_', CFG['contractor_name']).strip('_')


def money(x):
    return f"{CFG['currency_symbol']}{x:,.2f}"


def load_ledger():
    try:
        return json.loads((invoices_dir() / "invoices.json").read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"invoices": []}


def save_ledger(ledger):
    (invoices_dir() / "invoices.json").write_text(
        json.dumps(ledger, indent=2) + "\n", encoding='utf-8'
    )


def last_invoice(ledger):
    return max(ledger['invoices'], key=lambda r: r['number'], default=None)


def next_invoice_number(ledger):
    nums = {r['number'] for r in ledger['invoices']}
    pat = re.compile(rf'Invoice_(\d+)_{re.escape(name_slug())}\.pdf$', re.I)
    try:
        for f in invoices_dir().iterdir():
            m = pat.match(f.name)
            if m:
                nums.add(int(m.group(1)))
    except OSError:
        pass
    return max(nums, default=CFG['next_invoice_number'] - 1) + 1


def entries_in_period(lines, start, end):
    entries, _ = collect_stats(lines)
    return [e for e in entries if e['date'] and start <= e['date'] <= end]


INVOICE_HTML = Template("""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Invoice $invoice_no — $contractor_name</title>
<style>
  @page { size: Letter; margin: 0.75in; }
  body {
    font-family: Helvetica, Arial, sans-serif;
    color: #333;
    font-size: 10.5pt;
    line-height: 1.45;
    margin: 0;
  }
  .label {
    font-size: 8pt;
    font-weight: bold;
    letter-spacing: 0.05em;
    color: #8a94a6;
    text-transform: uppercase;
  }
  .gray { color: #777; }
  .header { display: flex; justify-content: space-between; align-items: baseline; }
  .header h1 { font-size: 15pt; color: #1c2e4a; margin: 0 0 2px; }
  .invoice-word { font-size: 24pt; font-weight: bold; color: #1c2e4a; }
  .meta { display: flex; justify-content: space-between; margin-top: 20px; }
  .meta-table { border-collapse: collapse; }
  .meta-table td { padding: 1px 0; }
  .meta-table td.val { text-align: right; padding-left: 32px; }
  .parties { display: flex; margin-top: 34px; }
  .parties > div { width: 48%; }
  .parties .label { margin-bottom: 8px; }
  .items { width: 100%; border-collapse: collapse; margin-top: 34px; }
  .items th {
    text-align: left;
    border-bottom: 1px solid #999;
    padding: 6px 10px 6px 0;
  }
  .items td {
    border-bottom: 1px solid #e2e2e2;
    vertical-align: top;
    padding: 9px 10px 9px 0;
  }
  .items .c-date { width: 15%; }
  .items td:first-child { white-space: nowrap; }
  .items .c-desc { width: 51%; }
  .items .c-hours { width: 9%; }
  .items .c-rate { width: 11%; }
  .items .c-amount { width: 14%; }
  .summary { width: 46%; margin-left: auto; margin-top: 14px; }
  .summary .row { display: flex; justify-content: space-between; padding: 4px 0; }
  .summary .total { border-top: 1px solid #999; font-weight: bold; margin-top: 6px; padding-top: 9px; }
  .payment { margin-top: 44px; }
  .payment .label { margin-bottom: 6px; }
  .footer { margin-top: 26px; }
</style>
</head>
<body>
  <div class="header">
    <div>
      <h1>$contractor_name</h1>
      <div class="label">$contractor_title</div>
    </div>
    <div class="invoice-word">INVOICE</div>
  </div>
  <div class="meta">
    <div class="gray">$address</div>
    <table class="meta-table">
      <tr><td class="label">Invoice No.</td><td class="val">$invoice_no</td></tr>
      <tr><td class="label">Invoice Date</td><td class="val">$invoice_date</td></tr>
      <tr><td class="label">Billing Period</td><td class="val">$period</td></tr>
    </table>
  </div>
  <div class="parties">
    <div>
      <div class="label">Bill To</div>
      <div>$bill_to</div>
    </div>
    <div>
      <div class="label">For</div>
      <div>$invoice_for</div>
    </div>
  </div>
  <table class="items">
    <thead>
      <tr>
        <th class="label c-date">Date</th>
        <th class="label c-desc">Description</th>
        <th class="label c-hours">Hours</th>
        <th class="label c-rate">Rate</th>
        <th class="label c-amount">Amount</th>
      </tr>
    </thead>
    <tbody>
$rows
    </tbody>
  </table>
  <div class="summary">
    <div class="row"><span>Total Hours</span><span>$total_hours</span></div>
    <div class="row"><span>Rate</span><span>$rate_line</span></div>
    <div class="row total"><span>TOTAL DUE</span><span>$total_due</span></div>
  </div>
  <div class="payment">
    <div class="label">Payment</div>
    <div>$payment</div>
  </div>
  <div class="footer gray">$footer</div>
</body>
</html>
""")

ITEM_ROW = Template("""      <tr>
        <td>$date</td>
        <td>$desc</td>
        <td>$hours</td>
        <td>$rate</td>
        <td>$amount</td>
      </tr>""")


def html_lines(*parts):
    return '<br>'.join(escape(p) for p in parts if p)


def render_invoice_html(inv):
    rate = CFG['hourly_rate']
    rows = '\n'.join(
        ITEM_ROW.substitute(
            date=escape(format_full_date(e['date'])),
            desc=escape(e['desc']),
            hours=f"{e['hours']:.1f}",
            rate=money(rate),
            amount=money(e['hours'] * rate),
        )
        for e in inv['items']
    )
    return INVOICE_HTML.substitute(
        invoice_no=f"{inv['number']:03d}",
        invoice_date=escape(format_full_date(inv['invoice_date'])),
        period=escape(format_period(inv['period_start'], inv['period_end'])),
        contractor_name=escape(CFG['contractor_name']),
        contractor_title=escape(CFG['contractor_title']),
        address=html_lines(CFG['address_line1'], CFG['address_line2']),
        bill_to=html_lines(
            CFG['client_name'], CFG['client_attn'], CFG['client_email'], CFG['client_phone']
        ),
        invoice_for=html_lines(CFG['service_description'], CFG['engagement_description']),
        rows=rows,
        total_hours=f"{inv['total_hours']:.1f}",
        rate_line=f"{money(rate)} / hour",
        total_due=money(inv['total_due']),
        payment=html_lines(CFG['payment_method'], CFG['payment_terms']),
        footer=escape(CFG['invoice_footer']),
    )


def find_chrome():
    if CFG['chrome_path']:
        return CFG['chrome_path']
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome', 'msedge'):
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in CHROME_CANDIDATES if Path(p).exists()), None)


def open_path(path):
    try:
        if sys.platform == 'darwin':
            subprocess.run(['open', str(path)])
        elif sys.platform == 'win32':
            os.startfile(str(path))
        else:
            subprocess.run(['xdg-open', str(path)])
    except OSError:
        pass


def chrome_to_pdf(html_path, pdf_path):
    chrome = find_chrome()
    if not chrome or not Path(chrome).exists():
        return False
    if pdf_path.exists():
        pdf_path.unlink()
    profile = tempfile.mkdtemp(prefix="loghours-chrome-")
    try:
        proc = subprocess.Popen(
            [
                chrome, '--headless', '--disable-gpu',
                '--no-first-run', '--no-default-browser-check',
                f'--user-data-dir={profile}',
                '--no-pdf-header-footer',
                f'--print-to-pdf={pdf_path}',
                html_path.resolve().as_uri(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        shutil.rmtree(profile, ignore_errors=True)
        return False

    # Chrome sometimes writes the PDF but never exits; poll for a stable file.
    deadline = time.monotonic() + 60
    size = -1
    while time.monotonic() < deadline:
        exited = proc.poll() is not None
        cur = pdf_path.stat().st_size if pdf_path.exists() else 0
        if cur > 0 and cur == size:
            break
        size = cur
        if exited:
            break
        time.sleep(0.5)

    if proc.poll() is None:
        proc.kill()
        proc.wait()
    shutil.rmtree(profile, ignore_errors=True)
    return pdf_path.exists() and pdf_path.stat().st_size > 0


def generate_invoice(lines):
    ledger = load_ledger()
    prev = last_invoice(ledger)
    default_start = (
        datetime.strptime(prev['period_end'], '%Y-%m-%d').date() + timedelta(days=1)
        if prev else date.today()
    )

    print()
    print(section('INVOICE'))
    start = ask_date('Period start', default_start)
    end = ask_date('Period end', date.today())
    if start > end:
        print("Period start is after period end.")
        sys.exit(1)

    entries = entries_in_period(lines, start, end)
    if not entries:
        print(f"No entries between {format_full_date(start)} and {format_full_date(end)}.")
        sys.exit(0)

    if prev and (prev['period_start'], prev['period_end']) == (start.isoformat(), end.isoformat()):
        num = prev['number']
    else:
        num = next_invoice_number(ledger)

    total_hours = sum(e['hours'] for e in entries)
    total_due = total_hours * CFG['hourly_rate']
    stem = f"Invoice_{num:03d}_{name_slug()}"
    pdf_path = invoices_dir() / f"{stem}.pdf"
    html_path = invoices_dir() / f"{stem}.html"

    print()
    print(f"   {BOLD}Invoice {num:03d}{RESET}   {DIM}{format_period(start, end)}{RESET}")
    for e in entries:
        desc = e['desc']
        room = WIDTH - 30
        if len(desc) > room:
            cut = desc[:room - 1]
            if ' ' in cut:
                cut = cut.rsplit(' ', 1)[0]
            desc = cut + '…'
        print(
            f"   {fg(grad(0.2))}{format_full_date(e['date']):<18}{RESET}"
            f"{BOLD}{e['hours']:>4.1f}h{RESET}  {DIM}{desc}{RESET}"
        )
    plural = 's' if len(entries) != 1 else ''
    print(
        f"   {DIM}{len(entries)} line item{plural} ·{RESET} {BOLD}{total_hours:.1f} hrs{RESET}"
        f" {DIM}·{RESET} {BOLD}{money(total_due)}{RESET} {DIM}({money(CFG['hourly_rate'])}/hr){RESET}"
    )
    print()

    verb = "Overwrite" if pdf_path.exists() else "Generate"
    if input(f"{verb} {pdf_path.name}? (y/n): ").strip().lower() != 'y':
        print("Aborted.")
        sys.exit(0)

    inv = {
        'number': num,
        'invoice_date': date.today(),
        'period_start': start,
        'period_end': end,
        'items': entries,
        'total_hours': total_hours,
        'total_due': total_due,
    }
    invoices_dir().mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_invoice_html(inv), encoding='utf-8')

    record = {
        'number': num,
        'invoice_date': date.today().isoformat(),
        'period_start': start.isoformat(),
        'period_end': end.isoformat(),
        'total_hours': total_hours,
        'total_due': total_due,
        'pdf': pdf_path.name,
    }
    ledger['invoices'] = [r for r in ledger['invoices'] if r['number'] != num]
    ledger['invoices'].append(record)
    ledger['invoices'].sort(key=lambda r: r['number'])
    save_ledger(ledger)

    if chrome_to_pdf(html_path, pdf_path):
        html_path.unlink(missing_ok=True)
        print()
        print(f" {fg(grad(0.0))}{BOLD}✓ Created{RESET}  {pdf_path}")
        print()
    else:
        open_path(html_path)
        print()
        print(f"Chrome PDF export failed — saved {html_path.name} instead.")
        print(f"Open it in a browser and use Print → Save as PDF, saving as {pdf_path.name} in {invoices_dir()}.")
        print()


def load_config():
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        print(f"Could not parse {CONFIG_FILE}: {e}")
        sys.exit(1)
    CFG.clear()
    CFG.update(DEFAULTS)
    CFG.update(data)
    missing = [k for k in REQUIRED if not CFG[k]]
    if missing:
        print(f"{CONFIG_FILE.name} is missing {', '.join(missing)} — run: log_hours.py setup")
        sys.exit(1)
    try:
        CFG['hourly_rate'] = float(CFG['hourly_rate'])
        CFG['next_invoice_number'] = int(CFG['next_invoice_number'])
    except (TypeError, ValueError):
        print(f"{CONFIG_FILE.name}: hourly_rate and next_invoice_number must be numbers")
        sys.exit(1)
    days = CFG['work_days']
    if not isinstance(days, list) or not days or set(days) - set(DAY_NAMES):
        print(f"{CONFIG_FILE.name}: work_days must be a non-empty list of {', '.join(DAY_NAMES)}")
        sys.exit(1)
    CFG['work_days'] = sorted(set(days), key=DAY_NAMES.index)


def create_hours_file():
    parts = (CFG['contractor_name'].split(' ', 1)[0], CFG['period_label'], CFG['client_name'])
    HOURS_FILE.write_text('\n'.join([
        f"{CFG['project_name'].upper()} — HOURS LOG",
        ' | '.join(p for p in parts if p),
        '=' * len(RULE),
        '',
        '=' * len(RULE),
        'RUNNING TOTAL: 0.0 hrs',
    ]) + '\n', encoding='utf-8')


def parse_work_days(raw):
    days = [d.strip()[:3].capitalize() for d in raw.split(',') if d.strip()]
    if not days or any(d not in DAY_NAMES for d in days):
        raise ValueError('Use day abbreviations separated by commas, e.g. Mon, Tue, Wed, Thu, Fri')
    return sorted(set(days), key=DAY_NAMES.index)


def parse_value(key, raw):
    if key == 'hourly_rate':
        if not re.fullmatch(r'\d+(\.\d*)?', raw) or float(raw) <= 0:
            raise ValueError('Enter a positive number, e.g. 25 or 32.50')
        return float(raw)
    if key == 'next_invoice_number':
        if not raw.isdigit() or int(raw) < 1:
            raise ValueError('Enter a whole number of 1 or more')
        return int(raw)
    if key == 'work_days':
        return parse_work_days(raw)
    if key == 'chrome_path':
        return raw or None
    if key in REQUIRED and not raw:
        raise ValueError('This field is required')
    return raw


def run_setup():
    current = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            current.update(json.loads(CONFIG_FILE.read_text(encoding='utf-8')))
        except json.JSONDecodeError:
            pass
    print()
    print(gradient_text('  SETUP', bold=True))
    print(f"  {DIM}Press Enter to keep the value in brackets.{RESET}")
    cfg = {}
    for title, fields in CONFIG_FIELDS:
        print()
        print(section(title))
        for key, prompt, default in fields:
            cur = current[key]
            if isinstance(cur, list):
                shown = ', '.join(cur)
            elif cur is None:
                shown = ''
            else:
                shown = str(cur)
            while True:
                try:
                    cfg[key] = parse_value(key, ask(prompt, shown))
                    break
                except ValueError as e:
                    print(f"   {DIM}{e}{RESET}")
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding='utf-8')
    load_config()
    print()
    print(f" {fg(grad(0.0))}{BOLD}✓ Saved{RESET}  {CONFIG_FILE}")
    print(f"   {DIM}Hours log:{RESET} {HOURS_FILE}")
    print(f"   {DIM}Invoices:{RESET}  {invoices_dir()}")
    print()


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else None
    if arg == 'setup':
        run_setup()
        return
    if arg not in (None, 'log', 'edit', 'stats', 'dash', 'dashboard', 'invoice', 'inv'):
        print(f"Unknown command: {arg}")
        print("Usage: log_hours.py [log|edit|stats|invoice|setup]")
        sys.exit(1)

    if CONFIG_FILE.exists():
        load_config()
    else:
        print(f"No config found at {CONFIG_FILE} — starting setup.")
        run_setup()
    if not HOURS_FILE.exists():
        create_hours_file()
    lines = HOURS_FILE.read_text(encoding='utf-8').splitlines()

    if arg in ('stats', 'dash', 'dashboard'):
        show_dashboard(lines)
        return
    if arg == 'log':
        log_today(lines)
        return
    if arg == 'edit':
        edit_previous(lines)
        return
    if arg in ('invoice', 'inv'):
        generate_invoice(lines)
        return

    today_label = day_prefix(datetime.today())
    print()
    print(gradient_text(f"  {CFG['project_name'].upper()} HOURS", bold=True))
    print(f"  {DIM}1{RESET}  Log today ({today_label})")
    print(f"  {DIM}2{RESET}  Edit a previous entry")
    print(f"  {DIM}3{RESET}  Dashboard")
    print(f"  {DIM}4{RESET}  Generate invoice")
    print(f"  {DIM}5{RESET}  Setup")
    choice = input("  Choice (1/2/3/4/5) [1]: ").strip() or '1'

    if choice == '1':
        log_today(lines)
    elif choice == '2':
        edit_previous(lines)
    elif choice == '3':
        show_dashboard(lines)
    elif choice == '4':
        generate_invoice(lines)
    elif choice == '5':
        run_setup()
    else:
        print("Invalid choice.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
