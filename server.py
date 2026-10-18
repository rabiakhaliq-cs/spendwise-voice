import sqlite3
from datetime import date, datetime, timedelta
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SpendWise Voice")
DB_FILE = "expenses.db"

KEYWORDS = {
    "food": ["food", "khana", "restaurant", "foodpanda", "lunch", "dinner", "grocery"],
    "transport": ["petrol", "fuel", "uber", "careem", "bus", "rickshaw", "taxi"],
    "bills": ["bill", "electricity", "gas", "internet", "rent"],
    "shopping": ["shopping", "clothes", "kapray", "daraz", "shoes"],
    "health": ["medicine", "doctor", "dawai", "hospital"],
}


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
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
    conn.commit()
    conn.close()


def guess_category(text: str) -> str:
    text = text.lower()
    for category, words in KEYWORDS.items():
        if any(w in text for w in words):
            return category
    return "other"


def period_range(period: str):
    today = date.today()
    if period == "today":
        start, end = today, today
    elif period == "week":
        start, end = today - timedelta(days=today.weekday()), today
    elif period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    else:  # "month"
        start, end = today.replace(day=1), today
    return start.isoformat(), end.isoformat()


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


@mcp.tool()
def add_expense(amount: float, category: str = "", note: str = "",
                user_id: str = "demo_user") -> str:
    """Add a new expense. Category is guessed from the note if not given."""
    if amount <= 0 or amount > 10_000_000:
        return "That amount doesn't look right. How many rupees was it?"
    cat = category.lower().strip() or guess_category(note)
    conn = get_conn()
    conn.execute(
        "INSERT INTO expenses (user_id, amount, category, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, cat, note, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    warning = budget_warning(conn, user_id, cat)
    conn.close()
    return f"Added {amount:.0f} rupees to {cat}.{warning}"


@mcp.tool()
def get_total(period: str = "month", category: str = "",
              user_id: str = "demo_user") -> str:
    """Total spending for a period: today, week, month or last_month.
    Optionally filter by category."""
    start, end = period_range(period)
    conn = get_conn()
    total = sum_for(conn, user_id, start, end, category.lower().strip())
    conn.close()
    label = f" on {category}" if category else ""
    return f"You spent {total:.0f} rupees{label} for {period.replace('_', ' ')}."


@mcp.tool()
def top_category(period: str = "month", user_id: str = "demo_user") -> str:
    """Find the category with the highest spending in a period."""
    start, end = period_range(period)
    conn = get_conn()
    rows = conn.execute(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ? "
        "GROUP BY category ORDER BY total DESC",
        (user_id, start, end),
    ).fetchall()
    conn.close()
    if not rows:
        return "You have no expenses recorded for this period."
    grand_total = sum(r["total"] for r in rows)
    top = rows[0]
    pct = top["total"] / grand_total * 100
    return (f"You spent the most on {top['category']}: {top['total']:.0f} rupees, "
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
def delete_last_expense(confirm: bool = False, user_id: str = "demo_user") -> str:
    """Delete the most recent expense. Ask the user to confirm first,
    then call again with confirm=true."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, amount, category FROM expenses WHERE user_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        conn.close()
        return "You have no expenses to delete."
    if not confirm:
        conn.close()
        return (f"Your last expense was {row['amount']:.0f} rupees on {row['category']}. "
                "Should I delete it?")
    conn.execute("DELETE FROM expenses WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return f"Deleted {row['amount']:.0f} rupees from {row['category']}."


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

    # 1. Top category
    by_cat = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["amount"]
    top_cat = max(by_cat, key=by_cat.get)
    tips.append(f"{top_cat.capitalize()} is your biggest spending at "
                f"{by_cat[top_cat] / total * 100:.0f} percent of the total.")

    # 2. Weekend vs weekday (average per day)
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

    # 3. Biggest single expense
    biggest = max(rows, key=lambda r: r["amount"])
    tips.append(f"Your biggest single expense was {biggest['amount']:.0f} rupees on "
                f"{biggest['category']}.")

    return " ".join(tips)


if __name__ == "__main__":
    init_db()
    mcp.run(transport="streamable-http")