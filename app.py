
import os
import re
import uuid
import math
import sqlite3
from pathlib import Path
from datetime import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, abort, send_from_directory
)
from werkzeug.utils import secure_filename


# =========================================================
# 1. PROJECT CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "lost_found.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "findit-demo-secret-key"
)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


# =========================================================
# 2. AWS S3 CONFIGURATION
# =========================================================

S3_BUCKET = os.environ.get(
    "S3_BUCKET",
    "findit-lost-found-photos-2026"
)

AWS_REGION = os.environ.get(
    "AWS_DEFAULT_REGION",
    "ap-southeast-2"
)

AWS_PROFILE = os.environ.get(
    "AWS_PROFILE",
    "findit"
).strip()


def create_s3_client():
    """Create S3 client using the local AWS profile."""

    if AWS_PROFILE:
        try:
            session = boto3.Session(
                profile_name=AWS_PROFILE,
                region_name=AWS_REGION
            )
        except ProfileNotFound:
            session = boto3.Session(
                region_name=AWS_REGION
            )
    else:
        session = boto3.Session(
            region_name=AWS_REGION
        )

    return session.client(
        "s3",
        region_name=AWS_REGION
    )


s3_client = create_s3_client()


# =========================================================
# 3. DATABASE CONNECTION
# =========================================================

def get_db():

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    return conn


# =========================================================
# 4. DATABASE CREATION AND MIGRATION
# =========================================================

def init_db():

    with get_db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS items (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                item_type TEXT NOT NULL,

                item_name TEXT NOT NULL,

                category TEXT NOT NULL,

                description TEXT,

                location TEXT NOT NULL,

                item_date TEXT NOT NULL,

                contact_name TEXT NOT NULL,

                contact_info TEXT NOT NULL,

                image_filename TEXT,

                status TEXT DEFAULT 'Open',

                created_at TEXT NOT NULL,

                latitude REAL,

                longitude REAL
            )
        """)

        # Preserve existing database records.
        existing_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(items)"
            ).fetchall()
        }

        # Add latitude column to existing database.
        if "latitude" not in existing_columns:

            conn.execute("""
                ALTER TABLE items
                ADD COLUMN latitude REAL
            """)

        # Add longitude column to existing database.
        if "longitude" not in existing_columns:

            conn.execute("""
                ALTER TABLE items
                ADD COLUMN longitude REAL
            """)


# =========================================================
# 5. GPS LOCATION VALIDATION
# =========================================================

def parse_coordinate(value, minimum, maximum):

    if value is None or str(value).strip() == "":
        return None

    try:
        number = float(value)

    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    if not minimum <= number <= maximum:
        return None

    return number


# =========================================================
# 6. DISTANCE CALCULATION
# =========================================================

def distance_meters(lat1, lon1, lat2, lon2):

    earth_radius = 6371000

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )

    return (
        2 * earth_radius
        * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    )


def locations_match(item, other):

    coords_available = all(
        value is not None
        for value in (
            item["latitude"],
            item["longitude"],
            other["latitude"],
            other["longitude"]
        )
    )

    if coords_available:

        distance = distance_meters(
            float(item["latitude"]),
            float(item["longitude"]),
            float(other["latitude"]),
            float(other["longitude"])
        )

        return distance <= 100

    return (
        (other["location"] or "").strip().casefold()
        == (item["location"] or "").strip().casefold()
    )


# =========================================================
# 7. IMAGE VALIDATION
# =========================================================

def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# =========================================================
# 8. AWS S3 IMAGE UPLOAD
# =========================================================

def upload_image_to_s3(image):

    original_name = secure_filename(
        image.filename
    )

    extension = original_name.rsplit(
        ".", 1
    )[1].lower()

    object_key = (
        f"{uuid.uuid4().hex}.{extension}"
    )

    content_type = (
        image.mimetype
        or "application/octet-stream"
    )

    s3_client.upload_fileobj(
        image,
        S3_BUCKET,
        object_key,
        ExtraArgs={
            "ContentType": content_type
        }
    )

    return object_key


# =========================================================
# 9. SMART MATCHING
# =========================================================

def words(value):

    return set(
        re.findall(
            r"[a-z0-9]+",
            (value or "").lower()
        )
    )


def find_matches(item, all_items):

    matches = []

    target_words = words(
        item["item_name"]
        + " "
        + (item["description"] or "")
    )

    for other in all_items:

        if other["id"] == item["id"]:
            continue

        if other["status"] != "Open":
            continue

        if other["item_type"] == item["item_type"]:
            continue

        score = 0
        reasons = []

        # Same category: 40 points.

        if other["category"] == item["category"]:

            score += 40

            reasons.append("same category")

        # Similar keywords: maximum 40 points.

        common_words = target_words.intersection(
            words(
                other["item_name"]
                + " "
                + (other["description"] or "")
            )
        )

        if common_words:

            score += min(
                40,
                15 * len(common_words)
            )

            reasons.append("similar keywords")

        # Same or nearby location: 20 points.

        if locations_match(item, other):

            score += 20

            reasons.append(
                "nearby map pin or same location"
            )

        if score >= 40:

            matches.append({

                "item": other,

                "score": min(score, 100),

                "reasons": ", ".join(reasons)

            })

    return sorted(
        matches,
        key=lambda x: x["score"],
        reverse=True
    )


# =========================================================
# 10. HOME PAGE AND SEARCH
# =========================================================

@app.route("/")
def index():

    search = request.args.get(
        "q", ""
    ).strip()

    item_type = request.args.get(
        "type", "All"
    )

    category = request.args.get(
        "category", "All"
    )

    sql = "SELECT * FROM items WHERE 1=1"

    params = []

    if search:

        sql += """
            AND (
                item_name LIKE ?
                OR description LIKE ?
                OR location LIKE ?
            )
        """

        pattern = f"%{search}%"

        params.extend([
            pattern,
            pattern,
            pattern
        ])

    if item_type in ("Lost", "Found"):

        sql += " AND item_type = ?"

        params.append(item_type)

    if category != "All":

        sql += " AND category = ?"

        params.append(category)

    sql += " ORDER BY id DESC"

    with get_db() as conn:

        items = conn.execute(
            sql, params
        ).fetchall()

        stats = {

            "total": conn.execute(
                "SELECT COUNT(*) FROM items"
            ).fetchone()[0],

            "lost": conn.execute(
                """
                SELECT COUNT(*) FROM items
                WHERE item_type='Lost'
                AND status='Open'
                """
            ).fetchone()[0],

            "found": conn.execute(
                """
                SELECT COUNT(*) FROM items
                WHERE item_type='Found'
                AND status='Open'
                """
            ).fetchone()[0],

            "returned": conn.execute(
                """
                SELECT COUNT(*) FROM items
                WHERE status='Returned'
                """
            ).fetchone()[0]

        }

    return render_template(
        "index.html",
        items=items,
        stats=stats,
        search=search,
        item_type=item_type,
        category=category
    )


# =========================================================
# 11. REPORT LOST OR FOUND ITEM WITH MAP PIN
# =========================================================

@app.route("/report", methods=["GET", "POST"])
def report():

    if request.method == "POST":

        item_type = request.form.get(
            "item_type", ""
        )

        item_name = request.form.get(
            "item_name", ""
        ).strip()

        category = request.form.get(
            "category", ""
        )

        description = request.form.get(
            "description", ""
        ).strip()

        location = request.form.get(
            "location", ""
        ).strip()

        item_date = request.form.get(
            "item_date", ""
        )

        contact_name = request.form.get(
            "contact_name", ""
        ).strip()

        contact_info = request.form.get(
            "contact_info", ""
        ).strip()

        raw_latitude = request.form.get(
            "latitude"
        )

        raw_longitude = request.form.get(
            "longitude"
        )

        latitude = parse_coordinate(
            raw_latitude, -90, 90
        )

        longitude = parse_coordinate(
            raw_longitude, -180, 180
        )

        image = request.files.get("image")

        one_coordinate_missing = (
            (
                (raw_latitude or "").strip() != ""
                or (raw_longitude or "").strip() != ""
            )
            and (
                latitude is None
                or longitude is None
            )
        )

        if one_coordinate_missing:

            flash(
                "Please select a valid location pin on the map.",
                "error"
            )

            return render_template(
                "report.html"
            ), 400

        # A map pin can be used without a typed landmark.

        if (
            not location
            and latitude is not None
            and longitude is not None
        ):

            location = (
                f"Map pin: {latitude:.6f}, "
                f"{longitude:.6f}"
            )

        if (

            item_type not in ("Lost", "Found")

            or not all([
                item_name,
                category,
                location,
                item_date,
                contact_name,
                contact_info
            ])

        ):

            flash(
                "Complete the required fields and either enter "
                "a place/landmark or select a location pin on the map.",
                "error"
            )

            return render_template(
                "report.html"
            ), 400

        image_filename = None

        # Upload image to AWS S3.

        if image and image.filename:

            if not allowed_file(image.filename):

                flash(
                    "Image must be PNG, JPG, JPEG or WEBP.",
                    "error"
                )

                return render_template(
                    "report.html"
                ), 400

            try:

                image_filename = upload_image_to_s3(
                    image
                )

            except (BotoCoreError, ClientError):

                app.logger.exception(
                    "FindIt could not upload the image to S3"
                )

                flash(
                    "Image upload to cloud storage failed. "
                    "Check the AWS profile, bucket name, region "
                    "and S3 permissions, then try again.",
                    "error"
                )

                return render_template(
                    "report.html"
                ), 503

        # Save details and GPS coordinates in SQLite.

        with get_db() as conn:

            conn.execute("""
                INSERT INTO items (
                    item_type,
                    item_name,
                    category,
                    description,
                    location,
                    item_date,
                    contact_name,
                    contact_info,
                    image_filename,
                    status,
                    created_at,
                    latitude,
                    longitude
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'Open', ?, ?, ?
                )
            """, (

                item_type,
                item_name,
                category,
                description,
                location,
                item_date,
                contact_name,
                contact_info,
                image_filename,
                datetime.now().isoformat(
                    timespec="minutes"
                ),
                latitude,
                longitude

            ))

        flash(
            f"{item_type} item reported successfully!",
            "success"
        )

        return redirect(
            url_for("index")
        )

    return render_template("report.html")


# =========================================================
# 12. ITEM DETAILS, MAP AND MATCHING
# =========================================================

@app.route("/item/<int:item_id>")
def item_detail(item_id):

    with get_db() as conn:

        item = conn.execute(
            "SELECT * FROM items WHERE id=?",
            (item_id,)
        ).fetchone()

        all_items = conn.execute(
            "SELECT * FROM items WHERE status='Open'"
        ).fetchall()

    if item is None:

        abort(404)

    matches = (
        find_matches(item, all_items)
        if item["status"] == "Open"
        else []
    )

    return render_template(
        "item.html",
        item=item,
        matches=matches
    )


# =========================================================
# 13. MARK ITEM AS RETURNED
# =========================================================

@app.route(
    "/item/<int:item_id>/returned",
    methods=["POST"]
)
def mark_returned(item_id):

    with get_db() as conn:

        result = conn.execute(
            """
            UPDATE items
            SET status='Returned'
            WHERE id=?
            """,
            (item_id,)
        )

        if result.rowcount == 0:

            abort(404)

    flash(
        "Item marked as returned!",
        "success"
    )

    return redirect(
        url_for(
            "item_detail",
            item_id=item_id
        )
    )


# =========================================================
# 14. DELETE ITEM AND ITS IMAGE
# =========================================================

@app.route(
    "/item/<int:item_id>/delete",
    methods=["POST"]
)
def delete_item(item_id):

    with get_db() as conn:

        item = conn.execute(
            "SELECT * FROM items WHERE id=?",
            (item_id,)
        ).fetchone()

        if item is None:

            abort(404)

        conn.execute(
            "DELETE FROM items WHERE id=?",
            (item_id,)
        )

    image_filename = item["image_filename"]

    if image_filename:

        local_path = UPLOAD_DIR / image_filename

        if local_path.is_file():

            local_path.unlink(
                missing_ok=True
            )

        else:

            try:

                s3_client.delete_object(
                    Bucket=S3_BUCKET,
                    Key=image_filename
                )

            except (BotoCoreError, ClientError):

                app.logger.exception(
                    "Could not delete the image from S3"
                )

                flash(
                    "The record was deleted, but its "
                    "S3 image could not be deleted.",
                    "warning"
                )

                return redirect(
                    url_for("index")
                )

    flash(
        "Demo record deleted.",
        "success"
    )

    return redirect(
        url_for("index")
    )


# =========================================================
# 15. DISPLAY LOCAL OR PRIVATE S3 IMAGES
# =========================================================

@app.route("/uploads/<filename>")
def uploaded_file(filename):

    if Path(filename).name != filename:

        abort(404)

    local_path = UPLOAD_DIR / filename

    # Display existing local images.

    if local_path.is_file():

        return send_from_directory(
            UPLOAD_DIR,
            filename
        )

    # Display images from private S3 using a temporary URL.

    try:

        image_url = s3_client.generate_presigned_url(

            "get_object",

            Params={
                "Bucket": S3_BUCKET,
                "Key": filename
            },

            ExpiresIn=3600

        )

        return redirect(image_url)

    except (BotoCoreError, ClientError):

        app.logger.exception(
            "Could not generate a private S3 image link"
        )

        abort(404)


# =========================================================
# 16. FILE SIZE ERROR
# =========================================================

@app.errorhandler(413)
def too_large(error):

    flash(
        "Image is too large. Maximum size is 5 MB.",
        "error"
    )

    return redirect(
        url_for("report")
    )


# =========================================================
# 17. INITIALIZE DATABASE
# =========================================================

# Safely adds latitude and longitude columns to old databases.
# Existing item records are preserved.

init_db()


# =========================================================
# 18. RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    app.run(debug=True)
