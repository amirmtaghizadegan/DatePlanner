from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from database import init_db, get_connection
from auth import hash_password, verify_password
from config import SITE_TITLE, SECRET_KEY

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY
)

init_db()

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(
    directory="templates"
)

UPLOAD_DIR = Path("static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DAY_CHOICES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday"
]

TIME_CHOICES = [
    "Morning",
    "Noon",
    "Afternoon",
    "Night"
]

TIME_ENDS = {
    "Morning": time(12, 0),
    "Noon": time(14, 0),
    "Afternoon": time(18, 0),
    "Night": time(23, 59)
}


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_or_create_option(cur, table_name, title):
    if table_name not in {"activities", "locations"}:
        raise ValueError("Unsupported option table")

    cleaned_title = title.strip()

    if not cleaned_title:
        return None

    existing = cur.execute(
        f"""
        SELECT id, title
        FROM {table_name}
        WHERE lower(title) = lower(?)
        """,
        (cleaned_title,)
    ).fetchone()

    if existing is not None:
        cur.execute(
            f"""
            UPDATE {table_name}
            SET active = 1
            WHERE id = ?
            """,
            (existing["id"],)
        )

        return existing

    cur.execute(
        f"""
        INSERT INTO {table_name}
        (
            title
        )
        VALUES (?)
        """,
        (cleaned_title,)
    )

    return cur.execute(
        f"""
        SELECT id, title
        FROM {table_name}
        WHERE id = ?
        """,
        (cur.lastrowid,)
    ).fetchone()


async def save_upload(upload):
    if upload is None or not upload.filename:
        return ""

    extension = Path(upload.filename).suffix.lower()

    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ""

    filename = f"{uuid4().hex}{extension}"
    path = UPLOAD_DIR / filename
    content = await upload.read()

    if not content:
        return ""

    path.write_bytes(content)

    return f"/static/uploads/{filename}"


async def set_option_image(cur, table_name, option_id, upload):
    if table_name not in {"activities", "locations"}:
        raise ValueError("Unsupported option table")

    image_path = await save_upload(upload)

    if not image_path:
        return

    cur.execute(
        f"""
        UPDATE {table_name}
        SET image_path = ?
        WHERE id = ?
        """,
        (
            image_path,
            option_id
        )
    )


def compute_expires_at(day_choice, time_choice):
    if day_choice not in DAY_CHOICES or time_choice not in TIME_ENDS:
        return None

    now = datetime.now()
    target_weekday = DAY_CHOICES.index(day_choice)
    days_until = (target_weekday - now.weekday()) % 7
    target_date = now.date() + timedelta(days=days_until)
    expires_at = datetime.combine(target_date, TIME_ENDS[time_choice])

    if expires_at <= now:
        expires_at = expires_at + timedelta(days=7)

    return expires_at.strftime("%Y-%m-%d %H:%M:%S")


def cleanup_expired_submissions():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE submissions
        SET status = 'expired'
        WHERE status = 'active'
            AND expires_at IS NOT NULL
            AND expires_at <= CURRENT_TIMESTAMP
        """
    )

    conn.commit()
    conn.close()


def bootstrap_admin():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE role = ?
        """,
        ("admin",)
    )

    admin = cur.fetchone()

    if admin is None:
        cur.execute(
            """
            INSERT INTO users
            (
                username,
                password,
                role
            )
            VALUES (?, ?, ?)
            """,
            (
                "admin",
                hash_password("change_me_now"),
                "admin"
            )
        )

        print("\nAdmin account created")
        print("Username: admin")
        print("Password: change_me_now\n")

    conn.commit()
    conn.close()


bootstrap_admin()


@app.get("/")
async def home(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": SITE_TITLE
        }
    )


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE username = ?
        """,
        (username,)
    )

    user = cur.fetchone()

    conn.close()

    if user is None:
        return RedirectResponse(
            url="/",
            status_code=302
        )

    if not verify_password(
        password,
        user["password"]
    ):
        return RedirectResponse(
            url="/",
            status_code=302
        )

    request.session["username"] = user["username"]
    request.session["role"] = user["role"]
    
    print("LOGIN USER:", user["username"])
    print("ROLE:", user["role"])
    
    if user["role"] == "admin":
        return RedirectResponse(
            url="/admin",
            status_code=302
        )

    return RedirectResponse(
        url="/dashboard",
        status_code=302
    )


@app.get("/logout")
async def logout(request: Request):

    request.session.clear()

    return RedirectResponse(
        url="/",
        status_code=302
    )


@app.get("/dashboard")
async def dashboard(request: Request):

    if "username" not in request.session:
        return RedirectResponse(
            url="/",
            status_code=302
        )

    cleanup_expired_submissions()

    conn = get_connection()
    cur = conn.cursor()

    activities = cur.execute(
        """
        SELECT
            activities.*
        FROM activities
        WHERE activities.active = 1
        ORDER BY activities.title
        """
    ).fetchall()

    locations = cur.execute(
        """
        SELECT *
        FROM locations
        WHERE active = 1
        ORDER BY title
        """
    ).fetchall()

    user_submissions = cur.execute(
        """
        SELECT
            submissions.*,
            activities.title AS selected_activity,
            locations.title AS selected_location
        FROM submissions
        LEFT JOIN activities
            ON activities.id = submissions.activity_id
        LEFT JOIN locations
            ON locations.id = submissions.location_id
        WHERE submissions.username = ?
            AND COALESCE(submissions.status, 'active') = 'active'
        ORDER BY submissions.id DESC
        """,
        (request.session["username"],)
    ).fetchall()

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "title": SITE_TITLE,
            "username": request.session["username"],
            "days": DAY_CHOICES,
            "times": TIME_CHOICES,
            "activities": activities,
            "locations": locations,
            "submissions": user_submissions
        }
    )


@app.post("/submit")
async def submit(
    request: Request,
    day_choice: str = Form(""),
    time_choice: str = Form(""),
    activity_id: str = Form(""),
    location_id: str = Form(""),
    custom_day_time: str = Form(""),
    custom_activity: str = Form(""),
    custom_location: str = Form("")
):

    if "username" not in request.session:
        return RedirectResponse(
            url="/",
            status_code=302
        )

    conn = get_connection()
    cur = conn.cursor()

    selected_activity_id = to_int(activity_id)
    selected_location_id = to_int(location_id)
    selected_day_choice = day_choice.strip()
    selected_time_choice = time_choice.strip()
    selected_date_choice = " ".join(
        value for value in [selected_day_choice, selected_time_choice] if value
    )
    expires_at = compute_expires_at(
        selected_day_choice,
        selected_time_choice
    )
    activity_choice = ""
    location_choice = ""

    if selected_activity_id is not None:
        activity = cur.execute(
            """
            SELECT
                title AS activity_title
            FROM activities
            WHERE activities.id = ?
            """,
            (selected_activity_id,)
        ).fetchone()

        if activity is not None:
            activity_choice = activity["activity_title"]

    if selected_location_id is not None:
        location = cur.execute(
            """
            SELECT title AS location_title
            FROM locations
            WHERE id = ?
            """,
            (selected_location_id,)
        ).fetchone()

        if location is not None:
            location_choice = location["location_title"]

    suggested_activity = get_or_create_option(
        cur,
        "activities",
        custom_activity
    )

    if suggested_activity is not None and not activity_choice:
        selected_activity_id = suggested_activity["id"]
        activity_choice = suggested_activity["title"]

    suggested_location = get_or_create_option(
        cur,
        "locations",
        custom_location
    )

    if suggested_location is not None and not location_choice:
        selected_location_id = suggested_location["id"]
        location_choice = suggested_location["title"]

    cur.execute(
        """
        INSERT INTO submissions
        (
            username,
            date_id,
            activity_id,
            location_id,
            day_choice,
            time_choice,
            date_choice,
            activity_choice,
            location_choice,
            custom_date,
            custom_activity,
            custom_location,
            expires_at,
            status
        )
        VALUES
        (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            request.session["username"],
            None,
            selected_activity_id,
            selected_location_id,
            selected_day_choice,
            selected_time_choice,
            selected_date_choice,
            activity_choice,
            location_choice,
            custom_day_time,
            custom_activity,
            custom_location,
            expires_at,
            "active"
        )
    )

    conn.commit()
    conn.close()

    redirect_url = "/admin" if request.session.get("role") == "admin" else "/dashboard"

    return RedirectResponse(
        url=redirect_url,
        status_code=302
    )


@app.post("/submission/delete")
async def delete_submission(
    request: Request,
    submission_id: str = Form(...)
):

    if "username" not in request.session:
        return RedirectResponse(
            url="/",
            status_code=302
        )

    selected_submission_id = to_int(submission_id)

    if selected_submission_id is not None:
        conn = get_connection()
        cur = conn.cursor()

        if request.session.get("role") == "admin":
            cur.execute(
                """
                DELETE FROM submissions
                WHERE id = ?
                """,
                (selected_submission_id,)
            )
        else:
            cur.execute(
                """
                DELETE FROM submissions
                WHERE id = ?
                    AND username = ?
                """,
                (
                    selected_submission_id,
                    request.session["username"]
                )
            )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/dashboard",
        status_code=302
    )


@app.get("/admin")
async def admin(request: Request):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    cleanup_expired_submissions()

    conn = get_connection()
    cur = conn.cursor()

    users = cur.execute(
        """
        SELECT
            users.*,
            COUNT(submissions.id) AS submission_count,
            MAX(submissions.created_at) AS last_submission_at
        FROM users
        LEFT JOIN submissions
            ON submissions.username = users.username
        GROUP BY users.id
        ORDER BY users.role, users.username
        """
    ).fetchall()

    submissions = cur.execute(
        """
        SELECT
            submissions.*,
            dates.title AS selected_date,
            activities.title AS selected_activity,
            locations.title AS selected_location
        FROM submissions
        LEFT JOIN dates
            ON dates.id = submissions.date_id
        LEFT JOIN activities
            ON activities.id = submissions.activity_id
        LEFT JOIN locations
            ON locations.id = submissions.location_id
        ORDER BY submissions.id DESC
        """
    ).fetchall()

    date_time_choices = cur.execute(
        """
        SELECT
            COALESCE(
                NULLIF(TRIM(
                    COALESCE(submissions.day_choice, '') || ' ' || COALESCE(submissions.time_choice, '')
                ), ''),
                submissions.date_choice,
                submissions.custom_date
            ) AS title,
            COUNT(submissions.id) AS pick_count
        FROM submissions
        WHERE COALESCE(
            NULLIF(TRIM(
                COALESCE(submissions.day_choice, '') || ' ' || COALESCE(submissions.time_choice, '')
            ), ''),
            submissions.date_choice,
            submissions.custom_date,
            ''
        ) != ''
        GROUP BY COALESCE(
            NULLIF(TRIM(
                COALESCE(submissions.day_choice, '') || ' ' || COALESCE(submissions.time_choice, '')
            ), ''),
            submissions.date_choice,
            submissions.custom_date
        )
        ORDER BY pick_count DESC, title
        """
    ).fetchall()

    locations = cur.execute(
        """
        SELECT
            locations.*,
            COUNT(submissions.id) AS pick_count
        FROM locations
        LEFT JOIN submissions
            ON submissions.location_id = locations.id
        WHERE locations.active = 1
        GROUP BY locations.id
        ORDER BY locations.title
        """
    ).fetchall()

    archived_locations = cur.execute(
        """
        SELECT *
        FROM locations
        WHERE active = 0
        ORDER BY title
        """
    ).fetchall()

    activities = cur.execute(
        """
        SELECT
            activities.*,
            COUNT(submissions.id) AS pick_count
        FROM activities
        LEFT JOIN submissions
            ON submissions.activity_id = activities.id
        WHERE activities.active = 1
        GROUP BY activities.id
        ORDER BY activities.title
        """
    ).fetchall()

    archived_activities = cur.execute(
        """
        SELECT *
        FROM activities
        WHERE active = 0
        ORDER BY title
        """
    ).fetchall()

    stats = {
        "users": len(users),
        "submissions": len(submissions),
        "activities": len(activities),
        "locations": len(locations)
    }

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "title": SITE_TITLE,
            "users": users,
            "submissions": submissions,
            "date_time_choices": date_time_choices,
            "activities": activities,
            "locations": locations,
            "archived_activities": archived_activities,
            "archived_locations": archived_locations,
            "stats": stats
        }
    )


@app.post("/admin/create-user")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO users
            (
                username,
                password,
                role
            )
            VALUES (?, ?, ?)
            """,
            (
                username,
                hash_password(password),
                "user"
            )
        )

        conn.commit()

    except Exception as e:
        print(e)

    finally:
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/change-password")
async def change_password(
    request: Request,
    user_id: str = Form(...),
    password: str = Form(...)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    selected_user_id = to_int(user_id)
    cleaned_password = password.strip()

    if selected_user_id is not None and cleaned_password:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE users
            SET password = ?
            WHERE id = ?
            """,
            (
                hash_password(cleaned_password),
                selected_user_id
            )
        )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/create-location")
async def create_location(
    request: Request,
    title: str = Form(...),
    image: UploadFile = File(None)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    cleaned_title = title.strip()

    if cleaned_title:
        conn = get_connection()
        cur = conn.cursor()

        location = get_or_create_option(cur, "locations", cleaned_title)
        await set_option_image(
            cur,
            "locations",
            location["id"],
            image
        )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/create-activity")
async def create_activity(
    request: Request,
    title: str = Form(...),
    image: UploadFile = File(None)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    cleaned_title = title.strip()

    if cleaned_title:
        conn = get_connection()
        cur = conn.cursor()

        activity = get_or_create_option(cur, "activities", cleaned_title)
        await set_option_image(
            cur,
            "activities",
            activity["id"],
            image
        )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/update-option-image")
async def update_option_image(
    request: Request,
    option_type: str = Form(...),
    option_id: str = Form(...),
    image: UploadFile = File(None)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    selected_option_id = to_int(option_id)
    table_name = ""

    if option_type == "activity":
        table_name = "activities"
    elif option_type == "location":
        table_name = "locations"

    if table_name and selected_option_id is not None:
        conn = get_connection()
        cur = conn.cursor()

        await set_option_image(
            cur,
            table_name,
            selected_option_id,
            image
        )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/reset-options")
async def reset_options(request: Request):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("UPDATE activities SET active = 0")
    cur.execute("UPDATE locations SET active = 0")

    conn.commit()
    conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/restore-option")
async def restore_option(
    request: Request,
    option_type: str = Form(...),
    option_id: str = Form(...)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    selected_option_id = to_int(option_id)
    table_name = ""

    if option_type == "activity":
        table_name = "activities"
    elif option_type == "location":
        table_name = "locations"

    if table_name and selected_option_id is not None:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            f"""
            UPDATE {table_name}
            SET active = 1
            WHERE id = ?
            """,
            (selected_option_id,)
        )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/delete-location")
async def delete_location(
    request: Request,
    location_id: str = Form(...)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    selected_location_id = to_int(location_id)

    if selected_location_id is not None:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE locations
            SET active = 0
            WHERE id = ?
            """,
            (selected_location_id,)
        )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )


@app.post("/admin/delete-activity")
async def delete_activity(
    request: Request,
    activity_id: str = Form(...)
):

    if request.session.get("role") != "admin":
        return RedirectResponse(
            url="/",
            status_code=302
        )

    selected_activity_id = to_int(activity_id)

    if selected_activity_id is not None:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE activities
            SET active = 0
            WHERE id = ?
            """,
            (selected_activity_id,)
        )

        conn.commit()
        conn.close()

    return RedirectResponse(
        url="/admin",
        status_code=302
    )
