# Valley Community Church App

Mobile-first Django PWA for church responsibilities, rosters, Sunday plans, calendar events, sermons, and in-app notifications.

## Local Setup

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver
```

Open `http://127.0.0.1:8000` and sign in with:

- Email: `roger@example.com`
- Password: `valley-demo`

## Visual Direction

The MVP uses the selected Charcoal Focus direction from the Valley brand guide:

- Charcoal `#16191b` as the primary identity surface
- Light grey `#f2f3f3` for calm page backgrounds
- Blue `#bfd9d6` for soft support panels
- Red `#a64f42` for primary actions and status accents
- Poppins-first digital typography

## Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Then run migrations and seed data in the web container:

```powershell
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo
```

## GitHub

This project is prepared for:

```text
https://github.com/therightidea-aus/valley_app
```

Local data and generated files are intentionally ignored:

- `.env`
- `db.sqlite3`
- `staticfiles/`
- `__pycache__/`
- virtual environments

Initialize and push from the project folder:

```powershell
git init
git branch -M main
git remote add origin https://github.com/therightidea-aus/valley_app.git
git add .
git commit -m "Initial Valley app"
git push -u origin main
```

## PythonAnywhere Deployment

Target project path:

```text
/home/therightidea/valley_app
```

On PythonAnywhere, open a Bash console and clone the repo:

```bash
cd /home/therightidea
git clone https://github.com/therightidea-aus/valley_app.git
cd valley_app
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp deployment/pythonanywhere.env.example .env
```

Edit `.env` with the production secret key, PythonAnywhere hostname, and the Postgres `DATABASE_URL` from the PythonAnywhere Databases tab.

Then run:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

In the PythonAnywhere Web tab:

- Set source code to `/home/therightidea/valley_app`
- Set working directory to `/home/therightidea/valley_app`
- Set virtualenv to `/home/therightidea/valley_app/.venv`
- Set static URL `/static/` to `/home/therightidea/valley_app/staticfiles`
- Replace the WSGI file contents with `deployment/pythonanywhere_wsgi.py`

Reload the web app after each deployment:

```bash
cd /home/therightidea/valley_app
source .venv/bin/activate
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

## Sync Jobs

Run these manually from a PythonAnywhere Bash console, or add them as scheduled tasks:

```bash
cd /home/therightidea/valley_app
source .venv/bin/activate
python manage.py sync_calendar
python manage.py sync_sermons
```

## Push Notifications

Push notifications require HTTPS, browser permission from each user, and VAPID keys configured in `.env`.

Generate keys on PythonAnywhere:

```bash
cd /home/therightidea/valley_app
source .venv/bin/activate
python manage.py generate_vapid_keys --private-key-path /home/therightidea/valley_app/.vapid_private_key.pem
```

Copy the printed `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY_PATH` values into `.env`, then reload the web app.

Users can then enable push notifications from the More tab. When a user is added to a Sunday roster, the app creates the normal in-app notification and also attempts to send a push notification to that user's enabled browser/device subscriptions.

## Email

Account approval emails use Django's SMTP email settings from `.env`.

For Google Workspace SMTP with an app password:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=your-google-workspace-address@valleychurch.com.au
EMAIL_HOST_PASSWORD=your-google-app-password-or-smtp-password
DEFAULT_FROM_EMAIL=Valley Community Church <your-google-workspace-address@valleychurch.com.au>
```

Test email from PythonAnywhere:

```bash
cd /home/therightidea/valley_app
source .venv/bin/activate
python manage.py shell -c "from django.core.mail import send_mail; send_mail('Valley test email', 'Email is working.', None, ['your-address@example.com'])"
```

## Notes

Google Calendar and Spotify sermon syncing use public feed/scrape endpoints and cache results in the database.
