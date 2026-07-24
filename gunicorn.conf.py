import os
import multiprocessing

bind = "0.0.0.0:" + os.getenv("PORT", "5000")
workers = int(os.getenv("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2 + 1)))
worker_class = "sync"
worker_connections = 1000
threads = int(os.getenv("GUNICORN_THREADS", "4"))
timeout = 120
keepalive = 5
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
proc_name = "ufoq_app"
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None
forwarded_allow_ips = "*"
secure_scheme_headers = {
    'X-FORWARDED-PROTOCOL': 'ssl',
    'X-FORWARDED-PROTO': 'https',
    'X-FORWARDED-SSL': 'on'
}
preload_app = True
max_requests = 1000
max_requests_jitter = 50
worker_tmp_dir = "/dev/shm"

def on_starting(server):
    print(f"UFOQ starting with {workers} workers, {threads} threads")
    print(f"Database pool: {os.getenv('DB_POOL_SIZE', '20')}")