import sqlite3
from datetime import date, datetime, timedelta
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SpendWise Voice")
DB_FILE = "expenses.db"

# keyword -> category (auto-categorization ka simple version)
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
    else:  # "month" (default)
        start, end = today.replace(day=1), today
    return start.isoformat(), end.isoformat()


@mcp.tool()
def add_expense(amount: float, category: str = "", note: str = "",
                user_id: str = "demo_user") -> str:
    """Add a new expense. Category is guessed from the note if not given."""
    if amount <= 0 or amount > 10_000_000:
        return "That amount doesn't look right. Please say it again."
    cat = category.lower().strip() or guess_category(note)
    conn = get_conn()
    conn.execute(
        "INSERT INTO expenses (user_id, amount, category, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, cat, note, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return f"Added {amount:.0f} rupees to {cat}."


@mcp.tool()
def get_total(period: str = "month", category: str = "",
              user_id: str = "demo_user") -> str:
    """Total spending for a period: today, week, month or last_month.
    Optionally filter by category."""
    start, end = period_range(period)
    query = ("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
             "WHERE user_id = ? AND date(created_at) BETWEEN ? AND ?")
    params = [user_id, start, end]
    if category:
        query += " AND category = ?"
        params.append(category.lower().strip())
    conn = get_conn()
    total = conn.execute(query, params).fetchone()["total"]
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


if __name__ == "__main__":
    init_db()
    mcp.run(transport="streamable-http")