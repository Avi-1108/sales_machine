import os, json, bcrypt, mysql.connector

PLANS = {
    "free":     {"price": 0,  "reports": 3,    "rows": 5_000},
    "pro":      {"price": 190, "reports": 50,   "rows": 200_000},
    "business": {"price": 490, "reports": 1000, "rows": 2_000_000},
}


def _conn():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "sales_analyzer"),
    )


def q(sql, args=(), one=False, write=False):
    c = _conn()
    cur = c.cursor(dictionary=True)
    try:
        cur.execute(sql, args)
        if write:
            c.commit()
            return cur.lastrowid
        return cur.fetchone() if one else cur.fetchall()
    finally:
        cur.close(); c.close()


def init():
    q("""CREATE TABLE IF NOT EXISTS users(
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        pw_hash VARCHAR(100) NOT NULL,
        plan VARCHAR(20) NOT NULL DEFAULT 'free',
        plan_started DATETIME NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""", write=True)
    q("""CREATE TABLE IF NOT EXISTS reports(
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        title VARCHAR(255) NOT NULL,
        filename VARCHAR(255),
        payload LONGTEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""", write=True)
    q("""CREATE TABLE IF NOT EXISTS plan_events(
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        old_plan VARCHAR(20), new_plan VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""", write=True)


def register(email, password):
    email = email.strip().lower()
    if len(password) < 8:
        return None, "Password must be at least 8 characters."
    if q("SELECT id FROM users WHERE email=%s", (email,), one=True):
        return None, "Email already registered."
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    uid = q("INSERT INTO users(email,pw_hash) VALUES(%s,%s)", (email, h), write=True)
    return get_user(uid), None


def login(email, password):
    u = q("SELECT * FROM users WHERE email=%s", (email.strip().lower(),), one=True)
    if u and bcrypt.checkpw(password.encode(), u["pw_hash"].encode()):
        return u
    return None


def get_user(uid):
    return q("SELECT * FROM users WHERE id=%s", (uid,), one=True)


def set_plan(uid, new_plan):
    """Call this from your payment webhook (Stripe/Razorpay) after a successful payment."""
    old = get_user(uid)["plan"]
    q("UPDATE users SET plan=%s, plan_started=NOW() WHERE id=%s", (new_plan, uid), write=True)
    q("INSERT INTO plan_events(user_id,old_plan,new_plan) VALUES(%s,%s,%s)", (uid, old, new_plan), write=True)


def save_report(uid, title, filename, payload):
    user = get_user(uid)
    n = q("SELECT COUNT(*) n FROM reports WHERE user_id=%s", (uid,), one=True)["n"]
    if n >= PLANS[user["plan"]]["reports"]:
        return None, f"Your {user['plan']} plan allows {PLANS[user['plan']]['reports']} saved reports. Upgrade to save more."
    rid = q("INSERT INTO reports(user_id,title,filename,payload) VALUES(%s,%s,%s,%s)",
            (uid, title, filename, json.dumps(payload)), write=True)
    return rid, None


def list_reports(uid):
    return q("SELECT id,title,filename,created_at FROM reports WHERE user_id=%s ORDER BY id DESC", (uid,))


def load_report(uid, rid):
    r = q("SELECT * FROM reports WHERE id=%s AND user_id=%s", (rid, uid), one=True)
    return (r, json.loads(r["payload"])) if r else (None, None)


def delete_report(uid, rid):
    q("DELETE FROM reports WHERE id=%s AND user_id=%s", (rid, uid), write=True)
