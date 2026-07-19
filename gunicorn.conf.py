# Gunicorn Production Configuration for UFOQ
# Usage: gunicorn -c gunicorn.conf.py app:app

import os
import multiprocessing

# Server socket
bind = "0.0.0.0:" + os.getenv("PORT", "5000")

# Worker processes - optimized for Render's free tier and above
workers = int(os.getenv("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2 + 1)))
worker_class = "sync"
worker_connections = 1000

# Threads (for sync worker, threads help with I/O bound tasks)
threads = int(os.getenv("GUNICORN_THREADS", "4"))

# Timeouts
timeout = 120
keepalive = 5
graceful_timeout = 30

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stdout
loglevel = os.getenv("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "ufoq_app"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL
forwarded_allow_ips = "*"
secure_scheme_headers = {
    'X-FORWARDED-PROTOCOL': 'ssl',
    'X-FORWARDED-PROTO': 'https',
    'X-FORWARDED-SSL': 'on'
}

# Preload app for memory efficiency
preload_app = True

# Max requests per worker (prevent memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Restart workers gracefully
worker_tmp_dir = "/dev/shm"

# Print config on startup
def on_starting(server):
    print(f"UFOQ starting with {workers} workers, {threads} threads")
    print(f"Database pool: {os.getenv('DB_POOL_SIZE', '20')}")
