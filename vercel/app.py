"""Vercel gateway for PhotoGrid's persistent Python analysis server."""

import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from flask import Flask, Response, abort, jsonify, redirect, request
from werkzeug.middleware.proxy_fix import ProxyFix


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def create_app():
    app = Flask(__name__, static_folder=None)
    hosts = ["localhost", "127.0.0.1"]
    for variable in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        hostname = os.environ.get(variable, "").strip()
        if hostname and "/" not in hostname and ":" not in hostname:
            hosts.append(hostname)
    public_url = os.environ.get("PHOTOGRID_PUBLIC_URL", "").strip()
    if public_url:
        parsed = urlsplit(public_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("PHOTOGRID_PUBLIC_URL must be an HTTPS origin.")
        hosts.append(parsed.netloc)
    if os.environ.get("VERCEL") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)
    app.config.update(TRUSTED_HOSTS=hosts, MAX_CONTENT_LENGTH=16384)
    backend = os.environ.get("PHOTOGRID_BACKEND_URL", "").strip().rstrip("/")
    if backend:
        parsed = urlsplit(backend)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("PHOTOGRID_BACKEND_URL must be an HTTPS origin without credentials or a path.")
        if parsed.netloc in hosts:
            raise ValueError("The backend must be a separate Python service, not this Vercel gateway.")
    opener = build_opener(NoRedirect())

    @app.before_request
    def check_origin():
        if request.method == "POST" and request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403)

    @app.route("/", defaults={"path": ""}, methods=["GET", "HEAD", "POST"])
    @app.route("/<path:path>", methods=["GET", "HEAD", "POST"])
    def gateway(path):
        if not backend:
            return jsonify(error="PhotoGrid hosting setup is incomplete. Set PHOTOGRID_BACKEND_URL to your deployed Python service URL in Vercel, then redeploy."), 503
        if path not in ("", "photogrid", "health") and not path.startswith(("api/", "results/", "static/")):
            abort(404)
        url = backend + "/" + quote(path, safe="/")
        if request.query_string:
            url += "?" + request.query_string.decode("ascii")
        # Raw rasters can exceed Vercel's response limit; download them directly.
        if request.method in ("GET", "HEAD") and path.startswith("results/") and path.endswith(".tif"):
            return redirect(url, code=307)
        headers = {name: request.headers[name] for name in ("Content-Type", "Accept", "Range", "If-None-Match", "If-Modified-Since") if name in request.headers}
        if request.method == "POST":
            # The browser origin was checked above. The server sees this gateway
            # as a same-origin client; arbitrary forwarded headers never reach it.
            headers["Origin"] = backend
        upstream = Request(url, data=request.get_data() if request.method == "POST" else None, headers=headers, method=request.method)
        try:
            try:
                response = opener.open(upstream, timeout=270)
            except HTTPError as exc:
                response = exc  # Preserve useful backend validation errors.
            with response:
                # Leave room below Vercel's 4.5 MB response limit.
                data = response.read(4_000_001)
                if len(data) > 4_000_000:
                    return jsonify(error="This result is too large for the gateway. Open the Python service directly to download it."), 502
                forwarded = {name: response.headers[name] for name in ("Content-Type", "Content-Disposition", "ETag", "Last-Modified", "Content-Range", "Accept-Ranges") if name in response.headers}
                # Shared analyses must not be cached by the CDN.
                forwarded["Cache-Control"] = "no-store"
                return Response(data, status=response.code, headers=forwarded)
        except (URLError, TimeoutError, OSError):
            return jsonify(error="PhotoGrid's analysis server is unavailable or waking up. Try again shortly."), 502

    return app


app = create_app()
