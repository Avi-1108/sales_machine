import pandas as pd
import plotly.express as px
import streamlit as st
import sales_machine.analysis as an
import sales_machine.db as db

st.set_page_config(page_title="Sales Machine", page_icon="📊", layout="wide")


@st.cache_resource
def _init():
    db.init()
    return True


try:
    _init()
except Exception as e:
    st.error(f"Cannot connect to MySQL: {e}\n\nSet MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB.")
    st.stop()

S = st.session_state


# ---------------- auth ----------------
def auth_page():
    st.title("📊 Sales Machine")
    t1, t2 = st.tabs(["Login", "Register"])
    with t1:
        e, p = st.text_input("Email", key="le"), st.text_input("Password", type="password", key="lp")
        if st.button("Login"):
            u = db.login(e, p)
            if u:
                S.uid = u["id"]; st.rerun()
            st.error("Invalid email or password.")
    with t2:
        e, p = st.text_input("Email", key="re"), st.text_input("Password (min 8 chars)", type="password", key="rp")
        if st.button("Create account"):
            u, err = db.register(e, p)
            if err:
                st.error(err)
            else:
                S.uid = u["id"]; st.rerun()


if "uid" not in S:
    auth_page()
    st.stop()

user = db.get_user(S.uid)
plan = db.PLANS[user["plan"]]
with st.sidebar:
    st.write(f"**{user['email']}**")
    st.caption(f"Plan: {user['plan'].title()}")
    page = st.radio("Menu", ["Analyze", "Saved reports", "Plans"])
    if st.button("Logout"):
        S.clear(); st.rerun()


# ---------------- report rendering ----------------
def show(rep, d=None, m=None, key="r"):
    cols, q = rep["cols"], rep["quality"]
    tabs = st.tabs(["Data quality", "Dashboard", "Insights", "Forecast", "Ask", "Export"])

    with tabs[0]:
        a, b, c, e = st.columns(4)
        a.metric("Rows", f"{q['rows']:,}"); b.metric("Duplicates", q["duplicates"])
        c.metric("Cols with missing", len(q["missing"])); e.metric("Invalid dates", sum(q["invalid_dates"].values()))
        if q["missing"]:
            st.subheader("Missing values")
            st.dataframe(pd.DataFrame(q["missing"].items(), columns=["Column", "Missing"]), hide_index=True)
        if q["invalid_dates"]:
            st.subheader("Invalid dates")
            st.dataframe(pd.DataFrame(q["invalid_dates"].items(), columns=["Column", "Invalid"]), hide_index=True)
        if q["inconsistent"]:
            st.subheader("Inconsistent formats")
            st.dataframe(pd.DataFrame(q["inconsistent"].items(), columns=["Column", "Issue"]), hide_index=True)
        if q.get("whitespace"):
            st.subheader("Whitespace issues")
            st.dataframe(pd.DataFrame(q["whitespace"].items(), columns=["Column", "Cells"]), hide_index=True)
        if q.get("outliers"):
            st.subheader("Outliers")
            st.dataframe(pd.DataFrame([{"Column": k, "Outliers": v["count"], "Normal range": f"{v['low']:,.2f} to {v['high']:,.2f}"} for k, v in q["outliers"].items()]), hide_index=True)
        if not any(q.get(k) for k in ("missing", "duplicates", "invalid_dates", "inconsistent", "whitespace", "outliers")):
            st.success("No data quality issues found.")
        st.subheader("Cleaning applied")
        for x in rep.get("cleaning") or ["Nothing changed - analysis uses your data as uploaded."]:
            st.markdown(f"- {x}")

    with tabs[1]:
        k = rep["kpis"]
        a, b, c = st.columns(3)
        a.metric("Total", f"{k['total']:,.0f}"); b.metric("Average", f"{k['average']:,.2f}"); c.metric("Records", f"{k['records']:,}")
        if rep["monthly"]:
            st.plotly_chart(px.line(pd.DataFrame(rep["monthly"]), x="month", y="value", markers=True, title="Monthly sales"), use_container_width=True)
        cc = st.columns(2)
        for i, (col, vals) in enumerate(rep["by_cat"].items()):
            df = pd.DataFrame(vals.items(), columns=[col, "Sales"])
            cc[i % 2].plotly_chart(px.bar(df, x=col, y="Sales", title=f"Sales by {col}"), use_container_width=True)

    with tabs[2]:
        for i in rep["insights"]:
            st.markdown(f"- {i}")

    with tabs[3]:
        if rep["forecast"]:
            hist = pd.DataFrame(rep["monthly"]).rename(columns={"value": "Actual"})
            fc = pd.DataFrame(rep["forecast"])
            fig = px.line(hist, x="month", y="Actual")
            fig.add_scatter(x=fc["month"], y=fc["forecast"], name="Forecast", line=dict(dash="dash"))
            fig.add_scatter(x=fc["month"], y=fc["high"], name="High", line=dict(width=0), showlegend=False)
            fig.add_scatter(x=fc["month"], y=fc["low"], name="95% range", fill="tonexty", line=dict(width=0))
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(fc.round(0), hide_index=True)
            st.caption("Linear trend (plus monthly seasonality when 24+ months exist). Treat as an estimate, not a guarantee.")
        else:
            st.info("Need a date column and at least 4 months of data to forecast.")

    with tabs[4]:
        if d is None:
            st.info("Natural-language questions need the original data. Re-upload the file to enable this.")
        else:
            qn = st.text_input("Ask about your sales", placeholder="Top 5 products by sales in 2024", key=f"{key}q")
            if qn:
                txt, df = an.ask(qn, d, cols, m)
                st.markdown(txt)
                if df is not None:
                    st.dataframe(df, hide_index=True)

    with tabs[5]:
        c1, c2 = st.columns(2)
        c1.download_button("⬇ Excel", an.to_excel(rep), "report.xlsx", key=f"{key}x")
        c2.download_button("⬇ PDF", an.to_pdf(rep), "report.pdf", key=f"{key}p")


# ---------------- step 1: data quality review ----------------
def has_issues(q):
    return any(q[k] for k in ("duplicates", "missing", "invalid_dates", "inconsistent", "whitespace", "outliers"))


def _kind(c):
    return an.col_kind(S.raw[c], c, S.cols0)


MISS_OPTS = {"date": ["Keep", "Drop rows"],
             "num": ["Keep", "Drop rows", "Fill median", "Fill zero"],
             "text": ["Keep", "Drop rows", "Fill most common", "Fill 'Unknown'"]}


def recommend():
    q = S.q
    S.o_dd, S.o_trim = True, True
    for c in q["missing"]:
        k = _kind(c)
        S[f"m_{c}"] = "Drop rows" if (k == "date" or c == S.cols0["sales"]) else ("Fill median" if k == "num" else "Fill 'Unknown'")
    for c in q["invalid_dates"]:
        S[f"d_{c}"] = "Drop rows"
    for c in q["inconsistent"]:
        S[f"f_{c}"] = True
    for c in q["outliers"]:
        S[f"x_{c}"] = "Keep"


def review():
    q = S.q
    st.header("Step 1 - Data quality report")
    st.caption("Nothing is changed unless you choose it. Review each issue, pick what to fix, then continue.")
    mets = [("Rows", q["rows"]), ("Duplicate rows", q["duplicates"]), ("Missing cells", sum(q["missing"].values())),
            ("Invalid dates", sum(q["invalid_dates"].values())), ("Whitespace cells", sum(q["whitespace"].values())),
            ("Outliers", sum(v["count"] for v in q["outliers"].values()))]
    for col, (k, v) in zip(st.columns(6), mets):
        col.metric(k, f"{v:,}")
    with st.expander("Preview data"):
        st.dataframe(S.raw.head(50))
    st.button("✨ Apply all recommended fixes", on_click=recommend)
    o = {"missing": {}, "dates": {}, "formats": {}, "outliers": {}}
    if q["duplicates"]:
        st.subheader("Duplicate rows")
        o["dedupe"] = st.checkbox(f"Remove {q['duplicates']:,} duplicate rows", key="o_dd")
    if q["missing"]:
        st.subheader("Missing values")
        for c, n in q["missing"].items():
            o["missing"][c] = st.selectbox(f"{c}: {n:,} missing", MISS_OPTS[_kind(c)], key=f"m_{c}")
    if q["invalid_dates"]:
        st.subheader("Invalid dates")
        st.caption("If kept, the row stays but is excluded from monthly charts and forecasts.")
        for c, n in q["invalid_dates"].items():
            o["dates"][c] = st.selectbox(f"{c}: {n:,} invalid", ["Keep", "Drop rows"], key=f"d_{c}")
    if q["inconsistent"]:
        st.subheader("Inconsistent formats")
        st.caption("If kept, values like 'North' and 'north' are reported as separate categories.")
        for c, msg in q["inconsistent"].items():
            o["formats"][c] = st.checkbox(f"Standardize '{c}' ({msg})", key=f"f_{c}")
    if q["whitespace"]:
        st.subheader("Whitespace values")
        cells = ", ".join(f"{c} ({n:,})" for c, n in q["whitespace"].items())
        o["trim"] = st.checkbox(f"Trim extra spaces and treat blank cells as missing: {cells}", key="o_trim")
    if q["outliers"]:
        st.subheader("Outliers")
        st.caption("Found with the 1.5 x IQR rule. Large values are often genuine big orders, so 'Keep' is the safe choice.")
        for c, v in q["outliers"].items():
            o["outliers"][c] = st.selectbox(f"{c}: {v['count']:,} outside {v['low']:,.2f} to {v['high']:,.2f}",
                                            ["Keep", "Remove rows", "Cap at bounds"], key=f"x_{c}")
    if st.button("Continue to analysis →", type="primary"):
        S.rep, S.d, S.m = an.build(S.raw, o)
        S.stage = "analysis"
        st.rerun()


# ---------------- pages ----------------
if page == "Analyze":
    st.title("Analyze sales data")
    f = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])
    if f and S.get("fid") != f"{f.name}{f.size}":
        for k in [k for k in S if k[:2] in ("m_", "d_", "f_", "x_", "o_")]:
            del S[k]
        S.pop("rep", None); S.stage = None
        try:
            raw = an.load(f)
            if len(raw) > plan["rows"]:
                st.warning(f"{user['plan'].title()} plan is limited to {plan['rows']:,} rows; analysing the first {plan['rows']:,}.")
                raw = raw.head(plan["rows"])
            S.raw, S.filename, S.fid = raw, f.name, f"{f.name}{f.size}"
            S.cols0 = an.detect(raw)
            if not S.cols0["sales"]:
                raise ValueError("No numeric sales/amount column found.")
            S.q = an.quality(raw)
            if has_issues(S.q):
                S.stage = "review"
            else:
                S.rep, S.d, S.m = an.build(raw)
                S.stage = "analysis"
        except Exception as e:
            S.fid = None
            st.error(str(e))
    if f and S.get("stage") == "review":
        review()
    elif f and S.get("stage") == "analysis":
        if has_issues(S.q):
            if st.button("← Back to data review (resets your choices)"):
                S.stage = "review"; st.rerun()
        else:
            st.success("Data quality check passed - no issues found.")
        with st.expander("Detected columns"):
            st.write(S.rep["cols"])
        title = st.text_input("Report title", S.filename)
        if st.button("💾 Save report"):
            rid, err = db.save_report(S.uid, title, S.filename, S.rep)
            st.error(err) if err else st.success("Saved.")
        show(S.rep, S.d, S.m, "new")


elif page == "Saved reports":
    st.title("Saved reports")
    rows = db.list_reports(S.uid)
    if not rows:
        st.info("No saved reports yet.")
    for r in rows:
        with st.expander(f"{r['title']} - {r['created_at']:%Y-%m-%d %H:%M}"):
            if st.button("Delete", key=f"del{r['id']}"):
                db.delete_report(S.uid, r["id"]); st.rerun()
            _, rep = db.load_report(S.uid, r["id"])
            show(rep, key=f"s{r['id']}")

else:
    st.title("Plans")
    cols = st.columns(3)
    for col, (name, p) in zip(cols, db.PLANS.items()):
        with col:
            st.subheader(name.title())
            st.markdown(f"**₹{p['price']}/month**\n\n- {p['reports']} saved reports\n- {p['rows']:,} rows per file")
            if name == user["plan"]:
                st.success("Current plan")
            elif st.button(f"Switch to {name.title()}", key=name):
                # TODO: redirect to Stripe/Razorpay Checkout here and call db.set_plan() from the payment webhook.
                db.set_plan(S.uid, name); st.rerun()
    st.caption("Demo mode: switching plans does not charge. Connect a payment provider before going live.")
