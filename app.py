import os
import sqlite3
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "kisanconnect.db")

app = Flask(__name__)
# Change this before publishing the project online.
app.config["SECRET_KEY"] = "kisanconnect-development-key-2026"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with app.open_resource("schema.sql") as file:
        db.executescript(file.read().decode("utf-8"))
    db.commit()


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "error")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You do not have permission to open this page.", "error")
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped_view
    return decorator


@app.route("/")
def index():
    db = get_db()
    products = db.execute(
        """SELECT products.*, users.name AS farmer_name
           FROM products JOIN users ON products.farmer_id = users.id
           ORDER BY products.id DESC LIMIT 6"""
    ).fetchall()
    return render_template("index.html", products=products)


@app.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        role = request.form["role"]
        db = get_db()

        if not name or not email or not password:
            flash("Please fill all fields.", "error")
        elif role not in ("farmer", "customer"):
            flash("Please select a valid role.", "error")
        elif db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            flash("This email is already registered. Please log in.", "error")
        else:
            db.execute(
                "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                (name, email, generate_password_hash(password), role),
            )
            db.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            flash("Welcome, " + user["name"] + "!", "success")
            if user["role"] == "farmer":
                return redirect(url_for("farmer_dashboard"))
            return redirect(url_for("products"))
        flash("Incorrect email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "success")
    return redirect(url_for("index"))


@app.route("/products")
def products():
    search = request.args.get("search", "").strip()
    db = get_db()
    query = """SELECT products.*, users.name AS farmer_name
               FROM products JOIN users ON products.farmer_id = users.id"""
    values = []
    if search:
        query += " WHERE products.name LIKE ? OR products.location LIKE ?"
        values = [f"%{search}%", f"%{search}%"]
    query += " ORDER BY products.id DESC"
    product_rows = db.execute(query, values).fetchall()
    return render_template("products.html", products=product_rows, search=search)


@app.route("/farmer/dashboard")
@login_required("farmer")
def farmer_dashboard():
    db = get_db()
    product_rows = db.execute(
        "SELECT * FROM products WHERE farmer_id = ? ORDER BY id DESC", (session["user_id"],)
    ).fetchall()
    order_rows = db.execute(
        """SELECT orders.*, products.name AS product_name, users.name AS customer_name
           FROM orders
           JOIN products ON orders.product_id = products.id
           JOIN users ON orders.customer_id = users.id
           WHERE products.farmer_id = ? ORDER BY orders.id DESC""",
        (session["user_id"],),
    ).fetchall()
    return render_template("farmer_dashboard.html", products=product_rows, orders=order_rows)


@app.route("/farmer/add-product", methods=("GET", "POST"))
@login_required("farmer")
def add_product():
    if request.method == "POST":
        name = request.form["name"].strip()
        category = request.form["category"].strip()
        price = request.form["price"].strip()
        quantity = request.form["quantity"].strip()
        location = request.form["location"].strip()
        if not all((name, category, price, quantity, location)):
            flash("Please fill all product fields.", "error")
        else:
            try:
                get_db().execute(
                    """INSERT INTO products (farmer_id, name, category, price, quantity, location)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (session["user_id"], name, category, float(price), float(quantity), location),
                )
                get_db().commit()
                flash("Product added successfully.", "success")
                return redirect(url_for("farmer_dashboard"))
            except ValueError:
                flash("Price and quantity must be numbers.", "error")
    return render_template("add_product.html")


@app.route("/order/<int:product_id>", methods=("POST",))
@login_required("customer")
def place_order(product_id):
    quantity = request.form.get("quantity", "1")
    product = get_db().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        flash("Product not found.", "error")
    else:
        try:
            order_quantity = float(quantity)
            if order_quantity <= 0 or order_quantity > product["quantity"]:
                flash("Please choose a valid available quantity.", "error")
            else:
                db = get_db()
                db.execute(
                    "INSERT INTO orders (product_id, customer_id, quantity, total_price) VALUES (?, ?, ?, ?)",
                    (product_id, session["user_id"], order_quantity, order_quantity * product["price"]),
                )
                db.execute("UPDATE products SET quantity = quantity - ? WHERE id = ?", (order_quantity, product_id))
                db.commit()
                flash("Order placed successfully. The farmer will confirm it.", "success")
        except ValueError:
            flash("Quantity must be a number.", "error")
    return redirect(url_for("products"))


@app.route("/my-orders")
@login_required("customer")
def my_orders():
    orders = get_db().execute(
        """SELECT orders.*, products.name AS product_name, users.name AS farmer_name
           FROM orders
           JOIN products ON orders.product_id = products.id
           JOIN users ON products.farmer_id = users.id
           WHERE orders.customer_id = ? ORDER BY orders.id DESC""",
        (session["user_id"],),
    ).fetchall()
    return render_template("my_orders.html", orders=orders)


@app.route("/order/<int:order_id>/status", methods=("POST",))
@login_required("farmer")
def update_order_status(order_id):
    status = request.form.get("status")
    if status not in ("Confirmed", "Delivered"):
        flash("Invalid order status.", "error")
        return redirect(url_for("farmer_dashboard"))
    db = get_db()
    order = db.execute(
        """SELECT orders.id FROM orders JOIN products ON orders.product_id = products.id
           WHERE orders.id = ? AND products.farmer_id = ?""",
        (order_id, session["user_id"]),
    ).fetchone()
    if order:
        db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        db.commit()
        flash("Order status updated.", "success")
    return redirect(url_for("farmer_dashboard"))


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)
