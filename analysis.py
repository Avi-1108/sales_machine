import io, os, re
import numpy as np
import pandas as pd

SALES_HINT = re.compile(r"sales|revenue|amount|total|income|price", re.I)
DATE_HINT = re.compile(r"date|time|day|month|period", re.I)


# ---------- loading ----------
def load(file):
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file, encoding_errors="replace")
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file)
    raise ValueError("Upload a .csv, .xlsx or .xls file.")


def _to_dt(s):
    return pd.to_datetime(s.astype(str), errors="coerce", format="mixed")


def _is_date(col, s):
    if s.dtype.kind in "ifb":
        return False
    if DATE_HINT.search(str(col)):
        return True
    return _to_dt(s.head(200)).notna().mean() > 0.8


def _num(s):
    if s.dtype.kind in "ifb":
        return pd.to_numeric(s)
    return pd.to_numeric(s.astype(str).str.replace(r"[,$€₹\s]", "", regex=True), errors="coerce")


def _outliers(x):
    x = x.dropna()
    if len(x) < 8 or x.nunique() < 6 or (x.is_unique and x.is_monotonic_increasing):
        return None
    q1, q3 = x.quantile(.25), x.quantile(.75)
    iqr = q3 - q1
    if iqr == 0:
        return None
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    n = int(((x < lo) | (x > hi)).sum())
    return {"count": n, "low": float(lo), "high": float(hi)} if n else None


def col_kind(s, c, cols):
    s = s.dropna()
    if c == cols["date"] or _is_date(c, s):
        return "date"
    if s.dtype.kind in "if" or (len(s) and _num(s).notna().mean() > 0.9):
        return "num"
    return "text"


# ---------- data quality ----------
def quality(df):
    out = {"rows": len(df), "cols": df.shape[1],
           "duplicates": int(df.duplicated().sum()),
           "missing": {c: int(n) for c, n in df.isna().sum().items() if n},
           "invalid_dates": {}, "inconsistent": {}, "whitespace": {}, "outliers": {}}
    for c in df.columns:
        s = df[c].dropna()
        if s.empty:
            continue
        if s.dtype.kind not in "ifb":
            rs = s.astype(str)
            n_ws = int(((rs != rs.str.strip()) | rs.str.contains(r"\s{2,}")).sum())
            if n_ws:
                out["whitespace"][c] = n_ws
        if s.dtype.kind in "if" or (s.dtype.kind != "b" and not _is_date(c, s) and _num(s).notna().mean() > 0.9):
            ol = _outliers(_num(s))
            if ol:
                out["outliers"][c] = ol
        if s.dtype.kind in "ifb":
            continue
        st = s.astype(str).str.strip()
        if _is_date(c, s):
            bad = int(_to_dt(st).isna().sum())
            if bad:
                out["invalid_dates"][c] = bad
            pats = st.str.replace(r"\d", "9", regex=True).str.replace(r"[A-Za-z]+", "A", regex=True).nunique()
            if pats > 1:
                out["inconsistent"][c] = f"{pats} different date formats"
            continue
        num = pd.to_numeric(st.str.replace(r"[,$€₹\s]", "", regex=True), errors="coerce")
        if 0.5 < num.notna().mean() < 1:
            out["inconsistent"][c] = f"mixed numbers and text ({int(num.isna().sum())} non-numeric)"
            continue
        groups = s.astype(str).groupby(st.str.lower()).nunique()
        n = int((groups > 1).sum())
        if n:
            out["inconsistent"][c] = f"{n} values with different casing/spacing (e.g. 'North' vs 'north ')"
    return out


# ---------- preparation ----------
def detect(df):
    date = next((c for c in df.columns if _is_date(c, df[c].dropna())), None)
    nums = [c for c in df.columns if c != date and (df[c].dtype.kind in "if" or
            pd.to_numeric(df[c].astype(str).str.replace(r"[,$€₹\s]", "", regex=True), errors="coerce").notna().mean() > 0.9)]
    sales = next((c for c in nums if SALES_HINT.search(str(c))), nums[0] if nums else None)
    cats = [c for c in df.columns if c not in (date, sales) and df[c].dtype.kind in "OU" and 1 < df[c].nunique() <= 50]
    return {"date": date, "sales": sales, "cats": cats}


def prepare(df, cols):
    d = df.copy()
    d[cols["sales"]] = _num(d[cols["sales"]])
    if cols["date"] and d[cols["date"]].dtype.kind != "M":
        d[cols["date"]] = _to_dt(d[cols["date"]])
    return d


def _nan(s):
    return s.astype(object).where(lambda x: x.notna(), np.nan)


def clean(raw, cols, o):
    """Apply ONLY the fixes the user chose in the review step. Returns (dataframe, log)."""
    d, log, n0 = raw.copy(), [], len(raw)
    if o.get("trim"):
        for c in d.columns:
            if d[c].dtype.kind in "OU":
                s = d[c].astype("string").str.strip().str.replace(r"\s{2,}", " ", regex=True)
                d[c] = _nan(s.where(s.str.len() > 0))
        log.append("Trimmed extra spaces; blank cells treated as missing")
    for c, a in o.get("dates", {}).items():
        if a == "Drop rows":
            bad = d[c].notna() & _to_dt(d[c]).isna()
            d = d[~bad].copy()
            log.append(f"Dropped {int(bad.sum()):,} rows with invalid dates in '{c}'")
    for c, on in o.get("formats", {}).items():
        if not on:
            continue
        s, k = d[c], col_kind(d[c], c, cols)
        if k == "date":
            d[c] = _to_dt(s)
        elif k == "num":
            d[c] = _num(s)
        else:
            st = s.astype("string").str.strip()
            best = st.groupby(st.str.lower()).agg(lambda x: x.mode().iloc[0])
            d[c] = _nan(st.str.lower().map(best))
        log.append(f"Standardized formats in '{c}'")
    if o.get("dedupe"):
        k = len(d)
        d = d.drop_duplicates().copy()
        log.append(f"Removed {k - len(d):,} duplicate rows")
    for c, a in o.get("missing", {}).items():
        n = int(d[c].isna().sum())
        if a == "Drop rows":
            d = d[d[c].notna()].copy()
        elif a == "Fill median":
            x = _num(d[c]); d[c] = x.fillna(x.median())
        elif a == "Fill zero":
            d[c] = _num(d[c]).fillna(0)
        elif a == "Fill most common":
            mo = d[c].mode()
            if len(mo):
                d[c] = d[c].fillna(mo.iloc[0])
        elif a == "Fill 'Unknown'":
            d[c] = d[c].fillna("Unknown")
        else:
            continue
        log.append(f"Missing values in '{c}' ({n:,}): {a.lower()}")
    for c, a in o.get("outliers", {}).items():
        if a == "Keep":
            continue
        x = _num(d[c]); b = _outliers(x)
        if not b:
            continue
        out = (x < b["low"]) | (x > b["high"])
        if a == "Remove rows":
            d = d[~out].copy()
        else:
            d[c] = x.clip(b["low"], b["high"])
        log.append(f"Outliers in '{c}' ({b['count']:,}): {a.lower()} (normal range {b['low']:,.2f} to {b['high']:,.2f})")
    if log:
        log.append(f"Rows: {n0:,} -> {len(d):,}")
    return d, log


def monthly(d, cols):
    if not cols["date"]:
        return pd.Series(dtype=float)
    x = d.dropna(subset=[cols["date"], cols["sales"]])
    if x.empty:
        return pd.Series(dtype=float)
    return x.set_index(cols["date"])[cols["sales"]].resample("MS").sum()


# ---------- forecast ----------
def forecast(m, k=6):
    n = len(m)
    if n < 4:
        return pd.DataFrame(columns=["month", "forecast", "low", "high"])
    x = np.arange(n)
    slope, icpt = np.polyfit(x, m.values, 1)
    fit = slope * x + icpt
    resid = m.values - fit
    season = {}
    if n >= 24:
        season = pd.Series(resid, index=m.index.month).groupby(level=0).mean().to_dict()
        resid = resid - np.array([season[mo] for mo in m.index.month])
    sd = resid.std(ddof=1) if n > 2 else 0
    fx = np.arange(n, n + k)
    idx = pd.date_range(m.index[-1] + pd.offsets.MonthBegin(1), periods=k, freq="MS")
    pred = slope * fx + icpt + np.array([season.get(mo, 0) for mo in idx.month])
    pred = np.maximum(pred, 0)
    band = 1.96 * sd * np.sqrt(1 + (np.arange(1, k + 1) / n))
    return pd.DataFrame({"month": idx.strftime("%Y-%m"), "forecast": pred,
                         "low": np.maximum(pred - band, 0), "high": pred + band})


# ---------- insights / report ----------
def insights(d, cols, m):
    s, out = cols["sales"], []
    out.append(f"Total {s}: {d[s].sum():,.0f} across {len(d):,} records (average {d[s].mean():,.2f}).")
    if len(m) >= 2:
        out.append(f"Best month: {m.idxmax():%b %Y} ({m.max():,.0f}); weakest: {m.idxmin():%b %Y} ({m.min():,.0f}).")
        last, prev = m.iloc[-1], m.iloc[-2]
        if prev:
            ch = (last / prev - 1) * 100
            out.append(f"Latest month {'grew' if ch >= 0 else 'fell'} {abs(ch):.1f}% versus the previous month.")
        if len(m) >= 12:
            h1, h2 = m.iloc[:len(m) // 2].mean(), m.iloc[len(m) // 2:].mean()
            out.append(f"Average monthly {s} is {'up' if h2 >= h1 else 'down'} {abs(h2 / h1 - 1) * 100:.1f}% in the second half of the period vs the first.")
        cv = m.std() / m.mean() if m.mean() else 0
        if cv > 0.35:
            out.append(f"Sales are volatile (variation {cv:.0%} of the mean) - consider smoothing demand with promotions or inventory planning.")
    for c in cols["cats"][:4]:
        g = d.groupby(c)[s].sum().sort_values(ascending=False)
        share = g.iloc[0] / g.sum() * 100
        out.append(f"Top {c}: {g.index[0]} contributes {share:.0f}% of {s}; lowest is {g.index[-1]} ({g.iloc[-1] / g.sum() * 100:.1f}%).")
        if share > 50:
            out.append(f"Risk: more than half of revenue depends on one {c} ({g.index[0]}).")
    return out


def build(raw, opts=None):
    cols = detect(raw)
    if not cols["sales"]:
        raise ValueError("No numeric sales/amount column found.")
    q = quality(raw)
    d, log = clean(raw, cols, opts or {})
    d = prepare(d, cols)
    m = monthly(d, cols)
    f = forecast(m)
    rep = {
        "cols": cols, "quality": q, "cleaning": log,
        "kpis": {"total": float(d[cols["sales"]].sum()), "average": float(d[cols["sales"]].mean()), "records": int(len(d))},
        "monthly": [{"month": i.strftime("%Y-%m"), "value": float(v)} for i, v in m.items()],
        "forecast": f.to_dict("records"),
        "by_cat": {c: d.groupby(c)[cols["sales"]].sum().sort_values(ascending=False).head(10).round(2).to_dict() for c in cols["cats"]},
        "insights": insights(d, cols, m),
    }
    return rep, d, m


# ---------- natural-language questions ----------
def ask(question, d, cols, m):
    """Rule-based parser; falls back to an LLM (if ANTHROPIC_API_KEY set) for anything it can't map."""
    ql, s = question.lower(), cols["sales"]
    data = d
    for c in cols["cats"]:  # filter on mentioned values, e.g. "sales in north"
        hit = [v for v in d[c].unique() if str(v).lower() in ql]
        if hit:
            data = data[data[c].isin(hit)]
    y = re.search(r"\b(20\d\d)\b", ql)
    if y and cols["date"]:
        data = data[data[cols["date"]].dt.year == int(y.group(1))]
    n = int(re.search(r"top\s+(\d+)", ql).group(1)) if re.search(r"top\s+(\d+)", ql) else 5
    by = next((c for c in cols["cats"] if re.search(rf"\b{re.escape(c.lower())}", ql)), None)
    if re.search(r"forecast|predict|next|future|expect", ql):
        f = forecast(m)
        return "Projected sales for the next months (linear trend + seasonality):", f
    if by and re.search(r"top|best|highest|most|worst|lowest|least|by|each|per", ql):
        asc = bool(re.search(r"worst|lowest|least|bottom", ql))
        g = data.groupby(by)[s].sum().sort_values(ascending=asc).head(n).reset_index()
        return f"{'Lowest' if asc else 'Top'} {len(g)} {by} by {s}:", g
    if re.search(r"trend|over time|monthly|growth|month", ql) and cols["date"]:
        mm = monthly(data, cols).reset_index()
        mm.columns = ["month", s]
        return "Monthly trend:", mm
    if re.search(r"average|mean|avg", ql):
        return f"Average {s}: **{data[s].mean():,.2f}**", None
    if re.search(r"total|sum|how much|revenue|sales", ql):
        return f"Total {s}: **{data[s].sum():,.2f}** ({len(data):,} records)", None
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            ctx = f"Columns: {list(d.columns)}\nSummary stats:\n{d.describe(include='all').to_string()[:3000]}\nMonthly: {m.tail(24).round(0).to_dict()}"
            r = anthropic.Anthropic().messages.create(
                model="claude-sonnet-5-5", max_tokens=600,
                messages=[{"role": "user", "content": f"{ctx}\n\nAnswer this sales question concisely using only the data above: {question}"}])
            return r.content[0].text, None
        except Exception as e:
            return f"LLM fallback failed: {e}", None
    return "I couldn't map that question. Try: 'top 5 products by sales', 'worst region', 'sales trend 2024', 'forecast', 'total sales in North'.", None


# ---------- exports ----------
def to_excel(rep):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rep["kpis"].items(), columns=["Metric", "Value"]).to_excel(w, sheet_name="Summary", index=False)
        qq = rep["quality"]
        rows = [("Rows", qq["rows"]), ("Columns", qq["cols"]), ("Duplicate rows", qq["duplicates"])]
        rows += [(f"Missing - {k}", v) for k, v in qq["missing"].items()]
        rows += [(f"Invalid dates - {k}", v) for k, v in qq["invalid_dates"].items()]
        rows += [(f"Inconsistent - {k}", v) for k, v in qq["inconsistent"].items()]
        rows += [(f"Whitespace issues - {k}", v) for k, v in qq.get("whitespace", {}).items()]
        rows += [(f"Outliers - {k}", f"{v['count']} outside {v['low']:,.2f} to {v['high']:,.2f}") for k, v in qq.get("outliers", {}).items()]
        rows += [("Cleaning applied", x) for x in rep.get("cleaning", [])]
        pd.DataFrame(rows, columns=["Check", "Result"]).to_excel(w, sheet_name="Data Quality", index=False)
        pd.DataFrame(rep["monthly"]).to_excel(w, sheet_name="Monthly", index=False)
        pd.DataFrame(rep["forecast"]).to_excel(w, sheet_name="Forecast", index=False)
        pd.DataFrame({"Insight": rep["insights"]}).to_excel(w, sheet_name="Insights", index=False)
        for c, v in rep["by_cat"].items():
            pd.DataFrame(v.items(), columns=[c, "Sales"]).to_excel(w, sheet_name=f"By {c}"[:31], index=False)
    return buf.getvalue()


def to_pdf(rep, title="Sales Analysis Report"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    st, buf, el = getSampleStyleSheet(), io.BytesIO(), []
    el += [Paragraph(title, st["Title"]),
           Paragraph(f"Total: {rep['kpis']['total']:,.0f} | Average: {rep['kpis']['average']:,.2f} | Records: {rep['kpis']['records']:,}", st["Normal"]),
           Spacer(1, 12)]
    if rep["monthly"]:
        fig, ax = plt.subplots(figsize=(7, 3))
        mo = pd.DataFrame(rep["monthly"])
        ax.plot(mo["month"], mo["value"], label="Actual")
        if rep["forecast"]:
            fc = pd.DataFrame(rep["forecast"])
            ax.plot(fc["month"], fc["forecast"], "--", label="Forecast")
        ax.legend(); ax.tick_params(axis="x", rotation=60, labelsize=6); fig.tight_layout()
        ib = io.BytesIO(); fig.savefig(ib, format="png", dpi=150); plt.close(fig); ib.seek(0)
        el += [Image(ib, width=480, height=205), Spacer(1, 12)]
    el.append(Paragraph("Business Insights", st["Heading2"]))
    el += [Paragraph("• " + i, st["Normal"]) for i in rep["insights"]]
    q = rep["quality"]
    el += [Spacer(1, 12), Paragraph("Data Quality", st["Heading2"])]
    rows = [["Check", "Result"], ["Rows / Columns", f"{q['rows']} / {q['cols']}"], ["Duplicates", q["duplicates"]]]
    rows += [[f"Missing: {k}", v] for k, v in q["missing"].items()]
    rows += [[f"Invalid dates: {k}", v] for k, v in q["invalid_dates"].items()]
    rows += [[f"Inconsistent: {k}", Paragraph(v, st["Normal"])] for k, v in q["inconsistent"].items()]
    rows += [[f"Whitespace: {k}", v] for k, v in q.get("whitespace", {}).items()]
    rows += [[f"Outliers: {k}", f"{v['count']} outside {v['low']:,.2f} to {v['high']:,.2f}"] for k, v in q.get("outliers", {}).items()]
    rows += [["Cleaning applied", Paragraph(x, st["Normal"])] for x in rep.get("cleaning", [])]
    t = Table(rows, colWidths=[200, 270])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("GRID", (0, 0), (-1, -1), .3, colors.grey)]))
    el.append(t)
    SimpleDocTemplate(buf, pagesize=A4).build(el)
    return buf.getvalue()
