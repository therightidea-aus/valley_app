"""
PythonAnywhere WSGI entrypoint for the Valley app.

Copy the body of this file into the PythonAnywhere WSGI configuration file
for the web app, or adapt the project_home value if the repo path changes.
"""

import os
import sys

project_home = "/home/therightidea/valley_app"

if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
