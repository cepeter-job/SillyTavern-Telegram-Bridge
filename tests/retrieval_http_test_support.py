"""Loopback wire fixtures for the separately opted-in comparison adapters."""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def wire_server(respond):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size) if size else b""
            call = {
                "method": self.command,
                "path": self.path,
                "payload": json.loads(raw) if raw else None,
                "headers": dict(self.headers),
            }
            calls.append(call)
            status, data, options = respond(call)
            if options.get("disconnect"):
                self.close_connection = True
                return
            body = json.dumps(data).encode()
            try:
                time.sleep(options.get("header_delay", 0))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if options.get("redirect"):
                    self.send_header("Location", options["redirect"])
                self.end_headers()
                width = options.get("chunk_size", len(body))
                for start in range(0, len(body), width):
                    self.wfile.write(body[start : start + width])
                    self.wfile.flush()
                    time.sleep(options.get("chunk_delay", 0))
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = do_POST = do_PUT = do_DELETE = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class OwnedBankServer:
    def __init__(self, *, fail_retain=False, fail_create=False, existing=False, config_disabled=True):
        self.exists = existing
        self.fail_retain = fail_retain
        self.fail_create = fail_create
        self.config_disabled = config_disabled
        self.documents = {}

    def __call__(self, call):
        bank_id = call["path"].split("/banks/")[-1].split("/")[0]
        method, path, body = call["method"], call["path"], call["payload"]
        if method == "GET" and path.endswith("/config"):
            return (
                (
                    200,
                    {
                        "bank_id": bank_id,
                        "config": {"enable_observations": not self.config_disabled},
                        "overrides": {},
                        "PRIVATE": "DO NOT SERIALIZE",
                    },
                    {},
                )
                if self.exists
                else (404, {}, {})
            )
        if method == "PUT":
            self.exists = True
            return 200, {}, {"disconnect": self.fail_create}
        if method == "DELETE":
            self.exists = False
            return 200, {}, {}
        if method == "POST" and path.endswith("/memories"):
            self.documents.update({item["document_id"]: item for item in body["items"]})
            return (
                200,
                {"success": True, "bank_id": bank_id, "items_count": len(body["items"]), "async": False},
                {"disconnect": self.fail_retain},
            )
        if method == "POST" and path.endswith("/recall"):
            results = [
                {
                    "id": "remote-" + str(index),
                    "document_id": document,
                    "type": "world",
                    "tags": item["tags"],
                    "text": "UNTRUSTED REMOTE ENRICHMENT",
                }
                for index, (document, item) in enumerate(self.documents.items())
                if set(body["tags"]) <= set(item["tags"])
            ]
            return 200, {"results": results}, {}
        return 500, {"error": "PRIVATE provider body"}, {}
