import sqlite3
from datetime import date, datetime, timedelta
from mcp.server.fastmcp import FastMCP
import calendar

mcp = FastMCP("SpendWise Voice")
DB_FILE = "expenses.db"

KEYWORDS = {
    "food": ["food", "khana", "restaurant", "foodpanda", "lunch", "dinner", "grocery"],
    "transport": ["petrol", "fuel", "uber", "careem", "bus", "rickshaw", "taxi"],
    "bills": ["bill", "electricity", "gas", "internet", "rent"],
    "shopping": ["shopping", "clothes", "kapray", "daraz", "shoes"],
    "health": ["medicine", "doctor", "dawai", "hospital"],
}


def due_date_for(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def due_recurring(conn, user_id, name=""):
    """Recurring bills whose due day has arrived this month and are not yet handled."""
    today = date.today()
    ym = today.strftime("%Y-%m")
    rows = conn.execute(
        "SELECT id, amount, category, note, day_of_month FROM recurring "
        "WHERE user_id = ? AND (last_applied IS NULL OR last_applied != ?) "
        "ORDER BY day_of_month", (user_id, ym),
    ).fetchall()
    n = name.lower().strip()
    out = []
    for r in rows:
        if due_date_for(today.year, today.month, r["day_of_month"]) <= today:
            if not n or n in r["note"].lower() or n in r["category"].lower():
                out.append(r)
    return out


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS budgets (
            user_id TEXT NOT NULL,
            category TEXT NOT NULL,
            limit_amount REAL NOT NULL,
            PRIMARY KEY (user_id, category)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS recurring (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            note TEXT NOT NULL,
            day_of_month INTEGER NOT NULL,
            last_applied TEXT
        )"""
    )
    conn.commit()
    conn.close()


def guess_category(text: str) -> str:
    text = text.lower()
    for category, words in KEYWORDS.items():
        if any(w in text for w in words):
            return category
    return "other"


def parse_day(value):
    """Accepts 'today', 'yesterday' or YYYY-MM-DD. Returns a date or None."""
    v = (value or "").strip().lower()
    if v in ("", "today"):
        return date.today()
    if v == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def period_range(period: str):
    today = date.today()
    if period == "today":
        start, end = today, today
    elif period == "yesterday":
        start = end = today - timedelta(days=1)
    elif period == "week":
        start, end = today - timedelta(days=today.weekday()), today
    elif period == "last_week":
        this_monday = today - timedelta(days=today.weekday())
        start, end = this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    elif period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    else:  # "month"
        start, end = today.replace(day=1), today
    return start.isoformat(), end.isoformat()


def resolve_range(period="month", start_date="", end_date=""):
    """Custom dates win over the period. Returns (start, end, label) or None if invalid."""
    if start_date:
        s = parse_day(start_date)
        e = parse_day(end_date) if end_date else s
        if s is None or e is None:
            return None
        if e < s:
            s, e = e, s
        label = s.isoformat() if s == e else f"{s.isoformat()} to {e.isoformat()}"
        return s.isoformat(), e.isoformat(), label
    start, end = period_range(period)
    return start, end, period.replace("_", " ")


def sum_for(conn, user_id, start, end, category=""):
    query = ("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
             "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ?")
    params = [user_id, start, end]
    if category:
        query += " AND category = ?"
        params.append(category)
    return conn.execute(query, params).fetchone()["total"]


def budget_warning(conn, user_id, category) -> str:
    row = conn.execute(
        "SELECT limit_amount FROM budgets WHERE user_id = ? AND category = ?",
        (user_id, category),
    ).fetchone()
    if not row:
        return ""
    start, end = period_range("month")
    spent = sum_for(conn, user_id, start, end, category)
    limit = row["limit_amount"]
    pct = spent / limit * 100
    if pct >= 100:
        return (f" Warning: you have crossed your {category} budget of "
                f"{limit:.0f} rupees. You spent {spent:.0f} this month.")
    if pct >= 80:
        return f" Heads up: you have used {pct:.0f} percent of your {category} budget."
    return ""


def find_expense(conn, user_id, amount=0, position=0):
    """Find an expense by amount (latest with that amount) or by position (1 = last added)."""
    if amount and amount > 0:
        row = conn.execute(
            "SELECT id, amount, category FROM expenses "
            "WHERE user_id = ? AND amount = ? ORDER BY id DESC LIMIT 1",
            (user_id, amount),
        ).fetchone()
        return row, f"I couldn't find an expense of {amount:.0f} rupees."
    n = max(int(position), 1)
    row = conn.execute(
        "SELECT id, amount, category FROM expenses "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
        (user_id, n - 1),
    ).fetchone()
    return row, "I couldn't find that expense."


@mcp.tool()
def add_expense(amount: float, category: str = "", note: str = "",
                on_date: str = "", user_id: str = "demo_user") -> str:
    """Add a new expense. Category is guessed from the note if not given.
    on_date can be 'yesterday' or YYYY-MM-DD; empty means today."""
    if amount <= 0 or amount > 10_000_000:
        return "That amount doesn't look right. How many rupees was it?"
    d = parse_day(on_date)
    if d is None:
        return "I couldn't understand that date. Which day was it?"
    if d > date.today():
        return "That date is in the future. Which day was it?"
    cat = category.lower().strip() or guess_category(note)
    created = datetime.combine(d, datetime.now().time()).isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        "INSERT INTO expenses (user_id, amount, category, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, cat, note, created),
    )
    conn.commit()
    today = date.today()
    warning = ""
    if (d.year, d.month) == (today.year, today.month):
        warning = budget_warning(conn, user_id, cat)
    conn.close()
    when = "" if d == today else f" for {d.strftime('%d %B')}"
    return f"Added {amount:.0f} rupees to {cat}{when}.{warning}"


@mcp.tool()
def get_total(period: str = "month", category: str = "",
              start_date: str = "", end_date: str = "",
              user_id: str = "demo_user") -> str:
    """Total spending. period: today, yesterday, week, last_week, month, last_month.
    Or give start_date and end_date as YYYY-MM-DD. Optionally filter by category."""
    r = resolve_range(period, start_date, end_date)
    if r is None:
        return "I couldn't understand those dates. Which days do you mean?"
    start, end, label = r
    conn = get_conn()
    total = sum_for(conn, user_id, start, end, category.lower().strip())
    conn.close()
    on = f" on {category}" if category else ""
    return f"You spent {total:.0f} rupees{on} for {label}."


@mcp.tool()
def top_category(period: str = "month", start_date: str = "", end_date: str = "",
                 user_id: str = "demo_user") -> str:
    """Find the category with the highest spending in a period or date range."""
    r = resolve_range(period, start_date, end_date)
    if r is None:
        return "I couldn't understand those dates. Which days do you mean?"
    start, end, label = r
    conn = get_conn()
    rows = conn.execute(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ? "
        "GROUP BY category ORDER BY total DESC",
        (user_id, start, end),
    ).fetchall()
    conn.close()
    if not rows:
        return f"You have no expenses recorded for {label}."
    grand_total = sum(r_["total"] for r_ in rows)
    top = rows[0]
    pct = top["total"] / grand_total * 100
    return (f"For {label}, you spent the most on {top['category']}: {top['total']:.0f} rupees, "
            f"which is {pct:.0f} percent of your total.")


@mcp.tool()
def compare_months(user_id: str = "demo_user") -> str:
    """Compare spending so far this month with the whole of last month."""
    conn = get_conn()
    this_start, this_end = period_range("month")
    last_start, last_end = period_range("last_month")
    this_total = sum_for(conn, user_id, this_start, this_end)
    last_total = sum_for(conn, user_id, last_start, last_end)
    conn.close()
    if last_total == 0:
        return (f"You spent {this_total:.0f} rupees this month. "
                "There is no data for last month to compare with.")
    diff = this_total - last_total
    pct = abs(diff) / last_total * 100
    direction = "more" if diff > 0 else "less"
    return (f"This month you spent {this_total:.0f} rupees so far. "
            f"Last month's total was {last_total:.0f}. "
            f"That is {abs(diff):.0f} rupees, or {pct:.0f} percent, {direction} than last month.")


@mcp.tool()
def set_budget(category: str, limit: float, user_id: str = "demo_user") -> str:
    """Set a monthly budget limit for a category."""
    if not category.strip():
        return "Which category is this budget for?"
    if limit <= 0:
        return "The budget must be more than zero. How much should it be?"
    cat = category.lower().strip()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO budgets (user_id, category, limit_amount) VALUES (?, ?, ?)",
        (user_id, cat, limit),
    )
    conn.commit()
    conn.close()
    return f"Done. Your monthly {cat} budget is now {limit:.0f} rupees."


@mcp.tool()
def get_budget_status(category: str = "", user_id: str = "demo_user") -> str:
    """Show how much of the monthly budget is left. Empty category = all budgets."""
    cat = category.lower().strip()
    conn = get_conn()
    if cat:
        rows = conn.execute(
            "SELECT category, limit_amount FROM budgets WHERE user_id = ? AND category = ?",
            (user_id, cat),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT category, limit_amount FROM budgets WHERE user_id = ?", (user_id,)
        ).fetchall()
    if not rows:
        conn.close()
        if cat:
            return f"You haven't set a {cat} budget yet. Tell me an amount and I will set it."
        return "You haven't set any budgets yet. Try: set food budget to 10000."
    start, end = period_range("month")
    parts = []
    for r in rows:
        spent = sum_for(conn, user_id, start, end, r["category"])
        limit = r["limit_amount"]
        left = limit - spent
        if left >= 0:
            parts.append(f"{r['category']}: {left:.0f} rupees left out of {limit:.0f}, "
                         f"{spent / limit * 100:.0f} percent used")
        else:
            parts.append(f"{r['category']}: over budget by {-left:.0f} rupees "
                         f"(limit {limit:.0f}, spent {spent:.0f})")
    conn.close()
    return ". ".join(parts) + "."


@mcp.tool()
def list_recent_expenses(count: int = 5, user_id: str = "demo_user") -> str:
    """List the most recently added expenses (default 5, max 10)."""
    n = min(max(int(count), 1), 10)
    conn = get_conn()
    rows = conn.execute(
        "SELECT amount, category, created_at FROM expenses WHERE user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    conn.close()
    if not rows:
        return "You have no expenses yet."
    items = []
    for i, r in enumerate(rows, 1):
        d = datetime.fromisoformat(r["created_at"]).strftime("%d %b")
        items.append(f"{i}. {r['amount']:.0f} rupees on {r['category']}, {d}")
    return f"Your last {len(rows)} expenses: " + "; ".join(items) + "."


@mcp.tool()
def edit_expense(find_amount: float = 0, position: int = 0,
                 new_amount: float = 0, new_category: str = "",
                 user_id: str = "demo_user") -> str:
    """Change the amount and/or category of one expense. Find it by its current
    amount (find_amount) or by position (1 = last added, 2 = the one before)."""
    if new_amount == 0 and not new_category.strip():
        return "What should I change it to? Tell me the new amount or category."
    if new_amount and not (0 < new_amount <= 10_000_000):
        return "That amount doesn't look right. What should the new amount be?"
    conn = get_conn()
    row, missing = find_expense(conn, user_id, find_amount, position)
    if not row:
        conn.close()
        return missing
    amount = new_amount or row["amount"]
    cat = new_category.lower().strip() or row["category"]
    conn.execute("UPDATE expenses SET amount = ?, category = ? WHERE id = ?",
                 (amount, cat, row["id"]))
    conn.commit()
    warning = budget_warning(conn, user_id, cat)
    conn.close()
    return (f"Changed {row['amount']:.0f} rupees on {row['category']} to "
            f"{amount:.0f} rupees on {cat}.{warning}")


@mcp.tool()
def delete_expense(amount: float = 0, position: int = 0, confirm: bool = False,
                   user_id: str = "demo_user") -> str:
    """Delete one expense by amount or position (1 = last added).
    Call with confirm=false to preview, then confirm=true to delete."""
    conn = get_conn()
    row, missing = find_expense(conn, user_id, amount, position)
    if not row:
        conn.close()
        return missing
    if not confirm:
        conn.close()
        return f"{row['amount']:.0f} rupees on {row['category']}"
    conn.execute("DELETE FROM expenses WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return f"Deleted {row['amount']:.0f} rupees from {row['category']}."


@mcp.tool()
def delete_last_expense(confirm: bool = False, user_id: str = "demo_user") -> str:
    """Delete the most recent expense. Ask the user to confirm first,
    then call again with confirm=true."""
    return delete_expense(position=1, confirm=confirm, user_id=user_id)


@mcp.tool()
def get_insights(user_id: str = "demo_user") -> str:
    """Give short, useful tips based on this month's spending."""
    start, end = period_range("month")
    conn = get_conn()
    rows = conn.execute(
        "SELECT amount, category, created_at FROM expenses "
        "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ?",
        (user_id, start, end),
    ).fetchall()
    conn.close()
    if len(rows) < 3:
        return "Add a few more expenses and I will give you personal tips."

    tips = []
    total = sum(r["amount"] for r in rows)

    by_cat = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["amount"]
    top_cat = max(by_cat, key=by_cat.get)
    tips.append(f"{top_cat.capitalize()} is your biggest spending at "
                f"{by_cat[top_cat] / total * 100:.0f} percent of the total.")

    weekend, weekday = [], []
    for r in rows:
        d = datetime.fromisoformat(r["created_at"])
        (weekend if d.weekday() >= 5 else weekday).append(r["amount"])
    if weekend and weekday:
        w_avg = sum(weekend) / len(weekend)
        d_avg = sum(weekday) / len(weekday)
        if w_avg > d_avg * 1.3:
            tips.append("Your weekend expenses are noticeably higher than weekdays. "
                        "Planning weekend spending could help you save.")

    biggest = max(rows, key=lambda r: r["amount"])
    tips.append(f"Your biggest single expense was {biggest['amount']:.0f} rupees on "
                f"{biggest['category']}.")
    return " ".join(tips)


@mcp.tool()
def category_breakdown(period: str = "month", user_id: str = "demo_user") -> str:
    """Return spending per category as JSON text (used by the dashboard)."""
    import json
    start, end = period_range(period)
    conn = get_conn()
    rows = conn.execute(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ? "
        "GROUP BY category ORDER BY total DESC",
        (user_id, start, end),
    ).fetchall()
    conn.close()
    return json.dumps({r["category"]: r["total"] for r in rows})

@mcp.tool()
def add_recurring(amount: float, category: str = "", note: str = "",
                  day_of_month: int = 1, user_id: str = "demo_user") -> str:
    """Add a monthly recurring bill such as rent or internet. It is NOT added
    automatically: when due, the user is reminded and must confirm."""
    if amount <= 0 or amount > 10_000_000:
        return "That amount doesn't look right. How many rupees is it each month?"
    if not 1 <= int(day_of_month) <= 31:
        return "Which day of the month is it due? Please say a number from 1 to 31."
    day = int(day_of_month)
    cat = category.lower().strip() or guess_category(note)
    name = (note.strip() or cat).lower()
    conn = get_conn()
    conn.execute("DELETE FROM recurring WHERE user_id = ? AND lower(note) = ?", (user_id, name))
    conn.execute(
        "INSERT INTO recurring (user_id, amount, category, note, day_of_month, last_applied) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        (user_id, amount, cat, name, day),
    )
    conn.commit()
    conn.close()
    return (f"Done. {name.capitalize()}, {amount:.0f} rupees on {cat}, every month "
            f"on day {day}. I will remind you when it is due.")


@mcp.tool()
def list_recurring(user_id: str = "demo_user") -> str:
    """List all monthly recurring bills and whether one is waiting for the user's OK."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, note, amount, day_of_month FROM recurring WHERE user_id = ? "
        "ORDER BY day_of_month", (user_id,)
    ).fetchall()
    due_ids = {r["id"] for r in due_recurring(conn, user_id)}
    conn.close()
    if not rows:
        return "You have no recurring bills. Try: add rent 25000 every month on the first."
    items = [f"{r['note']}, {r['amount']:.0f} rupees on day {r['day_of_month']}"
             + (" (waiting for your OK)" if r["id"] in due_ids else "") for r in rows]
    total = sum(r["amount"] for r in rows)
    return "Your monthly recurring bills: " + "; ".join(items) + f". Total {total:.0f} rupees a month."


@mcp.tool()
def recurring_reminder(user_id: str = "demo_user") -> str:
    """Reminder text for recurring bills that are due and not yet added.
    Returns NONE when nothing is waiting."""
    conn = get_conn()
    rows = due_recurring(conn, user_id)
    conn.close()
    if not rows:
        return "NONE"
    items = ", ".join(f"{r['note']} {r['amount']:.0f}" for r in rows)
    total = sum(r["amount"] for r in rows)
    opener = "A new month has started." if date.today().day <= 3 else "Reminder."
    return (f"{opener} Your recurring bills are: {items}. Total {total:.0f} rupees. "
            "Should I add them to your expenses? Say yes, or skip this month, or tell me to "
            "remove a bill. I will keep reminding you until you decide.")


@mcp.tool()
def confirm_recurring(name: str = "", user_id: str = "demo_user") -> str:
    """Add the due recurring bills to expenses (all of them, or only the one named)."""
    conn = get_conn()
    rows = due_recurring(conn, user_id, name)
    if not rows:
        conn.close()
        return "You have no recurring bills waiting to be added."
    today = date.today()
    ym = today.strftime("%Y-%m")
    added, warnings = [], ""
    for r in rows:
        due = due_date_for(today.year, today.month, r["day_of_month"])
        created = datetime(due.year, due.month, due.day, 9, 0, 0).isoformat()
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, r["amount"], r["category"], r["note"], created),
        )
        conn.execute("UPDATE recurring SET last_applied = ? WHERE id = ?", (ym, r["id"]))
        added.append(f"{r['note']} {r['amount']:.0f}")
        warnings += budget_warning(conn, user_id, r["category"])
    conn.commit()
    conn.close()
    total = sum(r["amount"] for r in rows)
    return f"Added {', '.join(added)}. Total {total:.0f} rupees.{warnings}"


@mcp.tool()
def skip_recurring(name: str = "", user_id: str = "demo_user") -> str:
    """Skip the due recurring bills for this month without adding an expense."""
    conn = get_conn()
    rows = due_recurring(conn, user_id, name)
    if not rows:
        conn.close()
        return "There is nothing to skip right now."
    ym = date.today().strftime("%Y-%m")
    for r in rows:
        conn.execute("UPDATE recurring SET last_applied = ? WHERE id = ?", (ym, r["id"]))
    conn.commit()
    conn.close()
    return "Okay, I skipped " + ", ".join(r["note"] for r in rows) + " for this month."


@mcp.tool()
def remove_recurring(name: str, user_id: str = "demo_user") -> str:
    """Stop a recurring expense. name is its note or category, for example rent.
    Expenses already recorded are kept."""
    n = name.lower().strip()
    if not n:
        return "Which recurring expense should I stop?"
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, note, amount FROM recurring WHERE user_id = ? "
        "AND (lower(note) LIKE ? OR lower(category) LIKE ?)",
        (user_id, f"%{n}%", f"%{n}%"),
    ).fetchall()
    if not rows:
        conn.close()
        return f"I couldn't find a recurring expense called {name}."
    if len(rows) > 1:
        conn.close()
        names = ", ".join(r["note"] for r in rows)
        return f"I found more than one: {names}. Which one should I stop?"
    conn.execute("DELETE FROM recurring WHERE id = ?", (rows[0]["id"],))
    conn.commit()
    conn.close()
    return f"Stopped {rows[0]['note']}. Past expenses are kept."


@mcp.tool()
def weekly_summary(user_id: str = "demo_user") -> str:
    """A short spoken summary of the last 7 days."""
    today = date.today()
    s, e = (today - timedelta(days=6)).isoformat(), today.isoformat()
    ps, pe = (today - timedelta(days=13)).isoformat(), (today - timedelta(days=7)).isoformat()
    conn = get_conn()
    total = sum_for(conn, user_id, s, e)
    prev = sum_for(conn, user_id, ps, pe)
    if total == 0:
        conn.close()
        return "You haven't recorded any expenses in the last 7 days."

    cats = conn.execute(
        "SELECT category, SUM(amount) AS t FROM expenses "
        "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ? "
        "GROUP BY category ORDER BY t DESC", (user_id, s, e)
    ).fetchall()
    biggest = conn.execute(
        "SELECT amount, category FROM expenses "
        "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ? "
        "ORDER BY amount DESC LIMIT 1", (user_id, s, e)
    ).fetchone()

    parts = [f"In the last 7 days you spent {total:.0f} rupees."]
    if prev > 0:
        diff = total - prev
        pct = abs(diff) / prev * 100
        parts.append(f"That is {pct:.0f} percent {'more' if diff > 0 else 'less'} "
                     "than the week before.")
    top = cats[0]
    parts.append(f"Most of it, {top['t']:.0f} rupees, went to {top['category']}.")
    parts.append(f"Your biggest single expense was {biggest['amount']:.0f} rupees "
                 f"on {biggest['category']}.")

    # budget alerts
    ms, me = period_range("month")
    alerts = []
    for b in conn.execute("SELECT category, limit_amount FROM budgets WHERE user_id = ?",
                          (user_id,)).fetchall():
        spent = sum_for(conn, user_id, ms, me, b["category"])
        pct = spent / b["limit_amount"] * 100
        if pct >= 80:
            alerts.append(f"{b['category']} budget is {pct:.0f} percent used")
    if alerts:
        parts.append("Watch out: " + " and ".join(alerts) + ".")

    # recurring due in the next 7 days
    upcoming = []
    for r in conn.execute("SELECT note, amount, day_of_month FROM recurring WHERE user_id = ?",
                          (user_id,)).fetchall():
        due = due_date_for(today.year, today.month, r["day_of_month"])
        if due <= today:
            ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            due = due_date_for(ny, nm, r["day_of_month"])
        if 0 < (due - today).days <= 7:
            upcoming.append(f"{r['note']} of {r['amount']:.0f} rupees on {due.strftime('%d %B')}")
    if upcoming:
        parts.append("Coming up this week: " + ", ".join(upcoming) + ".")
    conn.close()
    return " ".join(parts)

if __name__ == "__main__":
    init_db()
    mcp.run(transport="streamable-http")