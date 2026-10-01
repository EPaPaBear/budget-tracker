"""
AuthPilot-independent, standalone personal budgeting web app.
50/30/20 adaptive envelope budgeting for a single user in Ghana (GHS).

Backend: FastAPI + SQLite (stdlib sqlite3), served by uvicorn.
Run:  uvicorn app:app --port 8770 --reload
"""

from __future__ import annotations

import math
import sqlite3
import datetime as dt
import os
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
# DB path is overridable (LEDGER_DB) so tests can run against a scratch DB.
DB_PATH = Path(os.environ.get("LEDGER_DB") or (BASE_DIR / "budget.db"))
STATIC_DIR = BASE_DIR / "static"

# ---------------------------------------------------------------------------
# Constants for the 50/30/20 model
# ---------------------------------------------------------------------------
NEEDS_PCT = 0.50
WANTS_PCT = 0.30
SAVINGS_PCT = 0.20

BUCKETS = ("needs", "wants", "savings")

# Savings sub-goal ordering (user's chosen waterfall):
# buffer -> debt -> emergency fund -> vehicle sinking fund -> invest
GOAL_ORDER = ("buffer", "debt", "fund", "vehicle", "invest")
GOAL_ORDER_LABEL = {
    "buffer": "Starter buffer",
    "debt": "Debt paydown",
    "fund": "Emergency fund",
    "vehicle": "Vehicle fund",
    "invest": "Investments",
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        c = conn.cursor()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                monthly_net_income REAL NOT NULL DEFAULT 0,
                pay_frequency TEXT NOT NULL DEFAULT 'biweekly',
                emergency_fund_months INTEGER NOT NULL DEFAULT 6,
                buffer_target REAL NOT NULL DEFAULT 1500,
                vehicle_target REAL NOT NULL DEFAULT 97000,
                savings_upgraded INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'GHS'
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                bucket TEXT NOT NULL,                 -- needs | wants | savings
                planned_monthly REAL NOT NULL DEFAULT 0,
                cadence TEXT NOT NULL DEFAULT 'monthly',   -- monthly | daily
                is_variable INTEGER NOT NULL DEFAULT 0,
                daily_min REAL,
                daily_max REAL,
                goal_kind TEXT,                       -- buffer|debt|fund|vehicle|invest|NULL (savings semantics)
                weight REAL NOT NULL DEFAULT 1,       -- priority weight for the weighted split
                is_fixed INTEGER NOT NULL DEFAULT 0,  -- fixed-cost item (funded before the weighted split)
                target_amount REAL,                   -- optional savings goal target (progress/ETA)
                sort_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                tx_date TEXT NOT NULL,                -- YYYY-MM-DD
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS incomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL,
                pay_date TEXT NOT NULL,               -- YYYY-MM-DD
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                original_balance REAL NOT NULL,
                apr REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                why TEXT NOT NULL,
                link TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                learned INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # --- lightweight migrations for pre-existing databases ---
        cols = {r[1] for r in c.execute("PRAGMA table_info(categories)").fetchall()}
        if "weight" not in cols:
            c.execute("ALTER TABLE categories ADD COLUMN weight REAL NOT NULL DEFAULT 1")
        if "is_fixed" not in cols:
            c.execute("ALTER TABLE categories ADD COLUMN is_fixed INTEGER NOT NULL DEFAULT 0")
        if "target_amount" not in cols:
            c.execute("ALTER TABLE categories ADD COLUMN target_amount REAL")
        scols = {r[1] for r in c.execute("PRAGMA table_info(settings)").fetchall()}
        if "vehicle_target" not in scols:
            c.execute("ALTER TABLE settings ADD COLUMN vehicle_target REAL NOT NULL DEFAULT 97000")
        if "savings_upgraded" not in scols:
            c.execute("ALTER TABLE settings ADD COLUMN savings_upgraded INTEGER NOT NULL DEFAULT 0")
        seed_if_empty(conn)
        upgrade_savings_model(conn)


def seed_if_empty(conn: sqlite3.Connection) -> None:
    c = conn.cursor()

    # settings
    if c.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        c.execute(
            "INSERT INTO settings (id, monthly_net_income, pay_frequency, "
            "emergency_fund_months, buffer_target, vehicle_target, savings_upgraded, currency) "
            "VALUES (1, 0, 'biweekly', 6, 1500, 97000, 1, 'GHS')"
        )

    # categories
    if c.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        needs = [
            # name, planned, cadence, is_variable, dmin, dmax
            ("Rent", 1000, "monthly", 0, None, None),
            ("Water", 100, "monthly", 0, None, None),
            ("Garbage disposal", 150, "monthly", 0, None, None),
            ("Electricity", 800, "monthly", 0, None, None),
            ("Food / Meals", 2000, "daily", 1, 100, 100),   # ~₵100/day × 20 eat-out days (cooks ~2×/week)
            ("Groceries", 1000, "monthly", 0, None, None),  # essential cooking ingredients (a Need)
            ("Data", 800, "monthly", 0, None, None),
            ("Transport", 1500, "daily", 1, 4, 170),
        ]
        # Wants: FIXED recurring bills funded first, then flexible lines split by weight.
        # name, weight, is_fixed, fixed_amount
        wants = [
            ("Subscriptions", 0, 1, 250),                       # only fixed Want
            ("Dining out / outings & events", 0.35, 0, 0),      # flexible, highest priority
            ("Room furnishing", 0.22, 0, 0),
            ("Clothing", 0.18, 0, 0),
            ("Hobbies / focus items", 0.10, 0, 0),
        ]
        # Savings: same fixed-first, then weighted engine as Wants — all editable.
        # Buffer + Debt are FIXED monthly amounts (funded first => priority preserved).
        # Emergency fund / Vehicle / Investments are WEIGHTED (split the remainder).
        # name, goal_kind, is_fixed, fixed_amount, weight, target_amount
        savings = [
            ("Starter emergency buffer", "buffer",  1, 500,  0,    1500),
            ("Debt paydown",             "debt",    1, 1500, 0,    None),   # target = debt balance (debts table)
            ("Emergency fund",           "fund",    0, 0,    0.50, None),   # target = months × Needs (dynamic)
            ("Vehicle fund",             "vehicle", 0, 0,    0.30, 97000),
            ("Investments",              "invest",  0, 0,    0.20, None),   # no target (open-ended)
        ]

        order = 0
        for name, planned, cadence, var, dmin, dmax in needs:
            c.execute(
                "INSERT INTO categories (name, bucket, planned_monthly, cadence, is_variable, "
                "daily_min, daily_max, goal_kind, sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
                (name, "needs", planned, cadence, var, dmin, dmax, None, order),
            )
            order += 1
        for name, weight, is_fixed, fixed_amount in wants:
            c.execute(
                "INSERT INTO categories (name, bucket, planned_monthly, cadence, is_variable, "
                "daily_min, daily_max, goal_kind, weight, is_fixed, sort_order) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name, "wants", fixed_amount, "monthly", 0, None, None, None, weight, is_fixed, order),
            )
            order += 1
        for name, goal_kind, is_fixed, fixed_amount, weight, target in savings:
            c.execute(
                "INSERT INTO categories (name, bucket, planned_monthly, cadence, is_variable, "
                "daily_min, daily_max, goal_kind, weight, is_fixed, target_amount, sort_order) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, "savings", fixed_amount, "monthly", 0, None, None, goal_kind,
                 weight, is_fixed, target, order),
            )
            order += 1

    # debt
    if c.execute("SELECT COUNT(*) FROM debts").fetchone()[0] == 0:
        c.execute(
            "INSERT INTO debts (name, original_balance, apr) VALUES (?,?,?)",
            ("Personal debt", 4500, 0),
        )

    # learning
    if c.execute("SELECT COUNT(*) FROM learning").fetchone()[0] == 0:
        for i, entry in enumerate(LEARNING_SEED):
            c.execute(
                "INSERT INTO learning (title, body, why, link, tags, learned, sort_order) "
                "VALUES (?,?,?,?,?,?,?)",
                (entry["title"], entry["body"], entry["why"], entry.get("link", ""),
                 entry.get("tags", ""), 1 if entry.get("learned") else 0, i),
            )


def upgrade_savings_model(conn: sqlite3.Connection) -> None:
    """
    One-time, idempotent upgrade of pre-existing databases to the editable
    fixed/weighted savings model. Sets sensible defaults on the previously
    locked seed goals (matched by goal_kind) without touching transactions,
    income, or any goal the user added. Runs once, guarded by settings flag.
    """
    c = conn.cursor()
    s = c.execute("SELECT savings_upgraded FROM settings WHERE id=1").fetchone()
    if not s or s[0]:
        return  # already upgraded (or fresh seed, which is already correct)

    # goal_kind -> (is_fixed, planned_monthly, weight, target_amount)
    defaults = {
        "buffer":  (1, 500,  0,    1500),
        "debt":    (1, 1500, 0,    None),
        "fund":    (0, 0,    0.50, None),
        "vehicle": (0, 0,    0.30, 97000),
        "invest":  (0, 0,    0.20, None),
    }
    for gk, (isf, planned, weight, target) in defaults.items():
        c.execute(
            "UPDATE categories SET is_fixed=?, planned_monthly=?, weight=?, target_amount=? "
            "WHERE bucket='savings' AND goal_kind=?",
            (isf, planned, weight, target, gk),
        )
    c.execute("UPDATE settings SET savings_upgraded=1 WHERE id=1")


LEARNING_SEED = [
    {
        "title": "The 50/30/20 rule",
        "body": "Split your after-tax income three ways: 50% to Needs (rent, utilities, food, transport), "
                "30% to Wants (things you enjoy but could live without), and 20% to Savings, Debt and "
                "Investments. It is a starting frame, not a law — if your Needs run above 50%, you "
                "adapt by trimming Wants first and protecting the savings that keep you solvent.",
        "why": "This is the backbone of your whole plan. Your fixed Needs are high, so the adaptive engine "
               "reshapes the split around reality while keeping the target visible.",
        "link": "https://www.investopedia.com/ask/answers/022916/what-502030-budget-rule.asp",
        "tags": "budgeting,core",
        "learned": True,
    },
    {
        "title": "Index funds",
        "body": "An index fund holds a little of every company in a market index, so you own the whole market "
                "cheaply instead of betting on single stocks. Low fees and broad diversification make it the "
                "default long-term wealth builder.",
        "why": "You already flagged this. Note for Ghana: US funds like VTSAX are not directly buyable here — "
               "your real on-ramps are local mutual funds and the Ghana Stock Exchange (see the Ghana investing note).",
        "link": "https://www.investopedia.com/terms/i/indexfund.asp",
        "tags": "investing,core",
        "learned": True,
    },
    {
        "title": "Emergency fund sizing",
        "body": "An emergency fund is cash set aside only for genuine emergencies (job loss, medical, urgent "
                "repairs). Size it as a multiple of your monthly Needs — not total spending — because "
                "Needs are what you must cover if income stops. 3 months is a floor; 6 months is solid.",
        "why": "Your target is computed live as 6 × your monthly Needs, and you can dial it from 6 to 12 "
               "months in Settings. Build the small buffer first, then fill this after debt.",
        "link": "https://www.investopedia.com/terms/e/emergency_fund.asp",
        "tags": "saving",
    },
    {
        "title": "Debt avalanche vs snowball",
        "body": "Two payoff strategies. Avalanche pays the highest-interest debt first (cheapest mathematically). "
                "Snowball pays the smallest balance first (fastest emotional wins). Both work; the best one is the "
                "one you stick to.",
        "why": "You have a single ₵4,500 balance, so order does not matter yet — just pay it aggressively. "
               "The dashboard estimates your payoff date from the amount you allocate each month.",
        "link": "https://www.investopedia.com/debt-avalanche-vs-debt-snowball-5225660",
        "tags": "debt",
    },
    {
        "title": "Compound interest",
        "body": "Interest earns interest. Money left to grow compounds on its own past gains, so the curve bends "
                "upward over time. It works against you on debt and for you on investments — which is why "
                "clearing debt and investing early both matter so much.",
        "why": "The same force that makes your ₵4,500 debt costly makes early investing powerful. Time in the "
               "market beats timing it.",
        "link": "https://www.investopedia.com/terms/c/compoundinterest.asp",
        "tags": "core,investing",
    },
    {
        "title": "Dollar-cost averaging",
        "body": "Invest a fixed amount on a regular schedule regardless of price. You buy more units when prices are "
                "low and fewer when high, smoothing out the entry price and removing the temptation to time the market.",
        "why": "You are paid bi-weekly — a natural rhythm for automatic, steady investing contributions from "
               "your 20% bucket.",
        "link": "https://www.investopedia.com/terms/d/dollarcostaveraging.asp",
        "tags": "investing",
    },
    {
        "title": "Sinking funds",
        "body": "A sinking fund saves a little each month toward a known irregular expense (annual insurance, a "
                "device, a trip) so the bill never becomes a shock. It converts lumpy costs into smooth monthly ones.",
        "why": "Perfect for your variable and one-off lines like Room furnishing — set aside monthly instead of "
               "blowing a single paycheck.",
        "link": "https://www.investopedia.com/sinking-fund-5210950",
        "tags": "budgeting,saving",
    },
    {
        "title": "Needs vs Wants",
        "body": "A Need is something you must pay to live and work — rent, utilities, basic food, transport to "
                "your job. A Want improves life but is optional — dining out, hobbies, upgrades. The line is "
                "personal, but being honest about it is what makes a budget hold.",
        "why": "Your engine trims Wants first when Needs run hot, so classifying each cedi correctly is what keeps "
               "you solvent without borrowing.",
        "link": "https://www.investopedia.com/ask/answers/022916/what-502030-budget-rule.asp",
        "tags": "budgeting,core",
    },
    {
        "title": "Lifestyle inflation",
        "body": "When income rises, spending quietly rises to match, so you never feel richer. The fix is to send "
                "raises and windfalls to savings and debt before lifestyle absorbs them.",
        "why": "As your paychecks grow, hold your Needs and Wants steady and let the extra flow into the 20% "
               "bucket — that is how the debt clears and the fund fills faster.",
        "link": "https://www.investopedia.com/terms/l/lifestyle-inflation.asp",
        "tags": "behaviour",
    },
    {
        "title": "Net worth",
        "body": "Net worth is everything you own (cash, investments, savings) minus everything you owe (debt). It is "
                "the single number that tells you whether you are moving forward over time, regardless of income.",
        "why": "Right now your debt drags it down; every payoff and every fund contribution pushes it up. Watch the "
               "trend, not any single month.",
        "link": "https://www.investopedia.com/terms/n/networth.asp",
        "tags": "core",
    },
    {
        "title": "Investing in Ghana (context)",
        "body": "Global index funds (VTSAX, VOO) are not directly purchasable from a Ghanaian bank account. Real "
                "local on-ramps: mutual funds and unit trusts from managers like Databank (e.g. the EPACK / MFund "
                "products) and EDC, treasury bills and bonds via the government / your bank, and equities on the "
                "Ghana Stock Exchange (GSE). Some brokers also offer access to offshore funds for larger accounts. "
                "Always check current fees and minimums.",
        "why": "Your Investments sub-goal should map to instruments you can actually buy in GHS. Start with a local "
               "mutual fund or T-bills once the buffer and debt are handled.",
        "link": "https://gse.com.gh/",
        "tags": "investing,ghana",
    },
]


# ---------------------------------------------------------------------------
# Core financial engine
# ---------------------------------------------------------------------------
def month_bounds(month: str) -> tuple[str, str]:
    """Return (first_day, last_day) inclusive for a 'YYYY-MM' string."""
    y, m = (int(x) for x in month.split("-"))
    first = dt.date(y, m, 1)
    if m == 12:
        nxt = dt.date(y + 1, 1, 1)
    else:
        nxt = dt.date(y, m + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return first.isoformat(), last.isoformat()


def period_of(day_iso: str) -> int:
    """Pay period 1 (days 1-15) or 2 (16+)."""
    d = int(day_iso.split("-")[2])
    return 1 if d <= 15 else 2


def get_settings(conn) -> sqlite3.Row:
    return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()


def compute_adaptive(income: float, needs_planned: float) -> dict:
    """
    The adaptive 50/30/20 engine.

    Ideal = flat 50/30/20 of income.
    Reality = cover fixed Needs first, then trim Wants to absorb any Needs
    overflow while protecting the 20% savings target. If Needs alone exceed
    income the plan is insolvent (guardrail: never require borrowing).
    """
    needs_ideal = income * NEEDS_PCT
    wants_ideal = income * WANTS_PCT
    savings_ideal = income * SAVINGS_PCT

    rec_needs = needs_planned
    remaining = income - rec_needs

    insolvent = False
    shortfall = 0.0

    if remaining < 0:
        # Needs alone exceed income -> cannot balance without borrowing.
        insolvent = True
        shortfall = -remaining
        rec_wants = 0.0
        rec_savings = 0.0
    else:
        # Protect the savings target; Wants absorbs the Needs overflow first.
        rec_savings = min(savings_ideal, remaining)
        rec_wants = remaining - rec_savings
        # If Needs are UNDER target, surplus lands in Wants above its ideal ->
        # redirect that surplus into savings/debt (good financial behaviour).
        if rec_wants > wants_ideal:
            surplus = rec_wants - wants_ideal
            rec_wants = wants_ideal
            rec_savings += surplus

    wants_trim = max(0.0, wants_ideal - rec_wants)
    needs_over_target = max(0.0, rec_needs - needs_ideal)

    if insolvent:
        status = "over"
    elif needs_over_target > 0 or wants_trim > 0:
        status = "tight"
    else:
        status = "black"

    return {
        "income": income,
        "ideal": {"needs": needs_ideal, "wants": wants_ideal, "savings": savings_ideal},
        "recommended": {"needs": rec_needs, "wants": rec_wants, "savings": rec_savings},
        "needs_over_target": needs_over_target,
        "wants_trim": wants_trim,
        "insolvent": insolvent,
        "shortfall": shortfall,
        "status": status,
    }


def compute_fixed_weighted_split(total: float, items: list) -> dict:
    """
    Generic envelope allocation = FIXED-first, then WEIGHTED/proportional.
    Used by BOTH the Wants and the Savings buckets.

    Fixed items (recurring bills, or priority goals like the starter buffer and
    debt paydown) are funded first at their set ₵ amount; the remaining bucket
    budget is split across the flexible/weighted items by priority weight.
    Everything re-normalises to sum EXACTLY to `total`, and scales down
    proportionally when the adaptive engine trims the bucket:
      - total >= fixed total -> fixed funded fully, remainder weighted.
      - total <  fixed total -> fixed scaled down pro-rata, weighted items get 0.
    """
    fixed = [c for c in items if c["is_fixed"]]
    flexible = [c for c in items if not c["is_fixed"]]
    fixed_total = sum(float(c["planned_monthly"]) for c in fixed)

    alloc: dict = {}
    if total >= fixed_total:
        for c in fixed:
            alloc[c["id"]] = round(float(c["planned_monthly"]), 2)
        remaining = total - fixed_total
        weighted = distribute_weighted(
            remaining, [(c["id"], float(c["weight"])) for c in flexible]
        )
        alloc.update(weighted)
    else:
        scale = (total / fixed_total) if fixed_total > 0 else 0.0
        for c in fixed:
            alloc[c["id"]] = round(float(c["planned_monthly"]) * scale, 2)
        for c in flexible:
            alloc[c["id"]] = 0.0
        # absorb rounding drift so the parts still sum exactly to `total`
        drift = round(total - sum(alloc.values()), 2)
        if fixed and abs(drift) >= 0.01:
            alloc[fixed[0]["id"]] = round(alloc[fixed[0]["id"]] + drift, 2)
    return alloc


def distribute_weighted(total: float, items: list[tuple[Any, float]]) -> dict:
    """
    Weighted / proportional budgeting: split `total` across items by their
    relative weights, re-normalised so the parts sum EXACTLY to `total`.

    items: list of (key, weight). Returns {key: amount}.
    Rounding drift is absorbed by the largest-weight item so the sum is exact.
    """
    if not items:
        return {}
    weight_sum = sum(max(0.0, w) for _, w in items)
    if weight_sum <= 0:
        # No positive weights -> fall back to an equal split.
        share = total / len(items)
        return {k: round(share, 2) for k, _ in items}

    raw = {k: total * max(0.0, w) / weight_sum for k, w in items}
    rounded = {k: round(v, 2) for k, v in raw.items()}
    # Absorb rounding remainder into the largest-weight item to keep the sum exact.
    drift = round(total - sum(rounded.values()), 2)
    if abs(drift) >= 0.01:
        biggest = max(items, key=lambda it: it[1])[0]
        rounded[biggest] = round(rounded[biggest] + drift, 2)
    return rounded


def build_dashboard(conn, month: str) -> dict:
    s = get_settings(conn)
    income = float(s["monthly_net_income"])
    first, last = month_bounds(month)

    cats = conn.execute(
        "SELECT * FROM categories WHERE active = 1 ORDER BY sort_order"
    ).fetchall()

    # Planned totals per bucket
    planned = {b: 0.0 for b in BUCKETS}
    for cat in cats:
        planned[cat["bucket"]] += float(cat["planned_monthly"])

    # Actuals from transactions this month, per category & bucket & period
    txns = conn.execute(
        "SELECT t.*, c.bucket, c.name AS cat_name, c.goal_kind FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "WHERE t.tx_date BETWEEN ? AND ?",
        (first, last),
    ).fetchall()

    actual_bucket = {b: 0.0 for b in BUCKETS}
    actual_cat: dict[int, float] = {}
    period_actual = {1: {b: 0.0 for b in BUCKETS}, 2: {b: 0.0 for b in BUCKETS}}
    goal_current = {"buffer": 0.0, "debt": 0.0, "fund": 0.0, "vehicle": 0.0, "invest": 0.0}

    for t in txns:
        b = t["bucket"]
        amt = float(t["amount"])
        actual_bucket[b] += amt
        actual_cat[t["category_id"]] = actual_cat.get(t["category_id"], 0.0) + amt
        period_actual[period_of(t["tx_date"])][b] += amt
        if t["goal_kind"]:
            goal_current[t["goal_kind"]] += amt

    # Debt: original balance minus all debt-goal contributions (all-time)
    debt = conn.execute("SELECT * FROM debts ORDER BY id LIMIT 1").fetchone()
    debt_paid_all = conn.execute(
        "SELECT COALESCE(SUM(t.amount),0) FROM transactions t "
        "JOIN categories c ON c.id = t.category_id WHERE c.goal_kind = 'debt'"
    ).fetchone()[0]
    debt_original = float(debt["original_balance"]) if debt else 0.0
    debt_balance = max(0.0, debt_original - float(debt_paid_all))

    # All-time contributions per savings category (drives progress + ETA)
    all_time_by_cat = {
        row["category_id"]: float(row["total"]) for row in conn.execute(
            "SELECT t.category_id, SUM(t.amount) AS total FROM transactions t "
            "JOIN categories c ON c.id = t.category_id WHERE c.bucket = 'savings' "
            "GROUP BY t.category_id"
        ).fetchall()
    }

    monthly_needs = planned["needs"]
    adaptive = compute_adaptive(income, monthly_needs)
    rec = adaptive["recommended"]

    # ---- Within-bucket sub-allocation ----
    # BOTH Wants and Savings use the SAME fixed-first, then weighted-remainder
    # engine, and both scale with the adaptive 20% / trim logic.
    wants_cats = [c for c in cats if c["bucket"] == "wants"]
    wants_alloc = compute_fixed_weighted_split(rec["wants"], wants_cats)

    savings_cats = [c for c in cats if c["bucket"] == "savings"]
    savings_alloc = compute_fixed_weighted_split(rec["savings"], savings_cats)

    # Resolve each savings goal's target: dynamic for the emergency fund
    # (months × Needs) and debt (its balance), stored target_amount otherwise.
    def savings_target(cat):
        gk = cat["goal_kind"]
        if gk == "fund":
            return float(s["emergency_fund_months"]) * monthly_needs
        if gk == "debt":
            return debt_original
        return float(cat["target_amount"]) if cat["target_amount"] is not None else None

    # Debt payoff estimate from the debt goal's monthly allocation
    debt_cat = next((c for c in savings_cats if c["goal_kind"] == "debt"), None)
    monthly_debt_alloc = round(savings_alloc.get(debt_cat["id"], 0.0), 2) if debt_cat else 0.0
    if debt_balance <= 0:
        payoff_months, payoff_date = 0, None
    elif monthly_debt_alloc > 0:
        payoff_months = math.ceil(debt_balance / monthly_debt_alloc)
        y, m = (int(x) for x in month.split("-"))
        total = (m - 1) + payoff_months
        payoff_date = f"{y + total // 12:04d}-{total % 12 + 1:02d}"
    else:
        payoff_months, payoff_date = None, None

    # Suggested cuts when tight/over
    suggestions = []
    if adaptive["insolvent"]:
        suggestions.append(
            f"Needs (₵{monthly_needs:,.0f}) exceed income (₵{income:,.0f}) by "
            f"₵{adaptive['shortfall']:,.0f}. Cut or defer Needs: renegotiate rent, reduce Data, "
            f"or lower the Food/Transport daily caps."
        )
        suggestions.append("Set Wants to ₵0 until income covers Needs.")
    elif adaptive["status"] == "tight":
        if adaptive["wants_trim"] > 0:
            suggestions.append(
                f"Wants trimmed by ₵{adaptive['wants_trim']:,.0f} vs the ideal 30% to keep Needs "
                f"covered and savings intact."
            )
        suggestions.append(
            "Attack the most variable line (Transport) and Dining/outings first if you need more room."
        )

    def _alloc_for(cat) -> float:
        b = cat["bucket"]
        if b == "wants":
            return wants_alloc.get(cat["id"], 0.0)
        if b == "savings":
            return savings_alloc.get(cat["id"], 0.0)
        return float(cat["planned_monthly"])  # needs: fixed real cost

    # Per-category detail
    cat_detail = []
    for cat in cats:
        cid = cat["id"]
        alloc = round(_alloc_for(cat), 2)
        bucket_rec = rec.get(cat["bucket"], 0.0)
        is_sav = cat["bucket"] == "savings"
        tgt = savings_target(cat) if is_sav else None
        cur = all_time_by_cat.get(cid, 0.0) if is_sav else None
        cat_detail.append({
            "id": cid,
            "name": cat["name"],
            "bucket": cat["bucket"],
            "planned": float(cat["planned_monthly"]),
            "alloc": alloc,                          # this month's carved-up amount
            "share": round(alloc / bucket_rec, 4) if bucket_rec else 0,
            "weight": float(cat["weight"]),
            "is_fixed": bool(cat["is_fixed"]),
            "target_amount": cat["target_amount"],
            "target": round(tgt, 2) if tgt is not None else None,
            "current": round(cur, 2) if cur is not None else None,
            "progress": round(cur / tgt, 4) if (tgt and cur is not None) else None,
            "actual": round(actual_cat.get(cid, 0.0), 2),
            "cadence": cat["cadence"],
            "is_variable": bool(cat["is_variable"]),
            "daily_min": cat["daily_min"],
            "daily_max": cat["daily_max"],
            "goal_kind": cat["goal_kind"],
        })

    # Savings goals breakdown: explicit per-item ₵ (fixed or weighted), plus
    # target / progress / ETA for goals that have a target. In the user's own
    # sort order (fixed items funded first preserves the priority sequence).
    savings_goals = []
    for cat in savings_cats:
        cid = cat["id"]
        galloc = round(savings_alloc.get(cid, 0.0), 2)
        cur = all_time_by_cat.get(cid, 0.0)
        tgt = savings_target(cat)
        remaining = round(max(0.0, tgt - cur), 2) if tgt is not None else None
        # ETA from this goal's monthly allocation (sinking-fund timeline)
        eta_months, eta_date = None, None
        if tgt is not None and remaining and remaining > 0 and galloc > 0:
            eta_months = math.ceil(remaining / galloc)
            y, m = (int(x) for x in month.split("-"))
            total = (m - 1) + eta_months
            eta_date = f"{y + total // 12:04d}-{total % 12 + 1:02d}"
        savings_goals.append({
            "id": cid,
            "goal_kind": cat["goal_kind"],
            "name": cat["name"],
            "is_fixed": bool(cat["is_fixed"]),
            "weight": float(cat["weight"]),
            "planned": float(cat["planned_monthly"]),
            "alloc": galloc,
            "share": round(galloc / rec["savings"], 4) if rec["savings"] else 0,
            "current": round(cur, 2),
            "target": round(tgt, 2) if tgt is not None else None,
            "remaining": remaining,
            "progress": round(cur / tgt, 4) if tgt else None,
            "eta_months": eta_months,
            "eta_date": eta_date,
        })

    def _goal(gk):
        return next((g for g in savings_goals if g["goal_kind"] == gk), None)

    return {
        "month": month,
        "currency": s["currency"],
        "income": income,
        "emergency_fund_months": s["emergency_fund_months"],
        "buckets": [
            {
                "bucket": b,
                "planned": round(planned[b], 2),
                "actual": round(actual_bucket[b], 2),
                "ideal": round(adaptive["ideal"][b], 2),
                "recommended": round(adaptive["recommended"][b], 2),
                "period1_actual": round(period_actual[1][b], 2),
                "period2_actual": round(period_actual[2][b], 2),
            }
            for b in BUCKETS
        ],
        "adaptive": adaptive,
        "wants_alloc": {str(k): round(v, 2) for k, v in wants_alloc.items()},
        "savings_alloc": {str(k): round(v, 2) for k, v in savings_alloc.items()},
        "savings_goals": savings_goals,
        "debt": {
            "name": debt["name"] if debt else "Debt",
            "original": debt_original,
            "balance": round(debt_balance, 2),
            "paid": round(float(debt_paid_all), 2),
            "monthly_alloc": round(monthly_debt_alloc, 2),
            "payoff_months": payoff_months,
            "payoff_date": payoff_date,
            "progress": round((debt_original - debt_balance) / debt_original, 4) if debt_original else 0,
        },
        "emergency_fund": (lambda g: {
            "current": g["current"], "target": g["target"] or 0,
            "months": s["emergency_fund_months"],
            "progress": g["progress"] or 0,
        } if g else {"current": 0, "target": 0, "months": s["emergency_fund_months"], "progress": 0})(_goal("fund")),
        "buffer": (lambda g: {
            "current": g["current"], "target": g["target"] or 0, "progress": g["progress"] or 0,
        } if g else {"current": 0, "target": 0, "progress": 0})(_goal("buffer")),
        "vehicle": _goal("vehicle"),
        "investments": (lambda g: {"current": g["current"]} if g else {"current": 0})(_goal("invest")),
        "period_view": {
            "half_income": round(income / 2, 2),
            "period1": {b: round(period_actual[1][b], 2) for b in BUCKETS},
            "period2": {b: round(period_actual[2][b], 2) for b in BUCKETS},
        },
        "suggestions": suggestions,
        "categories": cat_detail,
        "status": adaptive["status"],
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Budget App", version="1.0.0")


@app.on_event("startup")
def _startup():
    init_db()


# ---- Pydantic models ----
class SettingsIn(BaseModel):
    monthly_net_income: Optional[float] = None
    biweekly_paycheck: Optional[float] = None  # convenience: sets monthly = x2
    emergency_fund_months: Optional[int] = None
    buffer_target: Optional[float] = None
    vehicle_target: Optional[float] = None
    pay_frequency: Optional[str] = None


class CategoryIn(BaseModel):
    name: str
    bucket: str
    planned_monthly: float = 0
    cadence: str = "monthly"
    is_variable: bool = False
    daily_min: Optional[float] = None
    daily_max: Optional[float] = None
    goal_kind: Optional[str] = None
    weight: float = 1
    is_fixed: bool = False
    target_amount: Optional[float] = None


class CategoryPatch(BaseModel):
    name: Optional[str] = None
    planned_monthly: Optional[float] = None
    cadence: Optional[str] = None
    is_variable: Optional[bool] = None
    daily_min: Optional[float] = None
    daily_max: Optional[float] = None
    weight: Optional[float] = None
    is_fixed: Optional[bool] = None
    target_amount: Optional[float] = None
    active: Optional[bool] = None


class TransactionIn(BaseModel):
    category_id: int
    amount: float
    tx_date: Optional[str] = None
    note: str = ""


class IncomeIn(BaseModel):
    amount: float
    pay_date: Optional[str] = None
    note: str = ""


class LearningPatch(BaseModel):
    learned: Optional[bool] = None


# ---- Settings ----
@app.get("/api/settings")
def api_get_settings():
    with get_db() as conn:
        s = get_settings(conn)
        return dict(s)


@app.put("/api/settings")
def api_put_settings(body: SettingsIn):
    with get_db() as conn:
        s = get_settings(conn)
        income = s["monthly_net_income"]
        if body.biweekly_paycheck is not None:
            income = body.biweekly_paycheck * 2
        if body.monthly_net_income is not None:
            income = body.monthly_net_income
        conn.execute(
            "UPDATE settings SET monthly_net_income=?, emergency_fund_months=?, "
            "buffer_target=?, vehicle_target=?, pay_frequency=? WHERE id=1",
            (
                income,
                body.emergency_fund_months if body.emergency_fund_months is not None else s["emergency_fund_months"],
                body.buffer_target if body.buffer_target is not None else s["buffer_target"],
                body.vehicle_target if body.vehicle_target is not None else s["vehicle_target"],
                body.pay_frequency if body.pay_frequency is not None else s["pay_frequency"],
            ),
        )
        return dict(get_settings(conn))


# ---- Categories ----
@app.get("/api/categories")
def api_categories():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM categories WHERE active = 1 ORDER BY sort_order"
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/categories")
def api_add_category(body: CategoryIn):
    if body.bucket not in BUCKETS:
        raise HTTPException(400, "bucket must be needs|wants|savings")
    with get_db() as conn:
        maxo = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM categories").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO categories (name, bucket, planned_monthly, cadence, is_variable, "
            "daily_min, daily_max, goal_kind, weight, is_fixed, target_amount, sort_order) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.name, body.bucket, body.planned_monthly, body.cadence, int(body.is_variable),
             body.daily_min, body.daily_max, body.goal_kind, body.weight, int(body.is_fixed),
             body.target_amount, maxo + 1),
        )
        row = conn.execute("SELECT * FROM categories WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


@app.put("/api/categories/{cat_id}")
def api_update_category(cat_id: int, body: CategoryPatch):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
        if not row:
            raise HTTPException(404, "category not found")
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return dict(row)
        sets, vals = [], []
        for k, v in fields.items():
            if k in ("is_variable", "active", "is_fixed"):
                v = int(bool(v))
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(cat_id)
        conn.execute(f"UPDATE categories SET {', '.join(sets)} WHERE id=?", vals)
        return dict(conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone())


@app.delete("/api/categories/{cat_id}")
def api_delete_category(cat_id: int):
    with get_db() as conn:
        # Soft-delete to preserve historical transactions
        conn.execute("UPDATE categories SET active = 0 WHERE id=?", (cat_id,))
        return {"ok": True}


# ---- Transactions ----
@app.get("/api/transactions")
def api_transactions(month: Optional[str] = None):
    with get_db() as conn:
        if month:
            first, last = month_bounds(month)
            rows = conn.execute(
                "SELECT t.*, c.name AS category_name, c.bucket FROM transactions t "
                "JOIN categories c ON c.id=t.category_id "
                "WHERE t.tx_date BETWEEN ? AND ? ORDER BY t.tx_date DESC, t.id DESC",
                (first, last),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT t.*, c.name AS category_name, c.bucket FROM transactions t "
                "JOIN categories c ON c.id=t.category_id ORDER BY t.tx_date DESC, t.id DESC LIMIT 200"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["period"] = period_of(r["tx_date"])
            out.append(d)
        return out


@app.post("/api/transactions")
def api_add_transaction(body: TransactionIn):
    with get_db() as conn:
        cat = conn.execute("SELECT id FROM categories WHERE id=?", (body.category_id,)).fetchone()
        if not cat:
            raise HTTPException(404, "category not found")
        date = body.tx_date or dt.date.today().isoformat()
        now = dt.datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO transactions (category_id, amount, tx_date, note, created_at) VALUES (?,?,?,?,?)",
            (body.category_id, body.amount, date, body.note, now),
        )
        return dict(conn.execute("SELECT * FROM transactions WHERE id=?", (cur.lastrowid,)).fetchone())


@app.delete("/api/transactions/{tx_id}")
def api_delete_transaction(tx_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
        return {"ok": True}


# ---- Income (bi-weekly paychecks) ----
@app.get("/api/incomes")
def api_incomes(month: Optional[str] = None):
    with get_db() as conn:
        if month:
            first, last = month_bounds(month)
            rows = conn.execute(
                "SELECT * FROM incomes WHERE pay_date BETWEEN ? AND ? ORDER BY pay_date DESC",
                (first, last),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM incomes ORDER BY pay_date DESC LIMIT 100").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["period"] = period_of(r["pay_date"])
            out.append(d)
        return out


@app.post("/api/incomes")
def api_add_income(body: IncomeIn):
    with get_db() as conn:
        date = body.pay_date or dt.date.today().isoformat()
        now = dt.datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO incomes (amount, pay_date, note, created_at) VALUES (?,?,?,?)",
            (body.amount, date, body.note, now),
        )
        return dict(conn.execute("SELECT * FROM incomes WHERE id=?", (cur.lastrowid,)).fetchone())


@app.delete("/api/incomes/{inc_id}")
def api_delete_income(inc_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM incomes WHERE id=?", (inc_id,))
        return {"ok": True}


# ---- Debt ----
@app.get("/api/debt")
def api_debt():
    with get_db() as conn:
        debt = conn.execute("SELECT * FROM debts ORDER BY id LIMIT 1").fetchone()
        if not debt:
            return {}
        paid = conn.execute(
            "SELECT COALESCE(SUM(t.amount),0) FROM transactions t "
            "JOIN categories c ON c.id=t.category_id WHERE c.goal_kind='debt'"
        ).fetchone()[0]
        d = dict(debt)
        d["paid"] = float(paid)
        d["balance"] = max(0.0, float(debt["original_balance"]) - float(paid))
        return d


class DebtIn(BaseModel):
    name: Optional[str] = None
    original_balance: Optional[float] = None
    apr: Optional[float] = None


@app.put("/api/debt")
def api_update_debt(body: DebtIn):
    with get_db() as conn:
        debt = conn.execute("SELECT * FROM debts ORDER BY id LIMIT 1").fetchone()
        if not debt:
            raise HTTPException(404, "no debt record")
        conn.execute(
            "UPDATE debts SET name=?, original_balance=?, apr=? WHERE id=?",
            (
                body.name if body.name is not None else debt["name"],
                body.original_balance if body.original_balance is not None else debt["original_balance"],
                body.apr if body.apr is not None else debt["apr"],
                debt["id"],
            ),
        )
        return dict(conn.execute("SELECT * FROM debts WHERE id=?", (debt["id"],)).fetchone())


# ---- Dashboard ----
@app.get("/api/dashboard")
def api_dashboard(month: Optional[str] = None):
    month = month or dt.date.today().strftime("%Y-%m")
    with get_db() as conn:
        return build_dashboard(conn, month)


# ---- Audits ----
@app.get("/api/audit/{kind}")
def api_audit(kind: str, month: Optional[str] = None, date: Optional[str] = None):
    month = month or dt.date.today().strftime("%Y-%m")
    if kind not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, "kind must be daily|weekly|monthly")
    with get_db() as conn:
        dash = build_dashboard(conn, month)
        s = get_settings(conn)

        if kind == "daily":
            day = date or dt.date.today().isoformat()
            rows = conn.execute(
                "SELECT t.*, c.name AS category_name, c.bucket FROM transactions t "
                "JOIN categories c ON c.id=t.category_id WHERE t.tx_date=? ORDER BY t.id DESC",
                (day,),
            ).fetchall()
            spent = sum(float(r["amount"]) for r in rows if r["bucket"] != "savings")
            saved = sum(float(r["amount"]) for r in rows if r["bucket"] == "savings")
            # daily food/transport pace
            variable = conn.execute(
                "SELECT * FROM categories WHERE is_variable=1 AND active=1"
            ).fetchall()
            pace = []
            for cat in variable:
                today_amt = sum(float(r["amount"]) for r in rows if r["category_id"] == cat["id"])
                daily_target = float(cat["planned_monthly"]) / 30.0
                pace.append({
                    "name": cat["name"],
                    "today": round(today_amt, 2),
                    "daily_target": round(daily_target, 2),
                    "daily_min": cat["daily_min"],
                    "daily_max": cat["daily_max"],
                    "over": today_amt > daily_target,
                })
            return {
                "kind": "daily", "date": day, "month": month,
                "logged": [dict(r) for r in rows],
                "spent": round(spent, 2), "saved": round(saved, 2),
                "pace": pace,
                "status": dash["status"],
            }

        if kind == "weekly":
            # Use the pay-period containing 'date' (or today)
            ref = date or dt.date.today().isoformat()
            per = period_of(ref)
            first, last = month_bounds(month)
            y, m = (int(x) for x in month.split("-"))
            if per == 1:
                p_first, p_last = f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-15"
            else:
                p_first, p_last = f"{y:04d}-{m:02d}-16", last
            rows = conn.execute(
                "SELECT t.*, c.name AS category_name, c.bucket FROM transactions t "
                "JOIN categories c ON c.id=t.category_id WHERE t.tx_date BETWEEN ? AND ? "
                "ORDER BY t.tx_date DESC",
                (p_first, p_last),
            ).fetchall()
            by_bucket = {b: 0.0 for b in BUCKETS}
            for r in rows:
                by_bucket[r["bucket"]] += float(r["amount"])
            # half-month plan per bucket = recommended / 2
            plan_half = {b["bucket"]: b["recommended"] / 2 for b in dash["buckets"]}
            variance = {b: round(plan_half[b] - by_bucket[b], 2) for b in BUCKETS}
            return {
                "kind": "weekly", "period": per, "month": month,
                "range": [p_first, p_last],
                "by_bucket": {b: round(v, 2) for b, v in by_bucket.items()},
                "plan_half": {b: round(v, 2) for b, v in plan_half.items()},
                "variance": variance,
                "logged": [dict(r) for r in rows],
                "status": dash["status"],
            }

        # monthly close
        variance = []
        rollover = 0.0
        for b in dash["buckets"]:
            var = round(b["recommended"] - b["actual"], 2)
            variance.append({
                "bucket": b["bucket"],
                "recommended": b["recommended"],
                "actual": b["actual"],
                "variance": var,
                "period1": b["period1_actual"],
                "period2": b["period2_actual"],
            })
            if b["bucket"] in ("needs", "wants"):
                # unspent needs/wants can roll into savings
                rollover += max(0.0, var)
        return {
            "kind": "monthly", "month": month,
            "income": dash["income"],
            "variance": variance,
            "rollover_to_savings": round(rollover, 2),
            "debt": dash["debt"],
            "emergency_fund": dash["emergency_fund"],
            "buffer": dash["buffer"],
            "savings_goals": dash["savings_goals"],
            "suggestions": dash["suggestions"],
            "status": dash["status"],
            "adaptive": dash["adaptive"],
        }


# ---- Learning ----
@app.get("/api/learning")
def api_learning():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM learning ORDER BY sort_order").fetchall()
        return [dict(r) for r in rows]


@app.put("/api/learning/{item_id}")
def api_update_learning(item_id: int, body: LearningPatch):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM learning WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        if body.learned is not None:
            conn.execute("UPDATE learning SET learned=? WHERE id=?", (int(body.learned), item_id))
        return dict(conn.execute("SELECT * FROM learning WHERE id=?", (item_id,)).fetchone())


# ---- Static frontend ----
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
