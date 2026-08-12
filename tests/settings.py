INSTALLED_APPS = ["django.contrib.staticfiles", "django_altcha_widget"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3"}}
ROOT_URLCONF = "tests.urls"
STATIC_URL = "/static/"
ALTCHA_HMAC_KEY = "altcha-insecure-hmac-0123456789abcdef"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# No asset settings: the ALTCHA assets are vendored, so these settings are what
# a real install has. Nothing here makes the suite unrepresentative of one.
