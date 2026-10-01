/* ============================================================
   Ledger — frontend logic
   ============================================================ */

const API = {
  async get(url) { const r = await fetch(url); if (!r.ok) throw new Error(await r.text()); return r.json(); },
  async send(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  post(url, b) { return this.send("POST", url, b); },
  put(url, b) { return this.send("PUT", url, b); },
  del(url) { return this.send("DELETE", url); },
};

// ---------- helpers ----------
const C = "₵";
const fmt = (n) => C + Math.round(n || 0).toLocaleString("en-US");
const fmtSigned = (n) => (n < 0 ? "-" : "+") + C + Math.round(Math.abs(n || 0)).toLocaleString("en-US");
const pct = (n) => Math.round((n || 0) * 100) + "%";
const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const BUCKET_LABEL = { needs: "Needs", wants: "Wants", savings: "Savings / Debt / Invest" };
const BUCKET_PCT = { needs: "50%", wants: "30%", savings: "20%" };
const BUCKET_FILL = { needs: "fill-slate", wants: "fill-accent", savings: "fill-good" };
const GOAL_LABEL = { buffer: "Starter buffer", debt: "Debt paydown", fund: "Emergency fund", vehicle: "Vehicle fund", invest: "Investments" };
const GOAL_FILL = { buffer: "fill-accent", debt: "fill-good", fund: "fill-good", vehicle: "fill-slate", invest: "fill-slate" };

const state = {
  month: new Date().toISOString().slice(0, 7),
  page: "dashboard",
  auditTab: "daily",
  dash: null,
  categories: [],
};

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove("show"), 2200);
}

function monthLabelText(ym) {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" });
}
function shiftMonth(ym, delta) {
  let [y, m] = ym.split("-").map(Number);
  m += delta;
  while (m < 1) { m += 12; y--; }
  while (m > 12) { m -= 12; y++; }
  return `${y}-${String(m).padStart(2, "0")}`;
}

// ---------- theme ----------
function initTheme() {
  const saved = localStorage.getItem("ledger-theme") || "auto";
  applyTheme(saved);
  document.getElementById("themeToggle").addEventListener("click", () => {
    const cur = localStorage.getItem("ledger-theme") || "auto";
    const next = cur === "auto" ? "light" : cur === "light" ? "dark" : "auto";
    localStorage.setItem("ledger-theme", next);
    applyTheme(next);
  });
}
function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", mode);
  document.getElementById("themeLabel").textContent = mode;
}

// ---------- ring svg ----------
function ring(pctVal, label, sub, color) {
  const r = 54, circ = 2 * Math.PI * r;
  const p = clamp(pctVal, 0, 1.5);
  const dash = circ * Math.min(p, 1);
  const over = p > 1;
  const stroke = over ? "var(--bad)" : color;
  return `
    <div class="ring-wrap">
      <div class="ring">
        <svg width="128" height="128" viewBox="0 0 128 128">
          <circle cx="64" cy="64" r="${r}" fill="none" stroke="var(--surface-2)" stroke-width="12"/>
          <circle cx="64" cy="64" r="${r}" fill="none" stroke="${stroke}" stroke-width="12"
            stroke-linecap="round" stroke-dasharray="${dash} ${circ}"/>
        </svg>
        <div class="ring-center">
          <div class="rc-pct">${pct(pctVal)}</div>
          <div class="rc-lbl">${esc(sub)}</div>
        </div>
      </div>
      <div class="ring-cap">${label}</div>
    </div>`;
}

function bar(value, cls, targetPct) {
  const w = clamp(value, 0, 1) * 100;
  const over = value > 1;
  const mark = targetPct != null ? `<span class="target-mark" style="left:${clamp(targetPct,0,1)*100}%"></span>` : "";
  return `<div class="bar"><span class="${over ? "fill-bad" : cls}" style="width:${over ? 100 : w}%"></span>${mark}</div>`;
}

function statusPill(status) {
  if (status === "black") return `<span class="pill good"><span class="dot"></span> In the black</span>`;
  if (status === "tight") return `<span class="pill warn"><span class="dot"></span> Tight — adapted</span>`;
  return `<span class="pill bad"><span class="dot"></span> Over — needs a fix</span>`;
}

// ============================================================
//  DASHBOARD
// ============================================================
async function loadDashboard() {
  const d = await API.get(`/api/dashboard?month=${state.month}`);
  state.dash = d;
  document.getElementById("monthLabel").textContent = monthLabelText(state.month);
  document.getElementById("dashSub").textContent = d.income > 0
    ? "Your adaptive 50/30/20 plan" : "Set your net income to activate the plan";

  if (!d.income) { renderSetupState(); return; }
  renderDashboard(d);
}

function renderSetupState() {
  const d = state.dash;
  const needs = d.buckets.find((b) => b.bucket === "needs").planned;
  document.getElementById("dashBody").innerHTML = `
    <div class="card pad-lg" style="max-width:620px">
      <h2 style="font-size:20px;margin-bottom:6px">Let's set your income</h2>
      <p class="dim" style="margin:0 0 18px">
        Your seeded Needs currently total <span class="money" style="font-weight:700;color:var(--ink)">${fmt(needs)}</span>/month.
        Enter your net (after-tax) monthly income — or one bi-weekly paycheck — and the adaptive
        50/30/20 engine will build the plan and check solvency.
      </p>
      <div class="grid grid-2" style="max-width:460px">
        <label class="fld"><span class="lbl">Monthly net (₵)</span><input type="number" class="num" id="setupMonthly" placeholder="e.g. 12000"/></label>
        <label class="fld"><span class="lbl">— or bi-weekly (₵)</span><input type="number" class="num" id="setupBiweekly" placeholder="e.g. 6000"/></label>
      </div>
      <button class="btn primary mt" id="setupSave">Build my plan</button>
    </div>`;
  document.getElementById("setupSave").addEventListener("click", async () => {
    const monthly = parseFloat(document.getElementById("setupMonthly").value);
    const bi = parseFloat(document.getElementById("setupBiweekly").value);
    const body = {};
    if (!isNaN(bi) && bi > 0) body.biweekly_paycheck = bi;
    else if (!isNaN(monthly) && monthly > 0) body.monthly_net_income = monthly;
    else return toast("Enter an amount first");
    await API.put("/api/settings", body);
    toast("Plan built");
    loadDashboard();
  });
}

function renderDashboard(d) {
  const needs = d.buckets.find((b) => b.bucket === "needs");
  const wants = d.buckets.find((b) => b.bucket === "wants");
  const savings = d.buckets.find((b) => b.bucket === "savings");
  const a = d.adaptive;

  // solvency alert
  let solvency = "";
  if (d.status === "over") {
    solvency = `<div class="alert bad"><div class="ai">${warnIcon()}</div><div>
      <div class="alert-title">Over budget — obligations exceed income</div>
      <div style="font-size:13.5px;margin-top:3px">Your plan would require borrowing. Fix it before spending:</div>
      <ul>${d.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div></div>`;
  } else if (d.status === "tight") {
    solvency = `<div class="alert warn"><div class="ai">${warnIcon()}</div><div>
      <div class="alert-title">Tight — the engine reallocated to keep you solvent</div>
      <ul>${d.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div></div>`;
  } else {
    solvency = `<div class="alert good"><div class="ai">${checkIcon()}</div><div>
      <div class="alert-title">In the black</div>
      <div style="font-size:13.5px;margin-top:3px">Needs fit inside 50%. Every cedi is assigned with savings protected.</div></div></div>`;
  }

  const half = d.period_view.half_income;

  document.getElementById("dashBody").innerHTML = `
    ${solvency}

    <div class="grid grid-4 mt">
      ${statCard("Net income / month", fmt(d.income), `Bi-weekly ${fmt(half)} × 2`, statusPill(d.status))}
      ${statCard("Needs (fixed)", fmt(needs.planned), `Ideal 50% = ${fmt(needs.ideal)}`,
        needs.planned > needs.ideal ? `<span class="pill bad"><span class="dot"></span>${fmtSigned(needs.planned - needs.ideal)} vs ideal</span>` : `<span class="pill good"><span class="dot"></span>within 50%</span>`)}
      ${statCard("Debt balance", fmt(d.debt.balance), d.debt.payoff_date ? `Payoff ~ ${monthLabelText(d.debt.payoff_date)}` : "Allocate to start paydown", `<span class="pill neutral"><span class="dot"></span>${pct(d.debt.progress)} paid</span>`)}
      ${statCard("Emergency fund", fmt(d.emergency_fund.current), `Target ${fmt(d.emergency_fund.target)} · ${d.emergency_fund.months} mo`, `<span class="pill neutral"><span class="dot"></span>${pct(d.emergency_fund.progress)}</span>`)}
    </div>

    <div class="grid grid-2 mt">
      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">The 50 / 30 / 20 split</div><span class="card-hint">recommended allocation</span></div>
        <div class="rings">
          ${ring(needs.recommended / d.income, `<div class="ring-cap"><div class="rn">Needs</div><div class="rv">${fmt(needs.recommended)}</div></div>`, "of net", "var(--slate)")}
          ${ring(wants.recommended / d.income, `<div class="ring-cap"><div class="rn">Wants</div><div class="rv">${fmt(wants.recommended)}</div></div>`, "of net", "var(--accent)")}
          ${ring(savings.recommended / d.income, `<div class="ring-cap"><div class="rn">Savings</div><div class="rv">${fmt(savings.recommended)}</div></div>`, "of net", "var(--good)")}
        </div>
      </div>

      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">Ideal vs. your reality</div><span class="card-hint">adaptive engine</span></div>
        <div class="compare">
          <div>
            <div class="compare-h">Ideal 50 / 30 / 20</div>
            ${["needs","wants","savings"].map((b)=>{const bk=d.buckets.find(x=>x.bucket===b);return `<div class="cmp-row"><span class="cn">${BUCKET_LABEL[b].split(" ")[0]}</span><span class="money">${fmt(bk.ideal)}</span></div>`;}).join("")}
          </div>
          <div>
            <div class="compare-h">Your reality</div>
            ${["needs","wants","savings"].map((b)=>{const bk=d.buckets.find(x=>x.bucket===b);const diff=bk.recommended-bk.ideal;const cls=Math.abs(diff)<1?"":diff<0?"neg":"pos";return `<div class="cmp-row"><span class="cn">${BUCKET_LABEL[b].split(" ")[0]}</span><span class="money ${cls}">${fmt(bk.recommended)}</span></div>`;}).join("")}
          </div>
        </div>
        ${a.wants_trim > 0 ? `<div class="learn-why" style="margin-top:16px">Wants trimmed by <b class="money">${fmt(a.wants_trim)}</b> to absorb Needs running <b class="money">${fmt(a.needs_over_target)}</b> over the 50% target — savings kept whole.</div>` : ""}
      </div>
    </div>

    <div class="card pad-lg mt">
      <div class="card-head">
        <div class="card-title">Buckets — plan vs. actual</div>
        <div class="chip-group" id="periodToggle">
          <button class="chip active" data-per="mtd">Month to date</button>
          <button class="chip" data-per="p1">Period 1</button>
          <button class="chip" data-per="p2">Period 2</button>
        </div>
      </div>
      <div id="bucketRows"></div>
    </div>

    <div class="grid grid-2 mt">
      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">Wants breakdown</div><span class="card-hint">weighted / proportional</span></div>
        <div class="card-hint" style="margin-bottom:8px">Priority-weighted split of your <b class="money" style="color:var(--ink)">${fmt(wants.recommended)}</b> Wants total${a.wants_trim > 0 ? ` (scaled down after a ${fmt(a.wants_trim)} adaptive trim)` : ""}:</div>
        ${wantsBreakdownHTML(d)}
      </div>
      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">Savings breakdown</div><span class="card-hint">fixed + weighted</span></div>
        <div class="card-hint" style="margin-bottom:8px">Fixed goals funded first, then weighted goals split the rest of your <b class="money" style="color:var(--ink)">${fmt(savings.recommended)}</b> monthly savings:</div>
        ${waterfallHTML(d)}
      </div>
    </div>

    <div class="grid grid-2 mt">
      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">Debt paydown</div><span class="card-hint">${esc(d.debt.name)}</span></div>
        ${debtHTML(d.debt)}
      </div>
      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">Goal progress</div><span class="card-hint">funded targets</span></div>
        ${goalsProgressHTML(d)}
      </div>
    </div>
  `;

  renderBucketRows("mtd");
  document.querySelectorAll("#periodToggle .chip").forEach((c) =>
    c.addEventListener("click", () => {
      document.querySelectorAll("#periodToggle .chip").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      renderBucketRows(c.dataset.per);
    }));
}

function renderBucketRows(mode) {
  const d = state.dash;
  const rows = d.buckets.map((b) => {
    let actual, planLabel, planVal;
    if (mode === "mtd") { actual = b.actual; planVal = b.recommended; planLabel = "plan (month)"; }
    else if (mode === "p1") { actual = b.period1_actual; planVal = b.recommended / 2; planLabel = "plan (period)"; }
    else { actual = b.period2_actual; planVal = b.recommended / 2; planLabel = "plan (period)"; }
    const ratio = planVal > 0 ? actual / planVal : 0;
    const over = actual > planVal && planVal > 0;
    const remaining = planVal - actual;
    return `
      <div class="bucket-row">
        <div class="bucket-row-top">
          <span class="bucket-name"><span class="bucket-swatch sw-${b.bucket}"></span>${BUCKET_LABEL[b.bucket]} <span class="tag">${BUCKET_PCT[b.bucket]}</span></span>
          <span><span class="money" style="font-weight:700">${fmt(actual)}</span> <span class="dim" style="font-size:12.5px">/ ${fmt(planVal)} ${planLabel}</span></span>
        </div>
        ${bar(ratio, BUCKET_FILL[b.bucket])}
        <div class="row spread mt-sm" style="font-size:12.5px">
          <span class="dim mono">${pct(ratio)} used</span>
          <span class="${over ? "neg" : "pos"} mono">${over ? fmtSigned(remaining) + " over" : fmt(remaining) + " left"}</span>
        </div>
      </div>`;
  }).join("");
  document.getElementById("bucketRows").innerHTML = rows;
}

function statCard(label, big, sub, pill) {
  return `<div class="card">
    <div class="row spread"><span class="stat-label">${label}</span>${pill || ""}</div>
    <div class="stat-mid mt-sm money">${big}</div>
    <div class="card-hint mt-sm">${sub}</div>
  </div>`;
}

function wantsBreakdownHTML(d) {
  const wants = (d.categories || []).filter((c) => c.bucket === "wants");
  if (!wants.length) return `<div class="empty">No Wants categories.</div>`;
  return wants.map((c) => {
    const over = c.actual > c.alloc && c.alloc > 0;
    const left = c.alloc - c.actual;
    const tag = c.is_fixed
      ? `<span class="tag daily">fixed bill · funded first</span>`
      : `<span class="tag">weight ${c.weight}</span>`;
    return `
      <div class="bucket-row">
        <div class="bucket-row-top">
          <span class="bucket-name">${esc(c.name)} ${tag}</span>
          <span><span class="money" style="font-weight:700">${fmt(c.alloc)}</span> <span class="dim" style="font-size:12.5px">${pct(c.share)} of Wants</span></span>
        </div>
        ${bar(c.share, c.is_fixed ? "fill-slate" : "fill-accent")}
        <div class="row spread mt-sm" style="font-size:12.5px">
          <span class="dim mono">actual ${fmt(c.actual)}</span>
          <span class="${over ? "neg" : "pos"} mono">${over ? fmtSigned(left) + " over" : fmt(left) + " left"}</span>
        </div>
      </div>`;
  }).join("");
}

function waterfallHTML(d) {
  return (d.savings_goals || []).map((g, i) => {
    const typeTag = g.is_fixed
      ? `<span class="tag daily">fixed</span>`
      : `<span class="tag">weight ${g.weight}</span>`;
    const eta = g.eta_date ? ` · ~${g.eta_months} mo (${monthLabelText(g.eta_date)})` : "";
    let meta, progBar = "";
    if (g.goal_kind === "debt") {
      meta = g.remaining > 0 ? `${fmt(g.remaining)} balance left · ${pct(g.progress)} paid${eta}` : "cleared 🎉";
      progBar = `<div class="mt-sm">${bar(g.progress || 0, "fill-good")}</div>`;
    } else if (g.target != null) {
      meta = `${fmt(g.current)} / ${fmt(g.target)} · ${pct(g.progress)}${eta}`;
      progBar = `<div class="mt-sm">${bar(g.progress || 0, GOAL_FILL[g.goal_kind] || "fill-slate")}</div>`;
    } else {
      meta = `no target · have ${fmt(g.current)}`;
    }
    return `
      <div class="wf-step" style="align-items:flex-start">
        <div class="wf-num">${i + 1}</div>
        <div class="wf-body">
          <div class="row spread">
            <div class="wf-name">${esc(g.name)} ${typeTag}</div>
            <div class="wf-amt ${g.alloc > 0 ? "" : "dim"}">${fmt(g.alloc)}<span class="dim" style="font-size:11px">/mo</span></div>
          </div>
          <div class="wf-meta">${meta}</div>
          ${progBar}
        </div>
      </div>`;
  }).join("");
}

function debtHTML(debt) {
  return `
    <div class="row spread"><span class="stat-label">Balance remaining</span><span class="stat-mid money ${debt.balance>0?"neg":"pos"}">${fmt(debt.balance)}</span></div>
    <div class="mt-sm">${bar(debt.progress, "fill-good")}</div>
    <div class="row spread mt-sm" style="font-size:12.5px">
      <span class="dim mono">${fmt(debt.paid)} paid of ${fmt(debt.original)}</span>
      <span class="mono">${pct(debt.progress)}</span>
    </div>
    <div class="mt-sm card-hint">
      ${debt.balance <= 0 ? "Debt cleared 🎉" :
        debt.monthly_alloc > 0 ? `At ${fmt(debt.monthly_alloc)}/mo → payoff in <b style="color:var(--ink)">${debt.payoff_months} months</b> (~${monthLabelText(debt.payoff_date)})`
        : "No monthly allocation yet — increase income or savings to start paydown."}
    </div>`;
}

function goalProgressHTML(name, cur, target, fill, meta) {
  const r = target > 0 ? cur / target : 0;
  return `
    <div class="row spread" style="font-size:13px"><span style="font-weight:600">${name}</span><span class="money">${fmt(cur)} <span class="dim">/ ${fmt(target)}</span></span></div>
    <div class="mt-sm">${bar(r, fill)}</div>
    <div class="row spread mt-sm" style="font-size:11.5px"><span class="dim">${meta}</span><span class="dim mono">${pct(r)}</span></div>`;
}

function goalsProgressHTML(d) {
  const goals = (d.savings_goals || []).filter((g) => g.target != null && g.goal_kind !== "debt");
  if (!goals.length) return `<div class="empty">No savings goals with a target yet.</div>`;
  return goals.map((g, i) => {
    const meta = g.eta_date ? `~${g.eta_months} mo → ${monthLabelText(g.eta_date)}`
      : (g.remaining > 0 ? `${fmt(g.remaining)} to go` : "target met");
    return `<div class="${i ? "mt" : ""}">${goalProgressHTML(esc(g.name), g.current, g.target, GOAL_FILL[g.goal_kind] || "fill-slate", meta)}</div>`;
  }).join("");
}

// ============================================================
//  ALLOCATIONS
// ============================================================
async function loadCategories() {
  const cats = await API.get("/api/categories");
  state.categories = cats;
  const d = await API.get(`/api/dashboard?month=${state.month}`);
  state.dash = d;
  const byBucket = { needs: [], wants: [], savings: [] };
  cats.forEach((c) => byBucket[c.bucket].push(c));

  const det = {};
  (d.categories || []).forEach((c) => (det[c.id] = c));
  const METHOD = { needs: "fixed real costs", wants: "fixed + weighted", savings: "fixed + weighted" };

  const sections = ["needs", "wants", "savings"].map((b) => {
    const bk = d.buckets.find((x) => x.bucket === b);
    const totalAlloc = byBucket[b].reduce((s, c) => s + ((det[c.id] && det[c.id].alloc) || 0), 0);
    let head, colspan, rows;

    if (b === "needs") {
      head = `<th>Category</th><th class="r">Planned / mo</th><th class="r">Actual (mo)</th><th class="r"></th>`;
      colspan = 4;
      rows = byBucket[b].map((c) => {
        const dd = det[c.id] || {};
        const daily = c.is_variable ? `<span class="tag daily">daily${c.daily_min!=null?` ₵${c.daily_min}–${c.daily_max}`:""}</span>` : "";
        return `<tr>
          <td class="cat-name">${esc(c.name)} ${daily}</td>
          <td class="r"><input type="number" class="num" style="width:110px;text-align:right" data-planned="${c.id}" value="${c.planned_monthly}"/></td>
          <td class="r money">${fmt(dd.actual || 0)}</td>
          <td class="r"><button class="btn ghost sm danger" data-del="${c.id}">Remove</button></td>
        </tr>`;
      }).join("");
    } else if (b === "wants") {
      head = `<th>Category</th><th class="r">Weight / Fixed ₵</th><th class="r">Allocated / mo</th><th class="r">Share</th><th class="r">Actual</th><th class="r"></th>`;
      colspan = 6;
      rows = byBucket[b].map((c) => {
        const dd = det[c.id] || {};
        const control = c.is_fixed
          ? `<span class="tag daily" style="margin-right:6px">fixed</span><input type="number" step="1" min="0" class="num" style="width:88px;text-align:right" data-planned="${c.id}" value="${c.planned_monthly}"/>`
          : `<input type="number" step="0.05" min="0" class="num" style="width:78px;text-align:right" data-weight="${c.id}" value="${c.weight}"/>`;
        return `<tr>
          <td class="cat-name">${esc(c.name)}</td>
          <td class="r">${control}</td>
          <td class="r money" style="font-weight:600">${fmt(dd.alloc || 0)}</td>
          <td class="r mono dim">${pct(dd.share || 0)}</td>
          <td class="r money">${fmt(dd.actual || 0)}</td>
          <td class="r"><button class="btn ghost sm danger" data-del="${c.id}">Remove</button></td>
        </tr>`;
      }).join("");
    } else {
      head = `<th>Goal</th><th class="r">Type</th><th class="r">Fixed ₵ / Weight</th><th class="r">Allocated / mo</th><th class="r">Target</th><th class="r">Progress</th><th class="r"></th>`;
      colspan = 7;
      rows = byBucket[b].map((c) => {
        const dd = det[c.id] || {};
        const g = (d.savings_goals || []).find((x) => x.id === c.id) || {};
        const typeBtn = `<button class="btn ghost sm" data-togglefixed="${c.id}" data-fixed="${c.is_fixed ? 1 : 0}" data-weight="${c.weight}">${c.is_fixed ? "fixed" : "weighted"}</button>`;
        const control = c.is_fixed
          ? `<input type="number" step="1" min="0" class="num" style="width:92px;text-align:right" data-planned="${c.id}" value="${c.planned_monthly}"/>`
          : `<input type="number" step="0.05" min="0" class="num" style="width:78px;text-align:right" data-weight="${c.id}" value="${c.weight}"/>`;
        const dynamicTarget = c.goal_kind === "fund" || c.goal_kind === "debt";
        const targetCell = dynamicTarget
          ? `<span class="mono dim">${g.target != null ? fmt(g.target) : "—"}<div style="font-size:10px">${c.goal_kind === "fund" ? "auto: 6× Needs" : "debt balance"}</div></span>`
          : `<input type="number" step="100" min="0" class="num" style="width:104px;text-align:right" data-target="${c.id}" value="${c.target_amount != null ? c.target_amount : ""}" placeholder="none"/>`;
        const prog = g.progress != null
          ? `${pct(g.progress)}${g.eta_date ? `<div style="font-size:10px" class="dim">~${g.eta_months} mo</div>` : ""}`
          : "—";
        return `<tr>
          <td class="cat-name">${esc(c.name)}${c.goal_kind ? ` <span class="tag">${GOAL_LABEL[c.goal_kind] || ""}</span>` : ""}</td>
          <td class="r">${typeBtn}</td>
          <td class="r">${control}</td>
          <td class="r money" style="font-weight:600">${fmt(dd.alloc || 0)} <span class="dim" style="font-weight:400;font-size:12px">${pct(dd.share || 0)}</span></td>
          <td class="r">${targetCell}</td>
          <td class="r mono dim">${prog}</td>
          <td class="r"><button class="btn ghost sm danger" data-del="${c.id}">Remove</button></td>
        </tr>`;
      }).join("");
    }

    return `<div class="card mt">
      <div class="card-head">
        <div class="card-title"><span class="bucket-swatch sw-${b}" style="display:inline-block;margin-right:8px"></span>${BUCKET_LABEL[b]} <span class="tag">${BUCKET_PCT[b]}</span> <span class="tag">${METHOD[b]}</span></div>
        <div class="row gap-sm">
          <span class="card-hint">allocated</span>
          <span class="money" style="font-weight:700">${fmt(totalAlloc)}</span>
          <span class="dim">/ bucket ${fmt(bk ? bk.recommended : 0)}</span>
        </div>
      </div>
      <table class="tbl">
        <thead><tr>${head}</tr></thead>
        <tbody>${rows || `<tr><td colspan="${colspan}" class="empty">No categories</td></tr>`}</tbody>
      </table>
      ${b === "wants" ? `<div class="card-hint" style="margin-top:10px">Fixed-first, then weighted: fixed recurring bills (e.g. Subscriptions) are funded at their ₵ amount first, then the remaining ${fmt(bk ? bk.recommended : 0)} Wants total is split across the flexible lines by weight — re-normalized to sum exactly, scaling with the adaptive engine.</div>` : ""}
      ${b === "savings" ? `<div class="card-hint" style="margin-top:10px">Fixed-first, then weighted (same engine as Wants): fixed goals (e.g. buffer, debt) are funded at their ₵ amount first — preserving priority — then the remaining ${fmt(bk ? bk.recommended : 0)} savings is split across the weighted goals. Toggle any goal fixed/weighted, edit its target, or add new goals. Emergency-fund target auto-tracks 6× Needs; debt target is its balance.</div>` : ""}
    </div>`;
  }).join("");

  document.getElementById("catBody").innerHTML = sections;

  document.querySelectorAll("[data-planned]").forEach((inp) => {
    inp.addEventListener("change", async () => {
      await API.put(`/api/categories/${inp.dataset.planned}`, { planned_monthly: parseFloat(inp.value) || 0 });
      toast("Saved");
      state.dash = null;
      loadCategories();
    });
  });
  document.querySelectorAll("input[data-weight]").forEach((inp) => {
    inp.addEventListener("change", async () => {
      await API.put(`/api/categories/${inp.dataset.weight}`, { weight: Math.max(0, parseFloat(inp.value) || 0) });
      toast("Weights re-normalized");
      state.dash = null;
      loadCategories();
    });
  });
  document.querySelectorAll("[data-target]").forEach((inp) => {
    inp.addEventListener("change", async () => {
      const raw = inp.value.trim();
      const val = raw === "" ? null : Math.max(0, parseFloat(raw) || 0);
      await API.put(`/api/categories/${inp.dataset.target}`, { target_amount: val });
      toast("Target updated");
      state.dash = null;
      loadCategories();
    });
  });
  document.querySelectorAll("[data-togglefixed]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const nowFixed = btn.dataset.fixed === "1";
      const body = { is_fixed: !nowFixed };
      // switching to weighted with no weight set -> give it a default share
      if (nowFixed && (parseFloat(btn.dataset.weight) || 0) <= 0) body.weight = 1;
      await API.put(`/api/categories/${btn.dataset.togglefixed}`, body);
      toast(nowFixed ? "Now weighted" : "Now fixed ₵");
      state.dash = null;
      loadCategories();
    });
  });
  document.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Remove this category? (past logs are kept)")) return;
      await API.del(`/api/categories/${btn.dataset.del}`);
      toast("Removed");
      state.dash = null;
      loadCategories();
    });
  });
}

function openAddCategory() {
  showModal(`
    <h3>Add category</h3>
    <div class="fields">
      <label class="fld"><span class="lbl">Name</span><input id="ncName" placeholder="e.g. Gym membership"/></label>
      <label class="fld"><span class="lbl">Bucket</span>
        <select id="ncBucket">
          <option value="needs">Needs (50%)</option>
          <option value="wants" selected>Wants (30%)</option>
          <option value="savings">Savings / Debt / Invest (20%)</option>
        </select></label>
      <label class="fld row" id="ncFixedWrap" style="gap:8px;align-items:center"><input type="checkbox" id="ncFixed" style="width:auto"/> <span id="ncFixedLbl">Fixed amount (funded first at a set ₵/mo)</span></label>
      <label class="fld" id="ncPlannedWrap"><span class="lbl">Planned / month (₵)</span><input type="number" class="num" id="ncPlanned" value="0"/></label>
      <label class="fld" id="ncWeightWrap"><span class="lbl">Priority weight (proportional split of remainder)</span><input type="number" step="0.05" min="0" class="num" id="ncWeight" value="1"/></label>
      <label class="fld" id="ncTargetWrap"><span class="lbl">Goal target ₵ (optional — enables progress + ETA)</span><input type="number" step="100" min="0" class="num" id="ncTarget" placeholder="none"/></label>
      <label class="fld row" id="ncVarWrap" style="gap:8px;align-items:center"><input type="checkbox" id="ncVar" style="width:auto"/> <span>Daily-variable (flag for daily logging)</span></label>
    </div>
    <div class="actions">
      <button class="btn ghost" data-close>Cancel</button>
      <button class="btn primary" id="ncSave">Add category</button>
    </div>`);
  const syncFields = () => {
    const b = document.getElementById("ncBucket").value;
    const fixed = document.getElementById("ncFixed").checked;
    const flexOK = b === "wants" || b === "savings";  // both support fixed/weighted
    document.getElementById("ncFixedWrap").style.display = flexOK ? "" : "none";
    document.getElementById("ncFixedLbl").textContent = b === "savings"
      ? "Fixed amount (funded first at a set ₵/mo) — else weighted"
      : "Fixed recurring bill (funded first at a set ₵/mo) — else weighted";
    // Planned ₵ shows for Needs, and for a fixed Want/Savings item.
    document.getElementById("ncPlannedWrap").style.display = (b === "needs" || (flexOK && fixed)) ? "" : "none";
    document.getElementById("ncWeightWrap").style.display = (flexOK && !fixed) ? "" : "none";
    document.getElementById("ncTargetWrap").style.display = (b === "savings") ? "" : "none";
    document.getElementById("ncVarWrap").style.display = (b === "needs" || b === "wants") ? "" : "none";
  };
  document.getElementById("ncBucket").addEventListener("change", syncFields);
  document.getElementById("ncFixed").addEventListener("change", syncFields);
  syncFields();
  document.getElementById("ncSave").addEventListener("click", async () => {
    const name = document.getElementById("ncName").value.trim();
    if (!name) return toast("Name required");
    const bucket = document.getElementById("ncBucket").value;
    const flexOK = bucket === "wants" || bucket === "savings";
    const fixed = flexOK && document.getElementById("ncFixed").checked;
    const targetRaw = document.getElementById("ncTarget").value.trim();
    await API.post("/api/categories", {
      name,
      bucket,
      planned_monthly: parseFloat(document.getElementById("ncPlanned").value) || 0,
      weight: Math.max(0, parseFloat(document.getElementById("ncWeight").value) || 1),
      is_fixed: fixed,
      target_amount: (bucket === "savings" && targetRaw !== "") ? Math.max(0, parseFloat(targetRaw) || 0) : null,
      is_variable: document.getElementById("ncVar").checked,
      cadence: document.getElementById("ncVar").checked ? "daily" : "monthly",
    });
    closeModal();
    toast("Added");
    state.dash = null;
    loadCategories();
  });
}

// ============================================================
//  QUICK LOG
// ============================================================
async function loadLog() {
  const cats = await API.get("/api/categories");
  state.categories = cats;
  const sel = document.getElementById("logCategory");
  sel.innerHTML = ["needs", "wants", "savings"].map((b) => {
    const opts = cats.filter((c) => c.bucket === b).map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    return `<optgroup label="${BUCKET_LABEL[b]}">${opts}</optgroup>`;
  }).join("");
  document.getElementById("logDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("payDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("logMonthHint").textContent = monthLabelText(state.month);
  await refreshLogList();
}

async function refreshLogList() {
  const txns = await API.get(`/api/transactions?month=${state.month}`);
  const el = document.getElementById("logList");
  if (!txns.length) { el.innerHTML = `<div class="empty">No transactions logged this month yet.</div>`; return; }
  el.innerHTML = `<table class="tbl">
    <thead><tr><th>Date</th><th>Category</th><th class="r">Amount</th><th class="r"></th></tr></thead>
    <tbody>${txns.map((t) => `<tr>
      <td class="mono" style="font-size:12.5px">${t.tx_date.slice(5)} <span class="tag">P${t.period}</span></td>
      <td><span class="cat-name">${esc(t.category_name)}</span>${t.note ? `<div class="dim" style="font-size:12px">${esc(t.note)}</div>` : ""}</td>
      <td class="r money" style="font-weight:600;color:${t.bucket==="savings"?"var(--good)":"var(--ink)"}">${fmt(t.amount)}</td>
      <td class="r"><button class="btn ghost sm danger" data-deltx="${t.id}">✕</button></td>
    </tr>`).join("")}</tbody></table>`;
  el.querySelectorAll("[data-deltx]").forEach((b) => b.addEventListener("click", async () => {
    await API.del(`/api/transactions/${b.dataset.deltx}`);
    state.dash = null; refreshLogList(); toast("Deleted");
  }));
}

// ============================================================
//  AUDITS
// ============================================================
async function loadAudit() {
  const kind = state.auditTab;
  const a = await API.get(`/api/audit/${kind}?month=${state.month}`);
  const el = document.getElementById("auditBody");
  if (kind === "daily") el.innerHTML = renderDailyAudit(a);
  else if (kind === "weekly") el.innerHTML = renderWeeklyAudit(a);
  else el.innerHTML = renderMonthlyAudit(a);
}

function renderDailyAudit(a) {
  const paceRows = a.pace.map((p) => `
    <div class="bucket-row">
      <div class="bucket-row-top">
        <span class="bucket-name">${esc(p.name)} ${p.daily_min!=null?`<span class="tag daily">₵${p.daily_min}–${p.daily_max}/day</span>`:""}</span>
        <span><span class="money" style="font-weight:700">${fmt(p.today)}</span> <span class="dim" style="font-size:12.5px">/ ${fmt(p.daily_target)} target</span></span>
      </div>
      ${bar(p.daily_target>0?p.today/p.daily_target:0, "fill-accent")}
      <div class="row spread mt-sm" style="font-size:12.5px"><span class="dim mono">daily pace</span><span class="${p.over?"neg":"pos"} mono">${p.over?"over pace":"on pace"}</span></div>
    </div>`).join("");
  const logged = a.logged.length ? a.logged.map((l)=>`<div class="row spread" style="padding:6px 0;font-size:13.5px;border-bottom:1px solid var(--line)"><span>${esc(l.category_name)}${l.note?` · <span class="dim">${esc(l.note)}</span>`:""}</span><span class="money" style="font-weight:600">${fmt(l.amount)}</span></div>`).join("") : `<div class="empty">Nothing logged for this day.</div>`;
  return `
    <div class="grid grid-2">
      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">Today's check — ${a.date}</div>${statusPill(a.status)}</div>
        <div class="grid grid-2">
          ${statCard("Spent today", fmt(a.spent), "needs + wants", "")}
          ${statCard("Saved today", fmt(a.saved), "toward the 20% bucket", "")}
        </div>
        <div class="mt"><div class="card-title" style="font-size:14px;margin-bottom:6px">Logged today</div>${logged}</div>
      </div>
      <div class="card pad-lg">
        <div class="card-head"><div class="card-title">Variable-line pace</div><span class="card-hint">Food & Transport</span></div>
        ${paceRows || `<div class="empty">No variable lines flagged.</div>`}
      </div>
    </div>`;
}

function renderWeeklyAudit(a) {
  const rows = ["needs","wants","savings"].map((b)=>{
    const v = a.variance[b], over = v < 0;
    return `<tr>
      <td class="cat-name"><span class="bucket-swatch sw-${b}" style="display:inline-block;margin-right:8px"></span>${BUCKET_LABEL[b]}</td>
      <td class="r money">${fmt(a.plan_half[b])}</td>
      <td class="r money">${fmt(a.by_bucket[b])}</td>
      <td class="r money ${over?"neg":"pos"}">${fmtSigned(v)}</td>
    </tr>`;
  }).join("");
  return `
    <div class="card pad-lg">
      <div class="card-head">
        <div class="card-title">Period ${a.period} review · ${a.range[0]} → ${a.range[1]}</div>
        ${statusPill(a.status)}
      </div>
      <p class="card-hint" style="margin-top:-6px;margin-bottom:14px">Each pay period covers half the monthly plan. Positive variance = under budget (money left).</p>
      <table class="tbl">
        <thead><tr><th>Bucket</th><th class="r">Half-month plan</th><th class="r">Actual</th><th class="r">Variance</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderMonthlyAudit(a) {
  const rows = a.variance.map((v)=>{
    const over = v.variance < 0;
    return `<tr>
      <td class="cat-name"><span class="bucket-swatch sw-${v.bucket}" style="display:inline-block;margin-right:8px"></span>${BUCKET_LABEL[v.bucket]}</td>
      <td class="r money">${fmt(v.recommended)}</td>
      <td class="r money">${fmt(v.period1)}</td>
      <td class="r money">${fmt(v.period2)}</td>
      <td class="r money" style="font-weight:700">${fmt(v.actual)}</td>
      <td class="r money ${over?"neg":"pos"}">${fmtSigned(v.variance)}</td>
    </tr>`;
  }).join("");
  const sugg = a.suggestions.length ? `<div class="alert warn mt"><div class="ai">${warnIcon()}</div><div><div class="alert-title">Reoptimization</div><ul>${a.suggestions.map(s=>`<li>${esc(s)}</li>`).join("")}</ul></div></div>` : "";
  return `
    <div class="card pad-lg">
      <div class="card-head"><div class="card-title">Month-end close · ${monthLabelText(a.month)}</div>${statusPill(a.status)}</div>
      <p class="card-hint" style="margin-top:-6px;margin-bottom:14px">Two pay periods aggregated into full monthly actuals vs the adaptive plan.</p>
      <table class="tbl">
        <thead><tr><th>Bucket</th><th class="r">Plan</th><th class="r">Period 1</th><th class="r">Period 2</th><th class="r">Actual</th><th class="r">Variance</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="grid grid-3 mt">
      ${statCard("Rolls into savings", fmt(a.rollover_to_savings), "unspent Needs + Wants", "")}
      ${statCard("Debt balance", fmt(a.debt.balance), a.debt.payoff_date?`payoff ~ ${monthLabelText(a.debt.payoff_date)}`:"—", `<span class="pill neutral"><span class="dot"></span>${pct(a.debt.progress)}</span>`)}
      ${statCard("Emergency fund", fmt(a.emergency_fund.current), `of ${fmt(a.emergency_fund.target)}`, `<span class="pill neutral"><span class="dot"></span>${pct(a.emergency_fund.progress)}</span>`)}
    </div>
    ${sugg}`;
}

// ============================================================
//  LEARN
// ============================================================
async function loadLearn() {
  const items = await API.get("/api/learning");
  const done = items.filter((i) => i.learned).length;
  document.getElementById("learnProgress").innerHTML = `<span class="dot"></span> ${done} / ${items.length} learned`;
  document.getElementById("learnBody").innerHTML = `<div class="learn-grid">${items.map(learnCard).join("")}</div>`;
  document.querySelectorAll("[data-learned]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.learned, on = b.dataset.on === "1";
    await API.put(`/api/learning/${id}`, { learned: !on });
    loadLearn();
  }));
}

function learnCard(i) {
  const tags = (i.tags || "").split(",").filter(Boolean).map((t) => `<span class="tag">${esc(t.trim())}</span>`).join(" ");
  return `<div class="card learn-card ${i.learned ? "done" : ""}">
    <div class="learn-title">${esc(i.title)}</div>
    <div class="learn-body">${esc(i.body)}</div>
    <div class="learn-why"><b>Why it matters to you:</b> ${esc(i.why)}</div>
    <div class="row gap-sm" style="flex-wrap:wrap">${tags}</div>
    <div class="learn-foot">
      ${i.link ? `<a class="learn-link" href="${esc(i.link)}" target="_blank" rel="noopener">Read more →</a>` : "<span></span>"}
      <button class="btn sm ${i.learned ? "" : "primary"}" data-learned="${i.id}" data-on="${i.learned ? 1 : 0}">
        ${i.learned ? "✓ Learned" : "Mark learned"}
      </button>
    </div>
  </div>`;
}

// ============================================================
//  MODAL / ICONS / INCOME
// ============================================================
function showModal(html) {
  document.getElementById("modalRoot").innerHTML = `<div class="modal-backdrop" id="mb"><div class="modal">${html}</div></div>`;
  document.getElementById("mb").addEventListener("click", (e) => { if (e.target.id === "mb") closeModal(); });
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeModal));
}
function closeModal() { document.getElementById("modalRoot").innerHTML = ""; }

function openIncomeModal() {
  const d = state.dash || {};
  showModal(`
    <h3>Income & targets</h3>
    <div class="fields">
      <label class="fld"><span class="lbl">Monthly net income (₵)</span><input type="number" class="num" id="imMonthly" value="${d.income || ""}" placeholder="12000"/></label>
      <div class="card-hint">Paid bi-weekly? Enter one paycheck and we'll double it:</div>
      <label class="fld"><span class="lbl">Bi-weekly paycheck (₵)</span><input type="number" class="num" id="imBi" placeholder="6000"/></label>
      <label class="fld"><span class="lbl">Emergency fund (months of Needs)</span><input type="number" class="num" id="imMonths" value="${d.emergency_fund_months || 6}" min="3" max="12"/></label>
      <div class="card-hint">Per-goal targets (starter buffer, vehicle fund, custom goals) are now edited directly in <b>Allocations → Savings</b>. The emergency-fund target auto-tracks this many months of your Needs.</div>
    </div>
    <div class="actions">
      <button class="btn ghost" data-close>Cancel</button>
      <button class="btn primary" id="imSave">Save</button>
    </div>`);
  document.getElementById("imSave").addEventListener("click", async () => {
    const monthly = parseFloat(document.getElementById("imMonthly").value);
    const bi = parseFloat(document.getElementById("imBi").value);
    const body = {
      emergency_fund_months: parseInt(document.getElementById("imMonths").value) || 6,
    };
    if (!isNaN(bi) && bi > 0) body.biweekly_paycheck = bi;
    else if (!isNaN(monthly) && monthly >= 0) body.monthly_net_income = monthly;
    await API.put("/api/settings", body);
    closeModal();
    state.dash = null;
    toast("Saved");
    loadDashboard();
  });
}

function warnIcon() { return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`; }
function checkIcon() { return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`; }

// ============================================================
//  ROUTING
// ============================================================
function navigate(page) {
  state.page = page;
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.nav === page));
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
  if (page === "dashboard") loadDashboard();
  else if (page === "categories") loadCategories();
  else if (page === "log") loadLog();
  else if (page === "audits") loadAudit();
  else if (page === "learn") loadLearn();
}

function init() {
  initTheme();

  document.querySelectorAll(".nav-item").forEach((n) => n.addEventListener("click", () => navigate(n.dataset.nav)));

  document.getElementById("prevMonth").addEventListener("click", () => { state.month = shiftMonth(state.month, -1); state.dash = null; loadDashboard(); });
  document.getElementById("nextMonth").addEventListener("click", () => { state.month = shiftMonth(state.month, 1); state.dash = null; loadDashboard(); });
  document.getElementById("editIncome").addEventListener("click", openIncomeModal);
  document.getElementById("addCategory").addEventListener("click", openAddCategory);

  document.getElementById("logSubmit").addEventListener("click", async () => {
    const amt = parseFloat(document.getElementById("logAmount").value);
    if (isNaN(amt) || amt <= 0) return toast("Enter an amount");
    await API.post("/api/transactions", {
      category_id: parseInt(document.getElementById("logCategory").value),
      amount: amt,
      tx_date: document.getElementById("logDate").value,
      note: document.getElementById("logNote").value.trim(),
    });
    document.getElementById("logAmount").value = "";
    document.getElementById("logNote").value = "";
    state.dash = null;
    toast("Logged");
    refreshLogList();
  });

  document.getElementById("paySubmit").addEventListener("click", async () => {
    const amt = parseFloat(document.getElementById("payAmount").value);
    if (isNaN(amt) || amt <= 0) return toast("Enter an amount");
    await API.post("/api/incomes", { amount: amt, pay_date: document.getElementById("payDate").value });
    document.getElementById("payAmount").value = "";
    toast("Paycheck recorded");
  });

  document.querySelectorAll("#auditTabs .chip").forEach((c) => c.addEventListener("click", () => {
    document.querySelectorAll("#auditTabs .chip").forEach((x) => x.classList.remove("active"));
    c.classList.add("active");
    state.auditTab = c.dataset.audit;
    loadAudit();
  }));

  loadDashboard();
}

document.addEventListener("DOMContentLoaded", init);
