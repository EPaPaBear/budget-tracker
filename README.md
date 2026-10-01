# Ledger — Personal Budget (50/30/20, adaptive)

A local, single-user budgeting web app for Ghana (GHS, ₵). It runs the **50/30/20 rule**
as an *adaptive* engine: when your fixed Needs exceed 50%, it automatically reallocates to
keep you solvent and shows the ideal split next to your reality.

Stack: **FastAPI + SQLite** (backend), vanilla **HTML/CSS/JS** single-page app (frontend),
served by **uvicorn** on **port 8770**.

## Quick start (Windows)

Double-click **`start.bat`**, or from a terminal:

```bat
start.bat
```

The first run creates a `.venv`, installs dependencies, seeds `budget.db`, launches the
server on http://127.0.0.1:8770, and opens your browser.

### Manual run

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --port 8770 --reload
```

Then open http://127.0.0.1:8770

## First use

The app seeds your categories but **not** your income (nothing is invented). On the
Dashboard, click **Set income** and enter either your monthly net or one bi-weekly
paycheck (it doubles it). The adaptive plan and solvency check activate immediately.

## The financial model

- **Buckets:** 50% Needs / 30% Wants / 20% Savings-Debt-Investments (the *target*).
- **Bi-weekly, aggregatory:** each month = two pay periods; each period is half the
  monthly plan and the two sum to the full month. Dashboard shows month-to-date and
  per-period views.
- **Adaptive engine:** Needs are covered first (they're fixed). Any Needs overflow above
  50% is absorbed by **trimming Wants first**, protecting the 20% savings target. If Needs
  are *under* target, the surplus is redirected into savings/debt.
- **Solvency guardrail:** the plan can never require borrowing. If Needs alone exceed
  income, the dashboard flags **"Over"** with concrete suggested cuts (Wants to zero, then
  defer Needs).
- **Zero-based / envelope:** every cedi is assigned to a bucket.
- **Within-bucket allocation:**
  - *Needs* — fixed real costs (the seeded amounts), including Meals/Food ₵2,000 (≈ ₵100/day ×
    20 eat-out days) and Groceries ₵1,000 (essential cooking ingredients) — ₵3,000 of food
    total, all inside the 50% Needs bucket.
  - *Wants* — **fixed-first, then weighted / proportional**. The one fixed recurring bill
    (Subscriptions ₵250) is funded first at its set amount; the remaining Wants budget is
    split across the flexible lines by **priority weight** (Dining 0.35 · Room furnishing 0.22
    · Clothing 0.18 · Hobbies 0.10 — all editable). Amounts always re-normalize to sum exactly
    to the Wants total and scale down proportionally when the adaptive engine trims Wants (if a
    trim drops below the fixed total, the fixed bill scales down pro-rata and flexible lines go
    to ₵0).
  - *Savings* — the **same fixed-first, then weighted engine as Wants**, fully editable and
    extensible (see below).
- **Savings (fixed + weighted, editable):** every savings goal is user-editable and you can
  add/remove goals. Each goal is allocated by **either** a fixed ₵/mo **or** a weight (share of
  the remaining savings budget). Fixed goals are funded first at their set ₵ (this is how
  priority is preserved), then the weighted goals split the remainder — everything re-normalizes
  to sum exactly to the 20% savings bucket total, and scales with the adaptive engine (if a trim
  drops the bucket below the fixed total, the fixed goals scale down pro-rata and weighted goals
  go to ₵0). Seeded defaults keep good sequencing: **Starter buffer (fixed ₵500) + Debt paydown
  (fixed ₵1,500)** funded first, then **Emergency fund (weight 0.50) · Vehicle fund (0.30) ·
  Investments (0.20)** split the rest — but any goal can be switched fixed↔weighted, retargeted,
  or joined by new goals. Goals with a target show progress + an estimated completion date (ETA).
- **Emergency fund target** = *months × monthly Needs* (default 6, configurable 6–12).
- **Vehicle fund** = a sinking fund for a used car and its associated costs in Ghana
  (default target ₵97,000: ≈ ₵75k for a Toyota Camry 2010–2012 + ₵10k purchase setup / DVLA
  registration + driving school & licence + first-year insurance + a ₵6k car buffer, per the
  jiji_gh CAR_BUYING_GUIDE). Editable in Settings; shows target, progress, and a timeline.
- **Debt payoff estimate** = balance ÷ monthly debt allocation, projected to a payoff month.

## Pages

- **Dashboard** — 50/30/20 rings, plan-vs-actual bars (MTD / per-period), solvency status,
  ideal-vs-reality compare, the weighted **Wants breakdown**, the **savings waterfall**
  (including the vehicle fund), and debt & emergency-fund progress.
- **Allocations** — manage Needs/Wants/Savings envelopes; edit Needs amounts, Wants weights
  (or the fixed ₵ for recurring bills), and see the derived per-line allocations and shares.
- **Quick log** — fast spend entry per category/day, plus paycheck recording.
- **Audits** — daily check, end-of-week review, end-of-month close (aggregates the two
  periods, computes variance, rolls leftover into savings).
- **Learn** — bite-sized finance concepts, Ghana-contextualized, mark as learned.

## Data & files

- `app.py` — FastAPI backend + financial engine
- `static/` — SPA (`index.html`, `style.css`, `app.js`)
- `budget.db` — SQLite database, created and seeded on first run
- `requirements.txt`, `start.bat`

To reset all data, stop the server and delete `budget.db`; it re-seeds on next start.

## Notes / limitations

- Single user, local only — no auth, no cloud sync. Bind is localhost.
- Transport's seeded planned amount (₵1,500) is a placeholder estimate for a highly
  variable line (₵4–170/day) — adjust it in Allocations and log daily actuals.
- Investing entries are informational; Ghana on-ramps (local mutual funds, GSE, Databank/EDC)
  are noted on the Learn page, since US index funds aren't directly buyable here.
