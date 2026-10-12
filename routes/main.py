from datetime import date, timedelta
from pathlib import Path
import uuid

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, send_from_directory, url_for

from config import MODEL_IMAGE_DIR, UPLOAD_DIR
from database.database import database


main = Blueprint("main", __name__)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MONTH_NAMES = ("Januar", "Februar", "März", "April", "Mai", "Juni",
               "Juli", "August", "September", "Oktober", "November", "Dezember")


def money(value):
    return f"{float(value or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def printer_hourly_cost(connection, printer_id):
    printer = connection.execute("SELECT * FROM printers WHERE id = ?", (printer_id,)).fetchone()
    if printer is None:
        return 0.0
    electricity = float(connection.execute(
        "SELECT value FROM settings WHERE key = 'electricity_price'"
    ).fetchone()[0])
    depreciation = printer["purchase_price"] / max(printer["depreciation_hours"], 1)
    return depreciation + printer["maintenance_per_hour"] + printer["energy_kw"] * electricity


def model_cost(connection, model_id, material_id=None, printer_id=None):
    extras = connection.execute(
        "SELECT additional_material_cost, additional_work_hours, additional_hourly_cost FROM models WHERE id=?",
        (model_id,),
    ).fetchone()
    parts = connection.execute(
        """SELECT mp.*, m.price_per_kg, p.id AS default_printer_id
        FROM model_parts mp JOIN materials m ON m.id = mp.material_id
        JOIN printers p ON p.id = mp.printer_id WHERE mp.model_id = ?""",
        (model_id,),
    ).fetchall()
    total = 0.0
    for part in parts:
        selected_material = material_id or part["material_id"]
        selected_printer = printer_id or part["default_printer_id"]
        if material_id:
            material = connection.execute(
                "SELECT price_per_kg FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            material_price = material["price_per_kg"] if material else part["price_per_kg"]
        else:
            material_price = part["price_per_kg"]
        total += part["weight_g"] * material_price / 1000
        total += part["print_hours"] * printer_hourly_cost(connection, selected_printer)
    if extras:
        total += extras["additional_material_cost"]
        total += extras["additional_work_hours"] * extras["additional_hourly_cost"]
    return total


def model_part_cost(connection, part_id, material_id=None, printer_id=None):
    part = connection.execute(
        """SELECT mp.*, m.price_per_kg, p.id AS default_printer_id
        FROM model_parts mp JOIN materials m ON m.id=mp.material_id
        JOIN printers p ON p.id=mp.printer_id WHERE mp.id=?""", (part_id,)
    ).fetchone()
    if part is None:
        return 0.0
    material_price = part["price_per_kg"]
    if material_id:
        material = connection.execute(
            "SELECT price_per_kg FROM materials WHERE id=?", (material_id,)
        ).fetchone()
        if material:
            material_price = material["price_per_kg"]
    printer_cost = printer_hourly_cost(connection, printer_id or part["default_printer_id"])
    return part["weight_g"] * material_price / 1000 + part["print_hours"] * printer_cost


def model_extra_costs(form):
    material_note = form.get("additional_material_note", "").strip()
    material_cost = float(form.get("additional_material_cost", "0").replace(",", ".") or 0)
    work_hours = float(form.get("additional_work_hours", "0").replace(",", ".") or 0)
    hourly_cost = float(form.get("additional_hourly_cost", "0").replace(",", ".") or 0)
    if min(material_cost, work_hours, hourly_cost) < 0:
        raise ValueError
    return material_note, material_cost, work_hours, hourly_cost


def image_upload_error(upload):
    if upload is None or not upload.filename:
        return None
    extension = Path(upload.filename).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        return "Bitte ein PNG-, JPEG-, GIF- oder WebP-Bild auswählen."
    signature = upload.stream.read(12)
    upload.stream.seek(0)
    valid = {
        ".gif": signature.startswith((b"GIF87a", b"GIF89a")),
        ".jpg": signature.startswith(b"\xff\xd8\xff"),
        ".jpeg": signature.startswith(b"\xff\xd8\xff"),
        ".png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
        ".webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
    }
    return None if valid[extension] else "Die Bilddatei ist beschädigt oder hat ein falsches Format."


def save_model_image(upload):
    extension = Path(upload.filename).suffix.lower()
    filename = f"{uuid.uuid4().hex}{extension}"
    MODEL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    upload.save(MODEL_IMAGE_DIR / filename)
    return filename


def invoice_settings(connection):
    settings = {row["key"]: row["value"] for row in connection.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'invoice_%'"
    ).fetchall()}
    legacy_lines = settings.get("invoice_payment_info", "").splitlines()
    if not settings.get("invoice_payment_bank"):
        settings["invoice_payment_bank"] = next(
            (line for line in legacy_lines if not line.strip().upper().startswith(("IBAN:", "BIC:"))), ""
        )
    for field, prefix in (("invoice_payment_iban", "IBAN:"), ("invoice_payment_bic", "BIC:")):
        if not settings.get(field):
            settings[field] = next(
                (line.split(":", 1)[1].strip() for line in legacy_lines
                 if line.strip().upper().startswith(prefix)), ""
            )
    return settings


def remove_unused_upload(filename):
    if not filename or Path(filename).name != filename:
        return
    with database() as connection:
        referenced = connection.execute(
            """SELECT 1 FROM settings WHERE key IN ('site_logo', 'invoice_logo') AND value=?
            UNION SELECT 1 FROM models WHERE image_filename=? LIMIT 1""",
            (filename, filename),
        ).fetchone()
    image_path = UPLOAD_DIR / filename
    if referenced is None and image_path.is_file():
        image_path.unlink()


def years(connection):
    return connection.execute("SELECT year FROM book_years ORDER BY year DESC").fetchall()


@main.app_context_processor
def inject_years():
    with database() as connection:
        settings = invoice_settings(connection)
        site_logo = connection.execute("SELECT value FROM settings WHERE key='site_logo'").fetchone()[0]
        return {"years": years(connection), "site_logo": site_logo}


def sale_items(connection, sale):
    items = connection.execute(
        """SELECT si.*, m.name AS model_name FROM sale_items si
        JOIN models m ON m.id=si.model_id WHERE si.sale_id=? ORDER BY si.id""",
        (sale["id"],),
    ).fetchall()
    if items:
        return items
    legacy_model = connection.execute("SELECT name FROM models WHERE id=?", (sale["model_id"],)).fetchone()
    return [{"id": None, "model_id": sale["model_id"], "model_name": legacy_model["name"],
             "quantity": sale["quantity"], "material_id": sale["material_id"],
             "printer_id": sale["printer_id"], "item_price": sale["item_price"],
             "printed": sale["printed"], "customer_note": "", "part_id": None,
             "part_name": "", "part_cost": 0.0}]


def sale_financials(connection, sale, items=None):
    items = items if items is not None else sale_items(connection, sale)
    income = sum(item["quantity"] * item["item_price"] for item in items)
    costs = sum(
        (item["part_cost"] if item["part_id"] else
         model_cost(connection, item["model_id"], item["material_id"], item["printer_id"]))
        * item["quantity"] for item in items
    ) + sale["packaging_cost"]
    return {
        "income": income,
        "shipping": sale["shipping_price"],
        "gross": income + sale["shipping_price"],
        "costs": costs,
        "profit": income - costs,
    }


def elapsed_months(year, today=None):
    today = today or date.today()
    if year < today.year or year > today.year:
        return 12.0
    year_start = date(year, 1, 1)
    elapsed_days = max((today - year_start).days + 1, 1)
    days_in_year = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    return elapsed_days / (days_in_year / 12)


def group_rows_by_month(rows, current_date):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["date"][:7], []).append(row)
    return [
        {"label": f"{MONTH_NAMES[int(key[5:7]) - 1]} {key[:4]}",
         "rows": month_rows, "open": key == current_date.strftime("%Y-%m")}
        for key, month_rows in grouped.items()
    ]


def year_page(year, page):
    with database() as connection:
        exists = connection.execute("SELECT year FROM book_years WHERE year = ?", (year,)).fetchone()
        if not exists:
            abort(404)
        groups = connection.execute("SELECT * FROM expense_groups ORDER BY name").fetchall()
        if page == "expenses":
            rows = connection.execute(
                """SELECT e.*, g.name AS group_name FROM expenses e
                JOIN expense_groups g ON g.id = e.group_id
                WHERE e.year = ? ORDER BY e.date DESC, e.id DESC""", (year,)
            ).fetchall()
            total = sum(row["amount"] for row in rows)
            current_date = date.today()
            monthly_expenses = sum(row["amount"] for row in rows
                                   if year != current_date.year or row["date"] <= current_date.isoformat())
            average_monthly_expenses = monthly_expenses / elapsed_months(year, current_date)
            return render_template("expenses.html", year=year, years=years(connection), rows=rows,
                                   month_groups=group_rows_by_month(rows, current_date),
                                   groups=groups, total=total, today=date.today().isoformat(),
                                   average_monthly_expenses=average_monthly_expenses,
                                   page_title="Ausgaben")
        order_rows = connection.execute(
            """SELECT s.*, m.name AS model_name, sh.name AS shipping_name
            FROM sales s JOIN models m ON m.id = s.model_id
            LEFT JOIN shipping_methods sh ON sh.id = s.shipping_method_id
            WHERE s.year = ? ORDER BY s.date DESC, s.number DESC""", (year,)
        ).fetchall()
        rows = []
        total_income = 0.0
        costs = 0.0
        for order in order_rows:
            items = sale_items(connection, order)
            financials = sale_financials(connection, order, items)
            row = dict(order)
            row["items"] = items
            row["order_income"] = financials["income"]
            row["order_gross"] = financials["gross"]
            row["order_costs"] = financials["costs"]
            row["order_profit"] = financials["profit"]
            row["item_labels"] = ", ".join(
                f"{item['model_name']}{' · ' + item['part_name'] if item['part_id'] else ''} × {item['quantity']}"
                for item in items
            )
            rows.append(row)
            total_income += financials["income"]
            costs += financials["costs"]
        current_date = date.today()
        monthly_rows = [row for row in rows
                        if year != current_date.year or row["date"] <= current_date.isoformat()]
        month_divisor = elapsed_months(year, current_date)
        monthly_income = sum(row["order_income"] for row in monthly_rows) / month_divisor
        monthly_profit = sum(row["order_profit"] for row in monthly_rows) / month_divisor
        monthly_sales = len(monthly_rows) / month_divisor
        return render_template("sales.html", year=year, years=years(connection), rows=rows,
                               month_groups=group_rows_by_month(rows, current_date),
                               total_income=total_income, total_cost=costs, groups=groups,
                               monthly_income=monthly_income, monthly_profit=monthly_profit,
                               monthly_sales=monthly_sales, total_profit=total_income - costs)


@main.app_template_filter("euro")
def euro(value):
    return money(value)


@main.route("/")
def dashboard():
    current_year = date.today().year
    with database() as connection:
        current = connection.execute("SELECT year FROM book_years WHERE year = ?", (current_year,)).fetchone()
        if current is None:
            connection.execute("INSERT INTO book_years (year) VALUES (?)", (current_year,))
        expenses = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE year = ?", (current_year,)
        ).fetchone()[0]
        sales = 0.0
        for order in connection.execute("SELECT * FROM sales WHERE year=?", (current_year,)).fetchall():
            sales += sale_financials(connection, order)["income"]
        filament = connection.execute(
            """SELECT COALESCE(SUM(e.quantity_kg), 0), COALESCE(SUM(e.amount), 0)
            FROM expenses e JOIN expense_groups g ON g.id = e.group_id
            WHERE e.year = ? AND lower(g.name) LIKE '%filament%'""", (current_year,)
        ).fetchone()
        top_groups = connection.execute(
            """SELECT g.name, SUM(e.amount) AS amount FROM expenses e
            JOIN expense_groups g ON g.id = e.group_id WHERE e.year = ?
            GROUP BY g.id ORDER BY amount DESC LIMIT 5""", (current_year,)
        ).fetchall()
        totals = connection.execute(
            """SELECT y.year,
            COALESCE((SELECT SUM(e.amount) FROM expenses e WHERE e.year = y.year), 0) AS expenses,
            COALESCE((SELECT SUM(COALESCE((SELECT SUM(si.quantity*si.item_price) FROM sale_items si WHERE si.sale_id=s.id), s.quantity*s.item_price)) FROM sales s WHERE s.year = y.year), 0) AS income
            FROM book_years y ORDER BY y.year DESC"""
        ).fetchall()
        overall_income = sum(item["income"] for item in totals)
        overall_expenses = sum(item["expenses"] for item in totals)
        models = connection.execute(
            """SELECT m.*,
            COALESCE((SELECT SUM(si.quantity) FROM sale_items si WHERE si.model_id=m.id),0) +
            COALESCE((SELECT SUM(s.quantity) FROM sales s WHERE s.model_id=m.id AND NOT EXISTS (SELECT 1 FROM sale_items si WHERE si.sale_id=s.id)),0) AS sold_units
            FROM models m ORDER BY m.name COLLATE NOCASE"""
        ).fetchall()
        model_overview = []
        for model in models:
            details = connection.execute(
                "SELECT COALESCE(SUM(weight_g), 0) AS grams, COALESCE(SUM(print_hours), 0) AS hours FROM model_parts WHERE model_id = ?",
                (model["id"],),
            ).fetchone()
            sold_units = model["sold_units"]
            model_overview.append({"model": model, "grams": details["grams"] * sold_units,
                                   "hours": details["hours"] * sold_units,
                                   "cost": model_cost(connection, model["id"]) * sold_units})
        available_years = years(connection)
    return render_template("dashboard.html", year=current_year, years=available_years,
                           income=sales, expenses=expenses, profit=sales - expenses,
                           filament_kg=filament[0], filament_cost=filament[1],
                           top_groups=top_groups, totals=totals, models=model_overview,
                           overall_income=overall_income, overall_expenses=overall_expenses)


@main.route("/uploads/<path:filename>")
def model_image(filename):
    return send_from_directory(MODEL_IMAGE_DIR, filename)


@main.post("/uploads/logo")
def upload_site_logo():
    upload = request.files.get("logo")
    error = image_upload_error(upload)
    if error or upload is None or not upload.filename:
        flash(error or "Bitte eine Bilddatei auswählen.", "error")
    else:
        filename = save_model_image(upload)
        with database() as connection:
            old_filename = connection.execute(
                "SELECT value FROM settings WHERE key='site_logo'"
            ).fetchone()[0]
            connection.execute("UPDATE settings SET value=? WHERE key='site_logo'", (filename,))
        remove_unused_upload(old_filename)
        flash("Logo gespeichert.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))


@main.route("/invoice-settings", methods=["GET", "POST"])
def invoice_settings_page():
    if request.method == "POST":
        upload = request.files.get("invoice_logo_upload")
        error = image_upload_error(upload)
        if error:
            flash(error, "error")
            return redirect(url_for("main.invoice_settings_page"))
        fields = {
            "invoice_vendor_name": request.form.get("vendor_name", "").strip(),
            "invoice_vendor_address": request.form.get("vendor_address", "").strip(),
            "invoice_vendor_phone": request.form.get("vendor_phone", "").strip(),
            "invoice_vendor_email": request.form.get("vendor_email", "").strip(),
            "invoice_payment_bank": request.form.get("payment_bank", "").strip(),
            "invoice_payment_iban": request.form.get("payment_iban", "").strip(),
            "invoice_payment_bic": request.form.get("payment_bic", "").strip(),
            "invoice_tax_number": request.form.get("tax_number", "").strip(),
            "invoice_economic_id": request.form.get("economic_id", "").strip(),
            "invoice_additional_text": request.form.get("additional_text", "").strip(),
            "invoice_bold_text": request.form.get("bold_text", "").strip(),
        }
        old_logo = ""
        if upload and upload.filename:
            with database() as connection:
                old_logo = connection.execute(
                    "SELECT value FROM settings WHERE key='invoice_logo'"
                ).fetchone()[0]
            fields["invoice_logo"] = save_model_image(upload)
        with database() as connection:
            connection.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                fields.items(),
            )
        if upload and upload.filename:
            remove_unused_upload(old_logo)
        flash("Rechnungsangaben gespeichert.", "success")
        return redirect(url_for("main.invoice_settings_page"))
    with database() as connection:
        settings = invoice_settings(connection)
    return render_template("invoice_settings.html", settings=settings)


@main.route("/materials", methods=["GET", "POST"])
def materials():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        kind = request.form.get("material_type", "").strip()
        try:
            price = float(request.form.get("price_per_kg", "0").replace(",", "."))
        except ValueError:
            price = -1
        if not name or not kind or price < 0:
            flash("Bitte Material, Typ und einen gültigen Preis eintragen.", "error")
        else:
            with database() as connection:
                connection.execute("INSERT INTO materials (name, material_type, price_per_kg) VALUES (?, ?, ?)",
                                   (name, kind, price))
            flash("Material gespeichert.", "success")
        return redirect(url_for("main.materials"))
    with database() as connection:
        rows = connection.execute("SELECT * FROM materials WHERE active = 1 ORDER BY material_type, name").fetchall()
        grouped = {}
        for row in rows:
            grouped.setdefault(row["material_type"], []).append(row)
    return render_template("materials.html", grouped=grouped)


@main.post("/materials/<int:material_id>/delete")
def delete_material(material_id):
    with database() as connection:
        try:
            connection.execute("DELETE FROM materials WHERE id = ?", (material_id,))
            flash("Material gelöscht.", "success")
        except Exception:
            connection.execute("UPDATE materials SET active = 0 WHERE id = ?", (material_id,))
            flash("Verwendetes Material wurde archiviert und bleibt in bisherigen Modellen erhalten.", "success")
    return redirect(url_for("main.materials"))


@main.route("/printers", methods=["GET", "POST"])
def printers():
    if request.method == "POST":
        try:
            fields = [float(request.form.get(key, "0").replace(",", ".")) for key in
                      ("purchase_price", "energy_kw", "depreciation_hours", "maintenance_per_hour")]
            name = request.form.get("name", "").strip()
            if not name or min(fields) < 0 or fields[2] <= 0:
                raise ValueError
        except ValueError:
            flash("Bitte gültige Druckerwerte und eine Abschreibungsdauer größer null eintragen.", "error")
        else:
            with database() as connection:
                connection.execute(
                    "INSERT INTO printers (name, purchase_price, energy_kw, depreciation_hours, maintenance_per_hour) VALUES (?, ?, ?, ?, ?)",
                    (name, *fields),
                )
            flash("Drucker gespeichert.", "success")
        return redirect(url_for("main.printers"))
    with database() as connection:
        rows = connection.execute("SELECT * FROM printers WHERE active = 1 ORDER BY name").fetchall()
        electricity = float(connection.execute("SELECT value FROM settings WHERE key='electricity_price'").fetchone()[0])
        price_factor = float(connection.execute("SELECT value FROM settings WHERE key='price_calculator_factor'").fetchone()[0])
        printer_rows = [{"printer": row, "hourly": printer_hourly_cost(connection, row["id"])} for row in rows]
    return render_template("printers.html", rows=printer_rows, electricity=electricity,
                           price_factor=price_factor)


@main.post("/printers/electricity")
def update_electricity():
    try:
        price = float(request.form.get("electricity_price", "").replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Bitte einen gültigen Strompreis eingeben.", "error")
    else:
        with database() as connection:
            connection.execute("UPDATE settings SET value = ? WHERE key='electricity_price'", (str(price),))
        flash("Strompreis aktualisiert.", "success")
    return redirect(url_for("main.printers"))


@main.post("/printers/price-calculator")
def update_price_calculator():
    try:
        factor = float(request.form.get("price_calculator_factor", "").replace(",", "."))
        if factor < 0:
            raise ValueError
    except ValueError:
        flash("Bitte einen gültigen Wert für den Price Calculator eingeben.", "error")
    else:
        with database() as connection:
            connection.execute(
                "UPDATE settings SET value=? WHERE key='price_calculator_factor'", (str(factor),)
            )
        flash("Price Calculator aktualisiert.", "success")
    return redirect(url_for("main.printers"))


@main.post("/printers/<int:printer_id>/edit")
def edit_printer(printer_id):
    try:
        fields = [float(request.form.get(key, "0").replace(",", ".")) for key in
                  ("purchase_price", "energy_kw", "depreciation_hours", "maintenance_per_hour")]
        name = request.form.get("name", "").strip()
        if not name or min(fields) < 0 or fields[2] <= 0:
            raise ValueError
    except ValueError:
        flash("Bitte gültige Druckerwerte und eine Abschreibungsdauer größer null eintragen.", "error")
    else:
        with database() as connection:
            cursor = connection.execute(
                "UPDATE printers SET name=?, purchase_price=?, energy_kw=?, depreciation_hours=?, maintenance_per_hour=? WHERE id=?",
                (name, *fields, printer_id),
            )
            if cursor.rowcount == 0:
                abort(404)
        flash("Drucker aktualisiert.", "success")
    return redirect(url_for("main.printers"))


@main.post("/printers/<int:printer_id>/delete")
def delete_printer(printer_id):
    with database() as connection:
        try:
            connection.execute("DELETE FROM printers WHERE id = ?", (printer_id,))
            flash("Drucker gelöscht.", "success")
        except Exception:
            connection.execute("UPDATE printers SET active = 0 WHERE id = ?", (printer_id,))
            flash("Verwendeter Drucker wurde archiviert.", "success")
    return redirect(url_for("main.printers"))


@main.route("/models")
def model_list():
    archive_view = request.args.get("archive") == "1"
    with database() as connection:
        categories = connection.execute("SELECT * FROM categories ORDER BY name").fetchall()
        category_id = request.args.get("category", type=int)
        category_ids = {category["id"] for category in categories}
        if category_id not in category_ids:
            category_id = None
        query = """SELECT m.*,
            COALESCE((SELECT SUM(si.quantity) FROM sale_items si WHERE si.model_id=m.id),0) +
            COALESCE((SELECT SUM(s.quantity) FROM sales s WHERE s.model_id=m.id AND NOT EXISTS (SELECT 1 FROM sale_items si WHERE si.sale_id=s.id)),0) AS sold_units
            FROM models m WHERE m.archived=?"""
        parameters = [int(archive_view)]
        if category_id is not None:
            query += " AND EXISTS (SELECT 1 FROM model_categories mc WHERE mc.model_id=m.id AND mc.category_id=?)"
            parameters.append(category_id)
        models = connection.execute(query + " ORDER BY m.name", parameters).fetchall()
        model_ids = [model["id"] for model in models]
        category_map = {model_id: [] for model_id in model_ids}
        if model_ids:
            placeholders = ",".join("?" for _ in model_ids)
            for row in connection.execute(
                f"""SELECT mc.model_id, c.id, c.name FROM model_categories mc
                JOIN categories c ON c.id=mc.category_id
                WHERE mc.model_id IN ({placeholders}) ORDER BY c.name""", model_ids
            ):
                category_map[row["model_id"]].append({"id": row["id"], "name": row["name"]})
        items = []
        for model in models:
            totals = connection.execute(
                "SELECT COALESCE(SUM(weight_g),0) AS grams, COALESCE(SUM(print_hours),0) AS hours FROM model_parts WHERE model_id=?",
                (model["id"],),
            ).fetchone()
            model_data = dict(model)
            model_data["categories"] = category_map[model["id"]]
            items.append({"model": model_data, "grams": totals["grams"], "hours": totals["hours"],
                          "cost": model_cost(connection, model["id"])})
    return render_template("models.html", models=items, categories=categories,
                           selected_category=category_id, archive_view=archive_view)


@main.post("/categories")
def create_category():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Bitte einen Kategorienamen eingeben.", "error")
    else:
        with database() as connection:
            cursor = connection.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        flash("Kategorie gespeichert." if cursor.rowcount else "Diese Kategorie existiert bereits.",
              "success" if cursor.rowcount else "error")
    return redirect(url_for("main.model_list", archive=request.form.get("archive") or None,
                            category=request.form.get("filter_category") or None))


@main.post("/categories/<int:category_id>/delete")
def delete_category(category_id):
    with database() as connection:
        cursor = connection.execute("DELETE FROM categories WHERE id=?", (category_id,))
    if cursor.rowcount:
        flash("Kategorie entfernt.", "success")
    return redirect(url_for("main.model_list", archive=request.form.get("archive") or None,
                            category=request.form.get("filter_category") or None))


def part_form_rows(form):
    parts = []
    keys = zip(form.getlist("part_name"), form.getlist("material_id"), form.getlist("printer_id"),
               form.getlist("weight_g"), form.getlist("print_hours"), form.getlist("print_minutes"),
               form.getlist("part_price"))
    for name, material, printer, weight, hours, minutes, part_price in keys:
        if not name.strip():
            continue
        weight_value = float(weight.replace(",", "."))
        hours_value = float(hours.replace(",", ".") or 0)
        minutes_value = int(minutes or 0)
        price_value = float(part_price.replace(",", ".") or 0)
        if weight_value < 0 or hours_value < 0 or price_value < 0 or not 0 <= minutes_value < 60:
            raise ValueError
        parts.append((name.strip(), int(material), int(printer), weight_value,
                      hours_value + minutes_value / 60, price_value))
    return parts


def model_form_options(connection):
    materials = connection.execute("SELECT * FROM materials WHERE active=1 ORDER BY name").fetchall()
    printer_list = connection.execute("SELECT * FROM printers WHERE active=1 ORDER BY name").fetchall()
    printer_rates = {printer["id"]: printer_hourly_cost(connection, printer["id"]) for printer in printer_list}
    price_factor = float(connection.execute(
        "SELECT value FROM settings WHERE key='price_calculator_factor'"
    ).fetchone()[0])
    return materials, printer_list, printer_rates, price_factor


def model_form_parts(rows):
    parts = []
    for row in rows:
        item = dict(row)
        hours = float(item["print_hours"])
        whole_hours = int(hours)
        minutes = round((hours - whole_hours) * 60)
        if minutes == 60:
            whole_hours += 1
            minutes = 0
        item["input_hours"] = whole_hours
        item["input_minutes"] = minutes
        parts.append(item)
    return parts


def save_model_categories(connection, model_id, category_ids):
    connection.execute("DELETE FROM model_categories WHERE model_id=?", (model_id,))
    connection.executemany(
        "INSERT INTO model_categories (model_id, category_id) VALUES (?, ?)",
        [(model_id, category_id) for category_id in category_ids],
    )


def sale_form_models(connection):
    models = [dict(row) for row in connection.execute("SELECT * FROM models ORDER BY name")]
    category_map = {model["id"]: [] for model in models}
    if models:
        placeholders = ",".join("?" for _ in models)
        for row in connection.execute(
            f"""SELECT mc.model_id, mc.category_id FROM model_categories mc
            WHERE mc.model_id IN ({placeholders})""", [model["id"] for model in models]
        ):
            category_map[row["model_id"]].append(row["category_id"])
    for model in models:
        model["category_ids"] = category_map[model["id"]]
        model["parts"] = [dict(row) for row in connection.execute(
            "SELECT * FROM model_parts WHERE model_id=? ORDER BY name", (model["id"],)
        )]
        for part in model["parts"]:
            part["production_cost"] = model_part_cost(connection, part["id"])
    return models


def sale_form_part_costs(connection, models, material_ids, printer_ids):
    return {
        str(part["id"]): {
            f"{material_id or 0}:{printer_id or 0}": model_part_cost(
                connection, part["id"], material_id, printer_id
            )
            for material_id in material_ids for printer_id in printer_ids
        }
        for model in models for part in model["parts"]
    }


@main.route("/models/new", methods=["GET", "POST"])
def new_model():
    error = None
    form_values = {}
    with database() as connection:
        categories = connection.execute("SELECT * FROM categories ORDER BY name").fetchall()
    valid_category_ids = {category["id"] for category in categories}
    selected_category_ids = []
    if request.method == "POST":
        upload = request.files.get("image")
        try:
            name = request.form.get("name", "").strip()
            price = float(request.form.get("sale_price", "0").replace(",", "."))
            extra_costs = model_extra_costs(request.form)
            parts = part_form_rows(request.form)
            selected_category_ids = [int(value) for value in request.form.getlist("category_ids")]
            if (not name or price < 0 or not parts
                    or not set(selected_category_ids).issubset(valid_category_ids)):
                raise ValueError
            error = image_upload_error(upload)
            if error:
                raise ValueError
        except (ValueError, TypeError):
            error = error or "Bitte Modell, Verkaufspreis und mindestens ein vollständiges Teil angeben."
        if error:
            form_values = request.form
            with database() as connection:
                materials, printer_list, printer_rates, price_factor = model_form_options(connection)
            return render_template("model_form.html", model=None, parts=[], materials=materials,
                                   printers=printer_list, printer_rates=printer_rates,
                                   price_factor=price_factor, error=error,
                                   categories=categories, selected_category_ids=set(selected_category_ids),
                                   form_values=form_values), 400
        filename = save_model_image(upload) if upload and upload.filename else None
        with database() as connection:
            cursor = connection.execute(
                """INSERT INTO models (name, description, sale_price, image_filename,
                additional_material_note, additional_material_cost, additional_work_hours,
                additional_hourly_cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, request.form.get("description", "").strip(), price, filename, *extra_costs),
            )
            model_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO model_parts (model_id, name, material_id, printer_id, weight_g, print_hours, part_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(model_id, *part) for part in parts],
            )
            save_model_categories(connection, model_id, selected_category_ids)
        return redirect(url_for("main.model_detail", model_id=model_id))
    with database() as connection:
        materials, printer_list, printer_rates, price_factor = model_form_options(connection)
    return render_template("model_form.html", model=None, parts=[], materials=materials,
                           printers=printer_list, printer_rates=printer_rates,
                           price_factor=price_factor, error=None, form_values=form_values,
                           categories=categories, selected_category_ids=set())


@main.route("/models/<int:model_id>/edit", methods=["GET", "POST"])
def edit_model(model_id):
    error = None
    upload = request.files.get("image")
    with database() as connection:
        model = connection.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        if model is None:
            abort(404)
        categories = connection.execute("SELECT * FROM categories ORDER BY name").fetchall()
        valid_category_ids = {category["id"] for category in categories}
        selected_category_ids = {
            row["category_id"] for row in connection.execute(
                "SELECT category_id FROM model_categories WHERE model_id=?", (model_id,)
            )
        }
        model_used = connection.execute(
            """SELECT EXISTS(SELECT 1 FROM sales WHERE model_id=?) OR
            EXISTS(SELECT 1 FROM sale_items WHERE model_id=?) AS used""",
            (model_id, model_id),
        ).fetchone()["used"]
        parts = connection.execute("SELECT * FROM model_parts WHERE model_id=? ORDER BY id", (model_id,)).fetchall()
        if request.method == "POST":
            try:
                name = request.form.get("name", "").strip()
                price = float(request.form.get("sale_price", "0").replace(",", "."))
                extra_costs = model_extra_costs(request.form)
                submitted_parts = part_form_rows(request.form)
                selected_category_ids = [int(value) for value in request.form.getlist("category_ids")]
                if (not name or price < 0 or not submitted_parts
                    or not set(selected_category_ids).issubset(valid_category_ids)):
                    raise ValueError
                error = image_upload_error(upload)
                if error:
                    raise ValueError
            except (ValueError, TypeError):
                error = error or "Bitte Modell, Verkaufspreis und mindestens ein vollständiges Teil angeben."
            if error is None:
                old_image = model["image_filename"]
                image_filename = save_model_image(upload) if upload and upload.filename else old_image
                connection.execute(
                    """UPDATE models SET name=?, description=?, sale_price=?, image_filename=?,
                    additional_material_note=?, additional_material_cost=?, additional_work_hours=?,
                    additional_hourly_cost=? WHERE id=?""",
                    (name, request.form.get("description", "").strip(), price, image_filename,
                     *extra_costs, model_id),
                )
                connection.execute("DELETE FROM model_parts WHERE model_id=?", (model_id,))
                connection.executemany(
                    "INSERT INTO model_parts (model_id,name,material_id,printer_id,weight_g,print_hours,part_price) VALUES (?,?,?,?,?,?,?)",
                    [(model_id, *part) for part in submitted_parts],
                )
                save_model_categories(connection, model_id, selected_category_ids)
                if image_filename != old_image:
                    remove_model_image(old_image)
                flash("Modell aktualisiert.", "success")
                return redirect(url_for("main.model_detail", model_id=model_id))
        materials, printer_list, printer_rates, price_factor = model_form_options(connection)
        display_parts = model_form_parts(parts)
    return render_template("model_form.html", model=model, parts=display_parts,
                           materials=materials, printers=printer_list,
                           printer_rates=printer_rates, price_factor=price_factor,
                           error=error, model_used=model_used,
                           categories=categories, selected_category_ids=set(selected_category_ids),
                           form_values=request.form if error else {})


@main.post("/models/<int:model_id>/delete")
def delete_model(model_id):
    with database() as connection:
        model = connection.execute("SELECT image_filename FROM models WHERE id=?", (model_id,)).fetchone()
        if model is None:
            abort(404)
        used = connection.execute(
            """SELECT EXISTS(SELECT 1 FROM sales WHERE model_id=?) OR
            EXISTS(SELECT 1 FROM sale_items WHERE model_id=?) AS used""",
            (model_id, model_id),
        ).fetchone()["used"]
        if used:
            flash("Dieses Modell wird in einem Verkauf verwendet und kann nicht gelöscht werden.", "error")
            return redirect(url_for("main.edit_model", model_id=model_id))
        image_filename = model["image_filename"]
        connection.execute("DELETE FROM models WHERE id=?", (model_id,))
    remove_model_image(image_filename)
    flash("Modell gelöscht.", "success")
    return redirect(url_for("main.model_list"))


@main.post("/models/<int:model_id>/archive")
def archive_model(model_id):
    with database() as connection:
        model = connection.execute("SELECT id FROM models WHERE id=?", (model_id,)).fetchone()
        if model is None:
            abort(404)
        connection.execute("UPDATE models SET archived=1 WHERE id=?", (model_id,))
        connection.execute(
            """UPDATE sales SET show_archived=1 WHERE model_id=? OR id IN
            (SELECT sale_id FROM sale_items WHERE model_id=?)""",
            (model_id, model_id),
        )
    flash("Modell ins Archiv verschoben. Betroffene Verkäufe zeigen archivierte Modelle an.", "success")
    return redirect(url_for("main.model_detail", model_id=model_id))


@main.post("/models/<int:model_id>/unarchive")
def unarchive_model(model_id):
    with database() as connection:
        cursor = connection.execute("UPDATE models SET archived=0 WHERE id=?", (model_id,))
        if cursor.rowcount == 0:
            abort(404)
    flash("Modell aus dem Archiv entfernt.", "success")
    return redirect(url_for("main.model_detail", model_id=model_id))


def remove_model_image(filename):
    if not filename or Path(filename).name != filename:
        return
    image_path = MODEL_IMAGE_DIR / filename
    if image_path.is_file():
        image_path.unlink()


@main.route("/models/<int:model_id>")
def model_detail(model_id):
    with database() as connection:
        model = connection.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        if model is None:
            abort(404)
        categories = connection.execute(
            """SELECT c.name FROM categories c JOIN model_categories mc ON mc.category_id=c.id
            WHERE mc.model_id=? ORDER BY c.name""", (model_id,)
        ).fetchall()
        parts = connection.execute(
            """SELECT mp.*, m.name AS material_name, m.price_per_kg, m.material_type,
            p.name AS printer_name FROM model_parts mp JOIN materials m ON m.id=mp.material_id
            JOIN printers p ON p.id=mp.printer_id WHERE mp.model_id=? ORDER BY mp.id""", (model_id,)
        ).fetchall()
        computed = []
        for part in parts:
            printer_cost = printer_hourly_cost(connection, part["printer_id"])
            material_cost = part["weight_g"] * part["price_per_kg"] / 1000
            time_cost = part["print_hours"] * printer_cost
            computed.append({"part": part, "material_cost": material_cost, "time_cost": time_cost,
                             "total": material_cost + time_cost})
        additional_material_cost = model["additional_material_cost"]
        additional_work_cost = model["additional_work_hours"] * model["additional_hourly_cost"]
        total_cost = sum(row["total"] for row in computed) + additional_material_cost + additional_work_cost
        sold = connection.execute(
            """SELECT COALESCE((SELECT SUM(quantity) FROM sale_items WHERE model_id=?),0) +
            COALESCE((SELECT SUM(quantity) FROM sales s WHERE s.model_id=? AND NOT EXISTS (SELECT 1 FROM sale_items si WHERE si.sale_id=s.id)),0)""",
            (model_id, model_id),
        ).fetchone()[0]
    return render_template("model_detail.html", model=model, categories=categories,
                           parts=computed, total_cost=total_cost,
                           additional_material_cost=additional_material_cost,
                           additional_work_cost=additional_work_cost,
                           sold=sold, now_year=date.today().year)


@main.route("/years/new", methods=["POST"])
def new_year():
    try:
        year = int(request.form.get("year", ""))
        if year < 2000 or year > 2200:
            raise ValueError
    except ValueError:
        flash("Bitte ein gültiges Jahr eintragen.", "error")
        return redirect(url_for("main.dashboard"))
    with database() as connection:
        connection.execute("INSERT OR IGNORE INTO book_years (year) VALUES (?)", (year,))
        previous = connection.execute("SELECT MAX(year) FROM book_years WHERE year < ?", (year,)).fetchone()[0]
        if previous:
            connection.execute(
                "INSERT OR IGNORE INTO shipping_methods (year, name, price) SELECT ?, name, price FROM shipping_methods WHERE year=?",
                (year, previous),
            )
    flash(f"Das Jahr {year} wurde angelegt.", "success")
    return redirect(url_for("main.expenses", year=year))


@main.route("/years/<int:year>/expenses", methods=["GET", "POST"])
def expenses(year):
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", "").replace(",", "."))
            quantity = float(request.form.get("quantity_kg", "0").replace(",", ".") or 0)
            if amount < 0 or quantity < 0:
                raise ValueError
            group_id = int(request.form["group_id"])
            expense_date = date.fromisoformat(request.form.get("date", date.today().isoformat()))
            name = request.form.get("name", "").strip()
            if not name or expense_date.year != year:
                raise ValueError
        except (ValueError, KeyError):
            flash("Bitte gültige Ausgabendaten eingeben.", "error")
            return redirect(url_for("main.expenses", year=year))
        with database() as connection:
            connection.execute(
                "INSERT INTO expenses (year, group_id, name, additional_info, amount, quantity_kg, note, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (year, group_id, name, request.form.get("additional_info", "").strip(), amount, quantity,
                 request.form.get("note", "").strip(), expense_date.isoformat()),
            )
        return redirect(url_for("main.expenses", year=year))
    return year_page(year, "expenses")


@main.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
def edit_expense(expense_id):
    with database() as connection:
        expense = connection.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
        if expense is None:
            abort(404)
        groups = connection.execute("SELECT * FROM expense_groups ORDER BY name").fetchall()
        if request.method == "POST":
            try:
                name = request.form.get("name", "").strip()
                amount = float(request.form.get("amount", "").replace(",", "."))
                quantity = float(request.form.get("quantity_kg", "0").replace(",", ".") or 0)
                group_id = int(request.form["group_id"])
                expense_date = date.fromisoformat(request.form.get("date", ""))
                valid_group = connection.execute(
                    "SELECT 1 FROM expense_groups WHERE id=?", (group_id,)
                ).fetchone()
                if not name or amount < 0 or quantity < 0 or not valid_group or expense_date.year != expense["year"]:
                    raise ValueError
            except (ValueError, KeyError):
                flash("Bitte gültige Ausgabendaten eingeben. Das Datum muss im selben Buchungsjahr liegen.", "error")
            else:
                connection.execute(
                    """UPDATE expenses SET group_id=?, name=?, additional_info=?, amount=?,
                    quantity_kg=?, note=?, date=? WHERE id=?""",
                    (group_id, name, request.form.get("additional_info", "").strip(), amount,
                     quantity, request.form.get("note", "").strip(), expense_date.isoformat(), expense_id),
                )
                flash("Ausgabe aktualisiert.", "success")
                return redirect(url_for("main.expenses", year=expense["year"]))
        form_values = request.form if request.method == "POST" else expense
        return render_template("expense_form.html", values=form_values, expense=expense,
                       groups=groups, years=years(connection),
                       error=request.method == "POST")


@main.post("/expenses/<int:expense_id>/delete")
def delete_expense(expense_id):
    with database() as connection:
        expense = connection.execute("SELECT year FROM expenses WHERE id=?", (expense_id,)).fetchone()
        if expense is None:
            abort(404)
        connection.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
        year = expense["year"]
    flash("Ausgabe gelöscht.", "success")
    return redirect(url_for("main.expenses", year=year))


@main.post("/groups/new")
def new_group():
    name = request.form.get("name", "").strip()
    if name:
        with database() as connection:
            connection.execute("INSERT OR IGNORE INTO expense_groups (name) VALUES (?)", (name,))
        flash("Ausgabengruppe gespeichert.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))


@main.route("/years/<int:year>/sales", methods=["GET", "POST"])
def sales(year):
    if request.method == "POST":
        return create_sale(year)
    return year_page(year, "sales")


def create_sale(year):
    try:
        model_ids = request.form.getlist("item_model_id")
        quantities = request.form.getlist("item_quantity")
        prices = request.form.getlist("item_price")
        materials = request.form.getlist("item_material_id")
        printers = request.form.getlist("item_printer_id")
        customer_notes = request.form.getlist("item_customer_note")
        part_ids = request.form.getlist("item_part_id")
        category_ids = request.form.getlist("item_category_id")
        if not model_ids:
            model_ids = [request.form["model_id"]]
            quantities = [request.form.get("quantity", "1")]
            prices = [request.form.get("item_price", "0")]
            materials = [request.form.get("material_id", "")]
            printers = [request.form.get("printer_id", "")]
            customer_notes = [""]
        count = len(model_ids)
        customer_notes = customer_notes or [""] * count
        part_ids = part_ids or [""] * count
        category_ids = category_ids or [""] * count
        submitted_items = []
        for model_value, quantity_value, price_value, material_value, printer_value, customer_note, part_value, category_value in zip(
            model_ids, quantities, prices, materials, printers, customer_notes, part_ids, category_ids
        ):
            model_id = int(model_value)
            quantity = int(quantity_value)
            item_price = float(price_value.replace(",", "."))
            material_id = int(material_value) if material_value else None
            printer_id = int(printer_value) if printer_value else None
            part_id = int(part_value) if part_value else None
            category_id = int(category_value) if category_value else None
            if quantity < 1 or item_price < 0:
                raise ValueError
            submitted_items.append((model_id, part_id, category_id, quantity, material_id,
                                    printer_id, item_price, customer_note.strip()))
        number_value = request.form.get("number", "").strip()
        sale_date = date.fromisoformat(request.form.get("date", date.today().isoformat()))
        shipping_date_value = request.form.get("shipping_date", "").strip()
        shipping_date = date.fromisoformat(shipping_date_value).isoformat() if shipping_date_value else ""
        lengths = {len(model_ids), len(quantities), len(prices), len(materials), len(printers),
                   len(customer_notes), len(part_ids), len(category_ids)}
        if sale_date.year != year or not submitted_items or len(lengths) != 1:
            raise ValueError
    except (KeyError, ValueError):
        flash("Bitte mindestens eine gültige Modellposition und gültige Datumsangaben eintragen.", "error")
        return redirect(url_for("main.new_sale", year=year))
    with database() as connection:
        items = []
        for model_id, part_id, category_id, quantity, material_id, printer_id, item_price, customer_note in submitted_items:
            model = connection.execute("SELECT id, archived FROM models WHERE id=?", (model_id,)).fetchone()
            if model is None:
                abort(400)
            if model["archived"] and "show_archived" not in request.form:
                flash("Aktiviere die Archivmodelle, um ein archiviertes Modell auszuwählen.", "error")
                return redirect(url_for("main.new_sale", year=year))
            if category_id and connection.execute(
                "SELECT 1 FROM model_categories WHERE model_id=? AND category_id=?", (model_id, category_id)
            ).fetchone() is None:
                abort(400)
            part_name = ""
            part_cost = 0.0
            if part_id:
                part = connection.execute(
                    "SELECT name FROM model_parts WHERE id=? AND model_id=?", (part_id, model_id)
                ).fetchone()
                if part is None:
                    abort(400)
                part_name = part["name"]
                part_cost = model_part_cost(connection, part_id, material_id, printer_id)
            items.append((model_id, part_id, part_name, part_cost, quantity, material_id,
                          printer_id, item_price, customer_note))
        number = int(number_value) if number_value else connection.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 FROM sales WHERE year=?", (year,)
        ).fetchone()[0]
        if connection.execute("SELECT 1 FROM sales WHERE year=? AND number=?", (year, number)).fetchone():
            flash("Diese Auftragsnummer ist in diesem Jahr bereits vergeben.", "error")
            return redirect(url_for("main.new_sale", year=year))
        shipping_id = request.form.get("shipping_method_id", type=int) or None
        shipping_price = 0.0
        if shipping_id:
            shipping = connection.execute("SELECT price FROM shipping_methods WHERE id=? AND year=?", (shipping_id, year)).fetchone()
            if shipping:
                shipping_price = shipping["price"]
        try:
            packaging = float(request.form.get("packaging_cost", "0").replace(",", ".") or 0)
            if packaging < 0:
                raise ValueError
            first_model, _, _, _, first_quantity, first_material, first_printer, first_price, _ = items[0]
            connection.execute(
                """INSERT INTO sales (year, number, date, platform, company_name, customer_name, street, postal_code, city,
                model_id, quantity, material_id, printer_id, item_price, shipping_method_id, shipping_price,
                packaging_cost, note, shipping_date, show_archived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (year, number, sale_date.isoformat(), request.form.get("platform", "").strip(),
                 request.form.get("company_name", "").strip(), request.form.get("customer_name", "").strip(),
                 request.form.get("street", "").strip(),
                 request.form.get("postal_code", "").strip(), request.form.get("city", "").strip(),
                first_model, first_quantity, first_material, first_printer, first_price, shipping_id, shipping_price, packaging,
                 request.form.get("note", "").strip(), shipping_date,
                 int("show_archived" in request.form)),
            )
            sale_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.executemany(
                "INSERT INTO sale_items (sale_id, model_id, part_id, part_name, part_cost, quantity, material_id, printer_id, item_price, customer_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(sale_id, *item) for item in items],
            )
        except ValueError:
            flash("Bitte gültige Preis- und Verpackungskosten eingeben.", "error")
            return redirect(url_for("main.new_sale", year=year))
    flash(f"Auftrag {number:03d} gespeichert.", "success")
    return redirect(url_for("main.sales", year=year))


@main.route("/years/<int:year>/sales/new", methods=["GET"])
def new_sale(year):
    with database() as connection:
        models = sale_form_models(connection)
        categories = connection.execute("SELECT * FROM categories ORDER BY name").fetchall()
        material_list = connection.execute("SELECT * FROM materials WHERE active=1 ORDER BY name").fetchall()
        printer_list = connection.execute("SELECT * FROM printers WHERE active=1 ORDER BY name").fetchall()
        shipping = connection.execute("SELECT * FROM shipping_methods WHERE year=? ORDER BY name", (year,)).fetchall()
        next_number = connection.execute("SELECT COALESCE(MAX(number),0)+1 FROM sales WHERE year=?", (year,)).fetchone()[0]
        defaults = {str(model["id"]): {"price": model["sale_price"],
                    "materials": [dict(row) for row in connection.execute(
                        "SELECT DISTINCT m.id, m.name FROM model_parts mp JOIN materials m ON m.id=mp.material_id WHERE mp.model_id=?", (model["id"],))],
                    "printers": [dict(row) for row in connection.execute(
                        "SELECT DISTINCT p.id, p.name FROM model_parts mp JOIN printers p ON p.id=mp.printer_id WHERE mp.model_id=?", (model["id"],))]}
                    for model in models}
        model_category_map = {str(model["id"]): model["category_ids"] for model in models}
        archived_model_ids = [str(model["id"]) for model in models if model["archived"]]
        material_ids = [None] + [item["id"] for item in material_list]
        printer_ids = [None] + [item["id"] for item in printer_list]
        part_costs = sale_form_part_costs(connection, models, material_ids, printer_ids)
        model_costs = {
            str(model["id"]): {
                f"{material_id or 0}:{printer_id or 0}": model_cost(connection, model["id"], material_id, printer_id)
                for material_id in material_ids for printer_id in printer_ids
            }
            for model in models
        }
        known_customers = connection.execute(
            """SELECT company_name, customer_name, street, postal_code, city FROM sales
            WHERE TRIM(customer_name)<>'' ORDER BY date DESC, id DESC"""
        ).fetchall()
    return render_template("sale_form.html", year=year, models=models, materials=material_list,
                           printers=printer_list, shipping=shipping, next_number=next_number,
                           defaults=defaults, model_costs=model_costs, today=date.today().isoformat(),
                           sale=None, items=[], known_customers=known_customers,
                           categories=categories, show_archived=False, item_notes=[],
                           model_category_map=model_category_map, archived_model_ids=archived_model_ids,
                           part_costs=part_costs)


@main.route("/sales/<int:sale_id>/edit", methods=["GET", "POST"])
def edit_sale(sale_id):
    with database() as connection:
        sale = connection.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if sale is None:
            abort(404)
        if request.method == "POST":
            try:
                sale_date = date.fromisoformat(request.form.get("date", ""))
                shipping_date_value = request.form.get("shipping_date", "").strip()
                shipping_date = date.fromisoformat(shipping_date_value).isoformat() if shipping_date_value else ""
                number = int(request.form.get("number", ""))
                packaging = float(request.form.get("packaging_cost", "0").replace(",", ".") or 0)
                model_ids = request.form.getlist("item_model_id")
                quantities = request.form.getlist("item_quantity")
                prices = request.form.getlist("item_price")
                materials = request.form.getlist("item_material_id")
                printers = request.form.getlist("item_printer_id")
                customer_notes = request.form.getlist("item_customer_note")
                part_ids = request.form.getlist("item_part_id")
                category_ids = request.form.getlist("item_category_id")
                customer_notes = customer_notes or [""] * len(model_ids)
                part_ids = part_ids or [""] * len(model_ids)
                category_ids = category_ids or [""] * len(model_ids)
                if (sale_date.year != sale["year"] or number < 1 or packaging < 0 or not model_ids
                        or len({len(model_ids), len(quantities), len(prices), len(materials), len(printers),
                                len(customer_notes), len(part_ids), len(category_ids)}) != 1):
                    raise ValueError
                submitted_items = []
                old_items = connection.execute(
                    "SELECT printed, model_id, part_id, part_name, part_cost FROM sale_items WHERE sale_id=? ORDER BY id",
                    (sale_id,),
                ).fetchall()
                for model_value, quantity_value, price_value, material_value, printer_value, customer_note, part_value, category_value in zip(
                    model_ids, quantities, prices, materials, printers, customer_notes, part_ids, category_ids
                ):
                    model_id = int(model_value)
                    quantity = int(quantity_value)
                    price = float(price_value.replace(",", "."))
                    part_id = int(part_value) if part_value else None
                    category_id = int(category_value) if category_value else None
                    model_row = connection.execute(
                        "SELECT id, archived FROM models WHERE id=?", (model_id,)
                    ).fetchone()
                    if (quantity < 1 or price < 0 or model_row is None
                            or (model_row["archived"] and "show_archived" not in request.form)):
                        raise ValueError
                    if category_id and connection.execute(
                        "SELECT 1 FROM model_categories WHERE model_id=? AND category_id=?",
                        (model_id, category_id),
                    ).fetchone() is None:
                        raise ValueError
                    material_id = int(material_value) if material_value else None
                    printer_id = int(printer_value) if printer_value else None
                    part_name = ""
                    part_cost = 0.0
                    if part_id:
                        part = connection.execute(
                            "SELECT name FROM model_parts WHERE id=? AND model_id=?", (part_id, model_id)
                        ).fetchone()
                        if part:
                            part_name = part["name"]
                            part_cost = model_part_cost(connection, part_id, material_id, printer_id)
                        else:
                            old_part = next((row for row in old_items
                                             if row["part_id"] == part_id and row["model_id"] == model_id), None)
                            if old_part is None:
                                raise ValueError
                            part_name = old_part["part_name"]
                            part_cost = old_part["part_cost"]
                    submitted_items.append((model_id, part_id, part_name, part_cost, quantity,
                                            material_id, printer_id, price, customer_note.strip()))
                duplicate = connection.execute(
                    "SELECT id FROM sales WHERE year=? AND number=? AND id<>?",
                    (sale["year"], number, sale_id),
                ).fetchone()
                if duplicate:
                    raise ValueError
                shipping_id = request.form.get("shipping_method_id", type=int) or None
                shipping_price = 0.0
                if shipping_id:
                    shipping_row = connection.execute(
                        "SELECT price FROM shipping_methods WHERE id=? AND year=?", (shipping_id, sale["year"])
                    ).fetchone()
                    if shipping_row is None:
                        raise ValueError
                    shipping_price = shipping_row["price"]
            except (KeyError, TypeError, ValueError):
                flash("Bitte gültige Auftrags-, Datums- und Positionsdaten eingeben.", "error")
                return redirect(url_for("main.edit_sale", sale_id=sale_id))
            first_model, _, _, _, first_quantity, first_material, first_printer, first_price, _ = submitted_items[0]
            connection.execute(
                """UPDATE sales SET number=?, date=?, shipping_date=?, platform=?, company_name=?, customer_name=?, street=?,
                postal_code=?, city=?, model_id=?, quantity=?, material_id=?, printer_id=?, item_price=?,
                shipping_method_id=?, shipping_price=?, packaging_cost=?, note=?, show_archived=? WHERE id=?""",
                (number, sale_date.isoformat(), shipping_date, request.form.get("platform", "").strip(),
                 request.form.get("company_name", "").strip(), request.form.get("customer_name", "").strip(),
                 request.form.get("street", "").strip(),
                 request.form.get("postal_code", "").strip(), request.form.get("city", "").strip(),
                 first_model, first_quantity, first_material, first_printer, first_price, shipping_id,
                 shipping_price, packaging, request.form.get("note", "").strip(),
                 int("show_archived" in request.form), sale_id),
            )
            connection.execute("DELETE FROM sale_items WHERE sale_id=?", (sale_id,))
            connection.executemany(
                "INSERT INTO sale_items (sale_id, model_id, part_id, part_name, part_cost, quantity, material_id, printer_id, item_price, customer_note, printed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(sale_id, *item, old_items[index]["printed"] if index < len(old_items) else 0)
                 for index, item in enumerate(submitted_items)],
            )
            flash(f"Auftrag {number:03d} aktualisiert.", "success")
            return redirect(url_for("main.sales", year=sale["year"]))

        models = sale_form_models(connection)
        categories = connection.execute("SELECT * FROM categories ORDER BY name").fetchall()
        material_list = connection.execute("SELECT * FROM materials WHERE active=1 ORDER BY name").fetchall()
        printer_list = connection.execute("SELECT * FROM printers WHERE active=1 ORDER BY name").fetchall()
        shipping = connection.execute("SELECT * FROM shipping_methods WHERE year=? ORDER BY name", (sale["year"],)).fetchall()
        items = sale_items(connection, sale)
        defaults = {str(model["id"]): {"price": model["sale_price"],
                    "materials": [dict(row) for row in connection.execute(
                        "SELECT DISTINCT m.id, m.name FROM model_parts mp JOIN materials m ON m.id=mp.material_id WHERE mp.model_id=?", (model["id"],))],
                    "printers": [dict(row) for row in connection.execute(
                        "SELECT DISTINCT p.id, p.name FROM model_parts mp JOIN printers p ON p.id=mp.printer_id WHERE mp.model_id=?", (model["id"],))]}
                    for model in models}
        model_category_map = {str(model["id"]): model["category_ids"] for model in models}
        archived_model_ids = [str(model["id"]) for model in models if model["archived"]]
        material_ids = [None] + [item["id"] for item in material_list]
        printer_ids = [None] + [item["id"] for item in printer_list]
        part_costs = sale_form_part_costs(connection, models, material_ids, printer_ids)
        model_costs = {str(model["id"]): {
            f"{material_id or 0}:{printer_id or 0}": model_cost(connection, model["id"], material_id, printer_id)
            for material_id in material_ids for printer_id in printer_ids
        } for model in models}
        known_customers = connection.execute(
            """SELECT company_name, customer_name, street, postal_code, city FROM sales
            WHERE TRIM(customer_name)<>'' ORDER BY date DESC, id DESC"""
        ).fetchall()
        next_number = connection.execute(
            "SELECT COALESCE(MAX(number),0)+1 FROM sales WHERE year=?", (sale["year"],)
        ).fetchone()[0]
    return render_template("sale_form.html", year=sale["year"], sale=sale, items=items, models=models,
                           materials=material_list, printers=printer_list, shipping=shipping,
                           next_number=next_number, defaults=defaults, model_costs=model_costs,
                           today=date.today().isoformat(), known_customers=known_customers,
                           categories=categories, show_archived=bool(sale["show_archived"]),
                           item_notes=[item["customer_note"] for item in items],
                           model_category_map=model_category_map, archived_model_ids=archived_model_ids,
                           part_costs=part_costs)


@main.post("/sales/<int:sale_id>/status")
def update_sale_status(sale_id):
    shipping_date_value = request.form.get("shipping_date", "").strip()
    try:
        shipping_date = date.fromisoformat(shipping_date_value).isoformat() if shipping_date_value else ""
    except ValueError:
        flash("Bitte ein gültiges Versanddatum eingeben.", "error")
        with database() as connection:
            sale = connection.execute("SELECT year FROM sales WHERE id=?", (sale_id,)).fetchone()
            if sale is None:
                abort(404)
        return redirect(url_for("main.sales", year=sale["year"]))
    with database() as connection:
        sale = connection.execute("SELECT year FROM sales WHERE id=?", (sale_id,)).fetchone()
        if sale is None:
            abort(404)
        connection.execute(
            "UPDATE sales SET paid=?, printed=?, shipped=?, shipping_date=? WHERE id=?",
            (int("paid" in request.form), int("printed" in request.form), int("shipped" in request.form),
             shipping_date, sale_id),
        )
        connection.execute(
            "UPDATE sale_items SET printed=? WHERE sale_id=?",
            (int("printed" in request.form), sale_id),
        )
    return redirect(url_for("main.sales", year=sale["year"]))


@main.post("/sale-items/<int:item_id>/printed")
def update_sale_item_printed(item_id):
    with database() as connection:
        row = connection.execute(
            "SELECT si.printed, s.year FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE si.id=?",
            (item_id,),
        ).fetchone()
        if row is None:
            abort(404)
        connection.execute(
            "UPDATE sale_items SET printed=? WHERE id=?",
            (int(request.form.get("printed", "0") == "1"), item_id),
        )
    return redirect(url_for("main.sales", year=row["year"]))


@main.route("/years/<int:year>/shipping", methods=["GET", "POST"])
def shipping_methods(year):
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            price = float(request.form.get("price", "0").replace(",", "."))
            if price < 0 or not name:
                raise ValueError
        except ValueError:
            flash("Bitte Namen und gültigen Preis eingeben.", "error")
        else:
            with database() as connection:
                shipping_id = request.form.get("shipping_id", type=int)
                if shipping_id:
                    shipping = connection.execute(
                        "SELECT id FROM shipping_methods WHERE id=? AND year=?", (shipping_id, year)
                    ).fetchone()
                    if shipping is None:
                        abort(404)
                    duplicate = connection.execute(
                        "SELECT 1 FROM shipping_methods WHERE year=? AND name=? AND id<>?",
                        (year, name, shipping_id),
                    ).fetchone()
                    if duplicate:
                        flash("Eine Versandart mit diesem Namen gibt es in diesem Jahr bereits.", "error")
                    else:
                        connection.execute(
                            "UPDATE shipping_methods SET name=?, price=? WHERE id=?",
                            (name, price, shipping_id),
                        )
                else:
                    connection.execute("INSERT INTO shipping_methods (year,name,price) VALUES (?,?,?) ON CONFLICT(year,name) DO UPDATE SET price=excluded.price",
                                       (year, name, price))
        return redirect(url_for("main.shipping_methods", year=year))
    with database() as connection:
        rows = connection.execute("SELECT * FROM shipping_methods WHERE year=? ORDER BY name", (year,)).fetchall()
    return render_template("shipping.html", year=year, rows=rows)


@main.post("/shipping/<int:shipping_id>/delete")
def delete_shipping(shipping_id):
    with database() as connection:
        row = connection.execute("SELECT year FROM shipping_methods WHERE id=?", (shipping_id,)).fetchone()
        if row:
            connection.execute("UPDATE sales SET shipping_method_id=NULL WHERE shipping_method_id=?", (shipping_id,))
            connection.execute("DELETE FROM shipping_methods WHERE id=?", (shipping_id,))
            year = row["year"]
        else:
            abort(404)
    return redirect(url_for("main.shipping_methods", year=year))


@main.route("/years/<int:year>/statistics")
def statistics(year):
    start = request.args.get("start", f"{year}-01-01")
    today = date.today()
    default_end = min(date(year, 12, 31), today) if year == today.year else date(year, 12, 31)
    end = request.args.get("end", default_end.isoformat())
    view = request.args.get("view", "monthly")
    if view not in {"monthly", "daily"}:
        view = "monthly"
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        start_date = date(year, 1, 1)
        end_date = default_end
        start = start_date.isoformat()
        end = end_date.isoformat()
    year_start = date(year, 1, 1)
    year_end = min(date(year, 12, 31), today) if year == today.year else date(year, 12, 31)
    start_date = max(start_date, year_start)
    end_date = min(end_date, year_end)
    start = start_date.isoformat()
    end = end_date.isoformat()
    if year == today.year and end_date > today:
        end_date = today
        end = end_date.isoformat()
    if start_date > end_date:
        start_date, end_date = end_date, start_date
        start, end = start_date.isoformat(), end_date.isoformat()
    with database() as connection:
        order_rows = connection.execute(
            "SELECT * FROM sales WHERE year=? AND date BETWEEN ? AND ?", (year, start, end)
        ).fetchall()
        expense_rows = connection.execute(
            "SELECT date, amount FROM expenses WHERE year=? AND date BETWEEN ? AND ?", (year, start, end)
        ).fetchall()
        period_totals = {}
        income = 0.0
        order_costs = 0.0
        for order in order_rows:
            financials = sale_financials(connection, order)
            income += financials["income"]
            order_costs += financials["costs"]
            period_key = order["date"] if view == "daily" else order["date"][:7]
            period = period_totals.setdefault(period_key,
                                              {"income": 0.0, "expenses": 0.0})
            period["income"] += financials["income"]
        expense_total = 0.0
        for item in expense_rows:
            expense_total += item["amount"]
            period_key = item["date"] if view == "daily" else item["date"][:7]
            period_totals.setdefault(period_key, {"income": 0.0, "expenses": 0.0})["expenses"] += item["amount"]
        if view == "daily":
            day = start_date
            while day <= end_date:
                period_totals.setdefault(day.isoformat(), {"income": 0.0, "expenses": 0.0})
                day += timedelta(days=1)
        month_rows = [{"period": key, "label": date.fromisoformat(
            key if view == "daily" else f"{key}-01").strftime("%d.%m" if view == "daily" else "%m"), **values}
            for key, values in sorted(period_totals.items())]
        available_years = years(connection)
    profit = income - order_costs
    result = profit - expense_total
    max_value = max([max(row["income"], row["expenses"])
                     for row in month_rows] + [1])
    return render_template("statistics.html", year=year, years=available_years, months=month_rows,
                           start=start, end=end, income=income, order_costs=order_costs,
                           profit=profit, result=result, expense=expense_total,
                           sale_count=len(order_rows), max_value=max_value, view=view)


@main.route("/years/<int:year>/export-euer.pdf")
def export_euer(year):
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    with database() as connection:
        rows = connection.execute(
            "SELECT g.name, COALESCE(SUM(e.amount),0) AS total FROM expense_groups g LEFT JOIN expenses e ON e.group_id=g.id AND e.year=? GROUP BY g.id ORDER BY g.name",
            (year,),
        ).fetchall()
        income = 0.0
        for order in connection.execute("SELECT * FROM sales WHERE year=?", (year,)).fetchall():
            income += sale_financials(connection, order)["income"]
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Einnahmenüberschussrechnung {year}")
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(52, height - 66, f"Einnahmenüberschussrechnung {year}")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(52, height - 91, "Jahresübersicht für die steuerliche Auswertung")
    y = height - 145
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(52, y, "Ausgaben")
    y -= 25
    expenses_sum = 0
    pdf.setFont("Helvetica", 11)
    for row in rows:
        total = row["total"] or 0
        expenses_sum += total
        pdf.drawString(62, y, row["name"])
        pdf.drawRightString(width - 52, y, money(total))
        y -= 22
    y -= 8
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(62, y, "Summe Ausgaben")
    pdf.drawRightString(width - 52, y, money(expenses_sum))
    y -= 38
    pdf.drawString(52, y, "Einnahmen")
    y -= 24
    pdf.setFont("Helvetica", 11)
    pdf.drawString(62, y, "Verkäufe")
    pdf.drawRightString(width - 52, y, money(income))
    y -= 36
    result = income - expenses_sum
    pdf.setStrokeColorRGB(0.3, 0.8, 0.48) if result >= 0 else pdf.setStrokeColorRGB(0.9, 0.3, 0.3)
    pdf.line(52, y + 15, width - 52, y + 15)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(52, y - 3, "Überschuss" if result >= 0 else "Fehlbetrag")
    pdf.drawRightString(width - 52, y - 3, money(abs(result)))
    pdf.save()
    output.seek(0)
    return send_file(output, mimetype="application/pdf", as_attachment=True,
                     download_name=f"Einnahmenueberschussrechnung-{year}.pdf")


@main.route("/sales/<int:sale_id>/invoice.pdf")
def invoice(sale_id):
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfgen import canvas

    with database() as connection:
        sale = connection.execute(
            """SELECT s.*, m.name AS model_name, sh.name AS shipping_name
            FROM sales s JOIN models m ON m.id=s.model_id
            LEFT JOIN shipping_methods sh ON sh.id=s.shipping_method_id WHERE s.id=?""", (sale_id,)
        ).fetchone()
        if sale is None:
            abort(404)
        items = sale_items(connection, sale)
        settings = invoice_settings(connection)
    output = BytesIO()

    payment_footer = [
        f"Bank: {settings['invoice_payment_bank']}" if settings.get("invoice_payment_bank") else "",
        f"IBAN: {settings['invoice_payment_iban']}" if settings.get("invoice_payment_iban") else "",
        f"BIC: {settings['invoice_payment_bic']}" if settings.get("invoice_payment_bic") else "",
    ]
    payment_footer = [line for line in payment_footer if line]
    payment_footer = [
        wrapped_line
        for line in payment_footer
        for wrapped_line in simpleSplit(line, "Helvetica", 10, 235)
    ]
    tax_footer = [
        f"Steuernummer: {settings['invoice_tax_number']}" if settings.get("invoice_tax_number") else "",
        f"Wirtschafts-Id.Nr.: {settings['invoice_economic_id']}" if settings.get("invoice_economic_id") else "",
    ]
    tax_footer = [line for line in tax_footer if line]
    tax_footer = [
        wrapped_line
        for line in tax_footer
        for wrapped_line in simpleSplit(line, "Helvetica", 10, 235)
    ]
    footer_line_count = max(len(payment_footer), len(tax_footer), 1)
    footer_top = 34 + (footer_line_count - 1) * 12
    footer_bar_y = footer_top + 9
    content_bottom = footer_bar_y + 14

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.saved_pages = []

        def showPage(self):
            self.saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            self.saved_pages.append(dict(self.__dict__))
            page_count = len(self.saved_pages)
            for page_number, state in enumerate(self.saved_pages, start=1):
                self.__dict__.update(state)
                self.setStrokeColorRGB(0.72, 0.74, 0.74)
                self.setLineWidth(0.6)
                self.line(52, footer_bar_y, A4[0] - 52, footer_bar_y)
                self.setFont("Helvetica", 10)
                self.setFillColorRGB(0.38, 0.4, 0.4)
                for line_index, line in enumerate(payment_footer):
                    self.drawString(52, footer_top - line_index * 12, line)
                for line_index, line in enumerate(tax_footer):
                    self.drawRightString(A4[0] - 52, footer_top - line_index * 12, line)
                self.setFont("Helvetica", 8)
                self.drawCentredString(A4[0] / 2, 13, f"Seite {page_number} von {page_count}")
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

    pdf = NumberedCanvas(output, pagesize=A4)
    width, height = A4
    bill_number = f"{sale['year']}-{sale['number']:03d}-{sale['date'][5:7]}-{sale['date'][8:10]}"
    pdf.setTitle(f"Rechnung-{bill_number}")
    logo_filename = settings.get("invoice_logo", "")
    logo_path = UPLOAD_DIR / logo_filename if logo_filename and Path(logo_filename).name == logo_filename else None
    logo_width = 0
    if logo_path and logo_path.is_file():
        logo_width = 382.5
        pdf.drawImage(ImageReader(str(logo_path)), 48, height - 160.5, width=logo_width, height=130.5,
                      preserveAspectRatio=True, anchor="sw", mask="auto")
    seller_width = width - 52 - (48 + logo_width + 12) if logo_width else width - 104
    vendor_sections = [
        (settings.get("invoice_vendor_name", ""), 10),
        *[(line, 10) for line in settings.get("invoice_vendor_address", "").splitlines()],
        (settings.get("invoice_vendor_phone", ""), 10),
        (settings.get("invoice_vendor_email", ""), 10),
    ]
    vendor_lines = [
        (wrapped, font_size)
        for line, font_size in vendor_sections if line
        for wrapped in ([line] if line == settings.get("invoice_vendor_name", "") else simpleSplit(line, "Helvetica", font_size, seller_width))
    ]
    vendor_y = height - 93
    for line, font_size in vendor_lines:
        pdf.setFont("Helvetica", font_size)
        pdf.drawRightString(width - 50, vendor_y, line)
        vendor_y -= font_size + 4
    address_lines = (sale["company_name"], sale["customer_name"], sale["street"], f"{sale['postal_code']} {sale['city']}".strip())
    pdf.setFont("Helvetica", 10)
    buyer_y = height - 250
    for line_index, line in enumerate(address_lines):
        if line:
            pdf.drawString(52, buyer_y - line_index * 16, line)
    pdf.setFont("Helvetica", 10)
    def draw_date_field(label, value, y):
        value_width = pdf.stringWidth(value, "Helvetica", 10)
        value_x = width - 52 - value_width
        pdf.drawRightString(value_x - 10, y, label)
        pdf.drawString(value_x, y, value)

    draw_date_field("Datum:", date.fromisoformat(sale["date"]).strftime("%d.%m.%Y"), buyer_y)
    shipping_date = sale["shipping_date"] or sale["date"]
    draw_date_field("Lieferdatum:", date.fromisoformat(shipping_date).strftime("%d.%m.%Y"), buyer_y - 18)
    draw_date_field("Rechnungs-Nr.:", bill_number, buyer_y - 36)
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawString(52, height - 337, "Rechnung")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(52, height - 359, "Im Folgenden der/die erworbene/n Artikel, die ich in Rechnung stelle:")
    y = height - 394
    positions = [(item["model_name"], item["quantity"], item["item_price"]) for item in items]
    if sale["shipping_price"]:
        positions.append((f"Versand: {sale['shipping_name'] or 'Versand'}", 1, sale["shipping_price"]))
    item_sum = sum(quantity * price for _, quantity, price in positions)

    def draw_table_header(top):
        pdf.setFillColorRGB(0.12, 0.13, 0.13)
        pdf.rect(52, top - 24, width - 104, 25, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(59, top - 16, "Pos.")
        pdf.drawString(91, top - 16, "Bezeichnung")
        pdf.drawRightString(width - 224, top - 16, "Menge")
        pdf.drawRightString(width - 130, top - 16, "Einzelpreis")
        pdf.drawRightString(width - 59, top - 16, "Gesamtpreis")
        pdf.setFillColorRGB(0.1, 0.1, 0.1)

    draw_table_header(y)
    y -= 42
    pdf.setFont("Helvetica", 9)
    for position, (label, quantity, price) in enumerate(positions, start=1):
        description_lines = simpleSplit(label, "Helvetica", 9, width - 91 - (width - 224) - 16)
        row_height = max(22, len(description_lines) * 12 + 8)
        if y - row_height < content_bottom + 15:
            pdf.showPage()
            y = height - 70
            draw_table_header(y)
            y -= 42
            pdf.setFont("Helvetica", 9)
        pdf.drawString(59, y, str(position))
        for line_index, line in enumerate(description_lines):
            pdf.drawString(91, y - line_index * 12, line)
        pdf.drawRightString(width - 224, y, f"{quantity} Stk")
        pdf.drawRightString(width - 130, y, money(price))
        pdf.drawRightString(width - 59, y, money(quantity * price))
        y -= row_height
    y -= 4
    if y < content_bottom + 100:
        pdf.showPage()
        y = height - 80
    pdf.setStrokeColorRGB(0.75, 0.77, 0.77)
    pdf.line(52, y, width - 52, y)
    y -= 21
    pdf.setFont("Helvetica", 10)
    pdf.drawRightString(width - 59, y, f"Summe Positionen   {money(item_sum)}")
    y -= 26
    pdf.setFont("Helvetica-Bold", 11)
    pdf.setFillColorRGB(0.92, 0.93, 0.93)
    pdf.rect(52, y - 9, width - 104, 25, fill=1, stroke=0)
    pdf.setFillColorRGB(0.1, 0.1, 0.1)
    pdf.drawRightString(width - 59, y, f"Rechnungsbetrag   {money(item_sum)}")
    y -= 36
    bold_text = settings.get("invoice_bold_text", "")
    if bold_text:
        pdf.setFont("Helvetica-Bold", 10)
        for line in simpleSplit(bold_text, "Helvetica-Bold", 10, width - 104):
            if y < content_bottom:
                pdf.showPage()
                y = height - 70
                pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(52, y, line)
            y -= 14
    additional_text = settings.get("invoice_additional_text", "")
    if additional_text:
        y -= 15
        pdf.setFont("Helvetica", 9)
        for paragraph in additional_text.splitlines():
            lines = simpleSplit(paragraph, "Helvetica", 9, width - 104)
            if not lines:
                y -= 13
            for line in lines:
                if y < content_bottom:
                    pdf.showPage()
                    y = height - 70
                    pdf.setFont("Helvetica", 9)
                pdf.drawString(52, y, line)
                y -= 13
    pdf.save()
    output.seek(0)
    filename = f"Rechnung-{bill_number}.pdf"
    return send_file(output, mimetype="application/pdf", as_attachment=True, download_name=filename)