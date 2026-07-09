bind = "0.0.0.0:5000"
workers = 4 # (CPU * 2 + 1)
worker_class = "gevent"
timeout = 30
loglevel = "info"
accesslog = "-"
errorlog = "-"
