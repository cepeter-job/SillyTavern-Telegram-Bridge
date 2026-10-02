#!/usr/bin/env python3
# ruff: noqa
# fmt: off
"""
leakscan - static memory/resource-leak scanner for Python code.

Finds the code shapes that most often turn into leaks at runtime:
unclosed resources, unbounded caches/globals, threads/processes without
cleanup, retained tracebacks, discarded asyncio tasks, and containers that
grow inside unbounded loops.

Stdlib only. Usage:
    python3 leakscan.py PATH [PATH ...] [--format markdown|text|json]
                              [--min-severity low] [--fail-on high]
                              [--exclude GLOB ...] [--list-rules]

Exit code is 0 unless a finding at/above --fail-on exists (then 1).
Silence a finding with an inline comment:  # leakscan: ignore
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import Iterable, Optional

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

DEFAULT_EXCLUDES = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".tox", ".nox", "node_modules",
    "build", "dist", "site-packages", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".eggs", ".ruff_cache", "htmlcov", ".idea", ".vscode", "migrations",
}

MAX_FILE_BYTES = 2_000_000

# ----------------------------------------------------------------------------
# Rule metadata
# ----------------------------------------------------------------------------
RULES = {
    "UNCLOSED_RESOURCE": (
        "high", "Resource acquired without guaranteed release",
        "Use a context manager (`with open(...) as f:`) or release it in a "
        "`finally:` block. CPython emits ResourceWarning at GC time, but "
        "file descriptors / sockets / DB sessions are a *process-wide* limited "
        "resource and the handle also pins its buffers until collected."),
    "DISCARDED_RESOURCE": (
        "medium", "Resource handle created and immediately discarded",
        "The handle is only released if/when GC runs; under load allocation "
        "outpaces GC and you accumulate open fds. Assign and close it."),
    "UNCLOSED_POOL": (
        "medium", "Pool/executor created without shutdown",
        "Wrap in `with Pool() as p:` or call `.shutdown()`/`.close()` + "
        "`.join()` in `finally:`. Worker processes/threads hold their own "
        "heap and never exit on their own."),
    "SUBPROCESS_NOT_REAPED": (
        "medium", "Popen child never waited on / terminated",
        "Call `.communicate()`/`.wait()`, or `.terminate()` in `finally:`. "
        "Unreaped children become zombies and their stdout/stderr pipes stay "
        "open (buffered in memory)."),
    "UNCLOSED_DB_SESSION": (
        "high", "DB/session client created without close",
        "Close the client in `finally:` or keep one long-lived client. "
        "Most drivers hold a connection pool plus per-connection buffers; "
        "per-request clients leak connections until the pool/server rejects you."),
    "THREAD_NEVER_JOINED": (
        "medium", "Non-daemon thread without join or cancel",
        "Set `daemon=True` for fire-and-forget work or `.join()` it in "
        "`finally:`. Each thread keeps its own stack, frame locals and any "
        "objects it references alive for its whole lifetime."),
    "THREAD_IN_LOOP": (
        "high", "Thread/process created per loop iteration",
        "Pool the workers (`ThreadPoolExecutor(max_workers=n)`) and reuse them, "
        "or bound concurrency. One thread per iteration is an unbounded growth "
        "path when the loop is long-lived."),
    "MUTABLE_MODULE_STATE": (
        "medium", "Module-level container mutated at runtime",
        "Process-lifetime global = nothing ever frees it. Use a bounded cache "
        "(`functools.lru_cache(maxsize=...)`, `cachetools.TTLCache`) or add "
        "eviction (cap + evict oldest, TTL, `weakref.WeakValueDictionary`)."),
    "UNBOUNDED_GROWTH_LOOP": (
        "high", "Container grows inside an unbounded loop with no eviction",
        "Add a bound/TTL or flush each iteration. `while True` workers that "
        "append to a long-lived container are the single most common "
        "production leak (slow, steady RSS climb)."),
    "UNBOUNDED_CACHE": (
        "medium", "Unbounded memoization cache",
        "`functools.cache` / `lru_cache(maxsize=None)` never evicts. Use "
        "`maxsize=N` or an LRU/TTL cache; for per-request data this grows with "
        "every distinct argument ever seen."),
    "LRU_CACHE_ON_METHOD": (
        "medium", "lru_cache applied to an instance method",
        "The cache is keyed on `self`, so every instance is retained for the "
        "process lifetime (a hidden registry of *all* objects). Use a module-level "
        "function taking the needed values, or @lru_cache on a staticmethod."),
    "MUTABLE_DEFAULT_ARG": (
        "medium", "Mutable default argument mutated in the body",
        "Defaults are created once at def-time and shared across calls; the "
        "container grows forever. Use `None` + `if x is None: x = []`."),
    "TRACEBACK_RETAINED": (
        "medium", "Exception object stored (keeps traceback/frames alive)",
        "An exception holds `__traceback__`, which holds every frame and its "
        "locals (including `self`, request bodies, big payloads). Store "
        "`str(e)` / `(type(e), e.args)` or null it: `e.__traceback__ = None`."),
    "LOG_HANDLER_LEAK": (
        "low", "Logging handler added without a guard",
        "`addHandler` on each call duplicates handlers: unbounded handler list "
        "+ duplicated log volume. Guard with `if not logger.handlers:` or do it "
        "once at import time."),
    "LISTENER_NEVER_REMOVED": (
        "low", "Listener/callback registered with no matching removal",
        "Publishers hold a strong ref to every subscriber and everything the "
        "closure captures. Remove in teardown (`disconnect`/`unsubscribe`/"
        "`remove_listener`) or subscribe with a weak method ref."),
    "QUEUE_UNBOUNDED": (
        "low", "Unbounded queue",
        "`Queue()` has no maxsize: if the producer outruns the consumer the "
        "queue *is* the leak. Pass `maxsize=` and handle backpressure."),
    "TASK_REF_DROPPED": (
        "medium", "asyncio task created without keeping a reference",
        "The docs are explicit: keep a reference or the task can be garbage "
        "collected mid-execution and its exception is never retrieved. Store "
        "tasks in a set and discard on completion."),
    "TEMP_FILE_LEAK": (
        "low", "Temp file that is not auto-deleted",
        "`delete=False` / `mkstemp()` require manual cleanup; the fd stays "
        "open if `os.close` is missed. Prefer the default (auto-delete) or "
        "`tempfile.TemporaryDirectory()`."),
    "DATAFRAME_REBUILD_IN_LOOP": (
        "low", "Data structure rebuilt inside a loop",
        "`concat`/`append`/`hstack` per iteration is O(n^2) in memory traffic "
        "and churns large temporaries. Collect parts in a list, build once."),
    "GC_DISABLED": (
        "medium", "gc.disable() called",
        "Cycle collection off means reference cycles are never reclaimed. If "
        "you need it for latency, re-enable promptly or use `gc.freeze()` after "
        "fork/setup instead."),
    "FINALIZER_DEL": (
        "info", "__del__ finalizer defined",
        "Objects with `__del__` in cycles are collected since PEP 442 but "
        "resurrection/finalizer ordering is fragile and exceptions inside "
        "`__del__` are swallowed. Prefer context managers or "
        "`weakref.finalize`."),
    "ATEXIT_IN_FUNCTION": (
        "low", "atexit/signal handler registered inside a function",
        "Registered once per call; handlers are never removed and close over "
        "whatever they reference. Register at module import time or once in main()."),
    "SYNTAX_ERROR": ("info", "File could not be parsed", "Scanner skipped it."),
}


@dataclass
class Finding:
    rule: str
    severity: str
    path: str
    line: int
    col: int
    message: str
    suggestion: str
    snippet: str = ""

    def as_dict(self):
        d = asdict(self)
        d["title"] = RULES.get(self.rule, ("", "", ""))[1]
        return d


# ----------------------------------------------------------------------------
# AST helpers
# ----------------------------------------------------------------------------
def dotted(node: Optional[ast.AST]) -> str:
    """Best-effort dotted source name: Name/Attribute/Call-callee -> 'a.b.c'."""
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func)
    if isinstance(node, ast.Subscript):
        return dotted(node.value)
    return ""


def tail(name: str) -> str:
    return name.rsplit(".", 1)[-1] if name else ""


def is_true(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def simple_slice(text: str, width: int = 160) -> str:
    text = text.strip()
    return text if len(text) <= width else text[: width - 1] + "\u2026"


class Context:
    """Per-file analysis index."""

    def __init__(self, path: str, source: str, tree: ast.AST):
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self.tree = tree
        self.parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node

        # indexes built by walking
        self.module_containers: dict[str, int] = {}     # name -> lineno
        self.mutated: set[str] = set()                  # names mutated anywhere
        self.evicted: set[str] = set()                  # names evicted anywhere
        self.cleaned: dict[int, set[str]] = {}          # func id -> receivers closed
        self.cleanup_methods_present: set[str] = set()  # any '<recv>.<m>()' seen
        self.returned: dict[int, set[str]] = {}
        self.self_attr_mutated: dict[int, set[str]] = {}  # class id -> attrs
        self.self_attr_evicted: set[str] = set()          # 'self.x' names evicted
        self.connect_calls: list[ast.Call] = []
        self.disconnect_present = False
        self.has_put = False
        self.has_os_close = False
        self._index()

    # ---- helpers -----------------------------------------------------------
    def line_of(self, node: ast.AST) -> int:
        return getattr(node, "lineno", 0)

    def snippet(self, node: ast.AST) -> str:
        ln = self.line_of(node)
        if 1 <= ln <= len(self.lines):
            return simple_slice(self.lines[ln - 1])
        return ""

    def ignored(self, node: ast.AST) -> bool:
        ln = self.line_of(node)
        for candidate in (ln, ln - 1):
            if 1 <= candidate <= len(self.lines):
                if "leakscan: ignore" in self.lines[candidate - 1]:
                    return True
        return False

    def ancestors(self, node: ast.AST) -> Iterable[ast.AST]:
        cur = node
        while cur is not None and id(cur) in self.parents:
            cur = self.parents[id(cur)]
            yield cur

    def enclosing_func(self, node: ast.AST) -> Optional[ast.AST]:
        for anc in self.ancestors(node):
            if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return anc
        return None

    def enclosing_class(self, node: ast.AST) -> Optional[ast.ClassDef]:
        for anc in self.ancestors(node):
            if isinstance(anc, ast.ClassDef):
                return anc
        return None

    def in_with_item(self, node: ast.AST) -> bool:
        return any(isinstance(a, ast.withitem) for a in self.ancestors(node))

    def in_with_block(self, node: ast.AST) -> bool:
        return any(isinstance(a, ast.With) for a in self.ancestors(node))

    def in_loop(self, node: ast.AST) -> bool:
        return any(isinstance(a, (ast.For, ast.AsyncFor, ast.While, ast.comprehension))
                   for a in self.ancestors(node))

    def in_unbounded_loop(self, node: ast.AST) -> bool:
        for anc in self.ancestors(node):
            if isinstance(anc, ast.While):
                if is_true(anc.test) or (isinstance(anc.test, ast.Constant) and anc.test.value == 1):
                    return True
            if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)) and anc is not self.enclosing_func(node):
                break
        return False

    # ---- indexing ----------------------------------------------------------
    def _index(self) -> None:
        for node in ast.walk(self.tree):
            # module-level container assignments
            if isinstance(node, ast.Assign) and not self.enclosing_func(node):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and self._container_like(node.value):
                        self.module_containers[tgt.id] = node.lineno
            if isinstance(node, ast.AnnAssign) and not self.enclosing_func(node):
                if isinstance(node.target, ast.Name) and self._container_like(node.value):
                    self.module_containers[node.target.id] = node.lineno

            # mutations / evictions
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                recv = dotted(node.func.value)
                meth = node.func.attr
                if meth in {"append", "extend", "add", "update", "setdefault",
                            "insert", "put", "appendleft", "add_reader", "iadd"}:
                    self._note_mutation(recv, node)
                if meth in {"pop", "popitem", "popleft", "clear", "discard", "remove",
                            "rotate", "truncate", "shutdown", "cancel"}:
                    self._note_eviction(recv)
                if meth in CLEANUP_METHODS:
                    self.cleanup_methods_present.add(meth)
                    func = self.enclosing_func(node)
                    if func is not None and recv:
                        self.cleaned.setdefault(id(func), set()).add(recv)
                        # os.close(fd) / os.fdopen(fd) -> the *argument* is what gets released
                        if recv == "os" and node.args and isinstance(node.args[0], ast.Name):
                            self.cleaned.setdefault(id(func), set()).add(node.args[0].id)
                if meth in {"put", "put_nowait"}:
                    self.has_put = True

            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                recv = dotted(node.value)
                self._note_mutation(recv, node)
            if isinstance(node, ast.AugAssign):
                recv = dotted(node.target)
                self._note_mutation(recv, node)
            if isinstance(node, ast.Delete):
                for t in node.targets:
                    self._note_eviction(dotted(t))

            # returned names
            if isinstance(node, ast.Return) and node.value is not None:
                func = self.enclosing_func(node)
                if func is not None:
                    self.returned.setdefault(id(func), set()).add(dotted(node.value))

            # listener registration
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"connect", "subscribe", "add_listener", "addEventListener",
                                      "on", "add_callback", "register", "attach"}:
                    self.connect_calls.append(node)
                if node.func.attr in {"disconnect", "unsubscribe", "remove_listener",
                                      "removeEventListener", "off", "remove_callback",
                                      "deregister", "remove_handler"}:
                    self.disconnect_present = True

            if isinstance(node, ast.Call) and dotted(node.func) == "os.close":
                self.has_os_close = True

    def _note_mutation(self, recv: str, node: ast.AST) -> None:
        if not recv:
            return
        base = recv.split(".")[0]
        if "." in recv:                      # self.x / obj.attr
            self.mutated.add(recv)
            parts = recv.split(".")
            if parts[0] in {"self", "cls"} and len(parts) >= 2:
                cls = self.enclosing_class(node)
                if cls is not None:
                    self.self_attr_mutated.setdefault(id(cls), set()).add(recv)
        else:
            self.mutated.add(base)

    def _note_eviction(self, recv: str) -> None:
        if not recv:
            return
        self.evicted.add(recv)
        self.evicted.add(recv.split(".")[0])
        if recv.startswith(("self.", "cls.")):
            self.self_attr_evicted.add(recv)

    @staticmethod
    def _container_like(node: Optional[ast.AST]) -> bool:
        if node is None:
            return False
        if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
            return True
        if isinstance(node, ast.Call):
            n = tail(dotted(node.func))
            return n in {"list", "dict", "set", "defaultdict", "Counter", "OrderedDict",
                         "deque", "Lock", "RLock", "Semaphore", "Queue"}
        return False

    def bounded(self, name: str) -> bool:
        """Heuristic: does this container have any eviction/bounding anywhere?"""
        base = name.split(".")[0]
        if base in self.evicted or name in self.evicted:
            return True
        return False


CLEANUP_METHODS = {
    "close", "aclose", "__exit__", "__aexit__", "release", "disconnect", "shutdown",
    "terminate", "kill", "wait", "communicate", "join", "unlink", "cancel", "logout",
    "quit", "stop", "flush", "rollback", "commit", "stop_worker", "release_conn",
    # os.fdopen(descriptor, ...) takes ownership: the returned file object closes it.
    "fdopen",
}

RESOURCE_CTORS = {
    # files / streams
    "open", "io.open", "codecs.open", "os.fdopen", "os.popen", "os.fdopen",
    "tempfile.NamedTemporaryFile", "tempfile.TemporaryFile", "tempfile.SpooledTemporaryFile",
    # archives / compression
    "zipfile.ZipFile", "tarfile.open", "gzip.open", "bz2.open", "lzma.open",
    # sockets / net
    "socket.socket", "socket.create_connection", "socket.socketpair", "ssl.wrap_socket",
    "urllib.request.urlopen", "urlopen", "http.client.HTTPConnection",
    "http.client.HTTPSConnection", "smtplib.SMTP", "ftplib.FTP", "telnetlib.Telnet",
    "mmap.mmap", "select.epoll", "select.poll", "select.kqueue",
    # DB / session clients
    "sqlite3.connect", "pymongo.MongoClient", "redis.Redis", "redis.StrictRedis",
    "boto3.client", "boto3.resource", "psycopg2.connect", "pymysql.connect",
    "mysql.connector.connect", "sqlalchemy.create_engine",
    # logging
    "logging.FileHandler", "logging.handlers.RotatingFileHandler",
    "logging.handlers.TimedRotatingFileHandler",
}

# tails safe to match loosely (only where the tail is unambiguous)
RESOURCE_TAILS = {
    "open", "urlopen", "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile",
    "ZipFile", "FileHandler", "MongoClient",
}

DB_TAILS = {"connect", "MongoClient", "Redis", "StrictRedis", "create_engine"}

POOL_CTORS = {"Pool", "ThreadPool", "ProcessPoolExecutor", "ThreadPoolExecutor",
              "multiprocessing.Pool", "concurrent.futures.ProcessPoolExecutor",
              "concurrent.futures.ThreadPoolExecutor", "Manager", "ProcessPoolExecutor"}
PROCESS_CTORS = {"Process", "multiprocessing.Process", "dummy.Process", "multiprocessing.dummy.Process"}
THREAD_CTORS = {"Thread", "threading.Thread", "Timer", "threading.Timer"}
QUEUE_CTORS = {"Queue", "LifoQueue", "PriorityQueue", "asyncio.Queue", "SimpleQueue"}

CACHE_DECORATORS = {"cache", "lru_cache", "cached", "memoize", "memoize_method", "functools.cache", "functools.lru_cache"}


# ----------------------------------------------------------------------------
# Scanner
# ----------------------------------------------------------------------------
class Scanner:
    def __init__(self, min_severity: str = "info"):
        self.min_severity = min_severity
        self.findings: list[Finding] = []

    # -- plumbing -----------------------------------------------------------
    def add(self, ctx: Context, node: ast.AST, rule: str, message: str, suggestion: Optional[str] = None,
            severity: Optional[str] = None) -> None:
        if ctx.ignored(node):
            return
        meta = RULES.get(rule, ("info", rule, ""))
        sev = severity or meta[0]
        if SEV_RANK[sev] > SEV_RANK[self.min_severity]:
            return
        self.findings.append(Finding(
            rule=rule, severity=sev, path=ctx.path, line=ctx.line_of(node),
            col=getattr(node, "col_offset", 0), message=message,
            suggestion=suggestion or meta[2], snippet=ctx.snippet(node)))

    def scan_path(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                source = fh.read(MAX_FILE_BYTES)
        except OSError as exc:
            print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
            return
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            f = Finding("SYNTAX_ERROR", "info", path, exc.lineno or 0, 0,
                        f"SyntaxError: {exc.msg}", RULES["SYNTAX_ERROR"][2], "")
            if SEV_RANK["info"] <= SEV_RANK[self.min_severity]:
                self.findings.append(f)
            return
        ctx = Context(path, source, tree)
        self.run_rules(ctx)

    # -- rules --------------------------------------------------------------
    def run_rules(self, ctx: Context) -> None:
        for node in ast.walk(ctx.tree):
            if isinstance(node, ast.Call):
                self.rule_resources(ctx, node)
                self.rule_pools(ctx, node)
                self.rule_processes(ctx, node)
                self.rule_threads(ctx, node)
                self.rule_queues(ctx, node)
                self.rule_tempfiles(ctx, node)
                self.rule_logging(ctx, node)
                self.rule_listeners(ctx, node)
                self.rule_tasks(ctx, node)
                self.rule_datastruct_loop(ctx, node)
                self.rule_gc(ctx, node)
                self.rule_atexit(ctx, node)
                self.rule_growth_loop(ctx, node)
            elif isinstance(node, (ast.Subscript, ast.AugAssign)):
                self.rule_growth_loop(ctx, node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.rule_mutable_default(ctx, node)
                self.rule_cache_decorator(ctx, node)
            if isinstance(node, ast.ClassDef):
                self.rule_del_method(ctx, node)
            if isinstance(node, ast.Assign):
                self.rule_module_container(ctx, node)
            if isinstance(node, ast.ExceptHandler) and node.name:
                self.rule_traceback(ctx, node)

    # ---- resources ---------------------------------------------------------
    def rule_resources(self, ctx: Context, node: ast.Call) -> None:
        name = dotted(node.func)
        t = tail(name)
        if name not in RESOURCE_CTORS and t not in RESOURCE_TAILS:
            return
        if t in {"connect", "create_engine"} and not _looks_like_db(name):
            return
        if t in RESOURCE_TAILS and not _qualifier_ok(name, t):
            return  # e.g. flask's `client.open()` is not a file handle
        if ctx.in_with_item(node):
            return
        parent = ctx.parents.get(id(node))

        # x = open(...)
        target_name = None
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
            target_name = parent.targets[0].id
        if target_name is None and isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
            target_name = parent.target.id

        is_db = t in DB_TAILS or "session" in name.lower() or "engine" in name.lower()
        rule = "UNCLOSED_DB_SESSION" if is_db else "UNCLOSED_RESOURCE"
        # sqlite3.connect(":memory:") holds no fd and no pool — it is heap-only and dies
        # with its last reference, so it must not rank as a handle leak.
        if any(isinstance(a, ast.Constant) and isinstance(a.value, str) and ":memory:" in a.value
               for a in node.args):
            is_db = False if t == "connect" else is_db

        if target_name:
            func = ctx.enclosing_func(node)
            closed = ctx.cleaned.get(id(func), set()) if func else set()
            if target_name in closed or any(c.endswith(target_name) for c in closed):
                return
            if func is not None and target_name in ctx.returned.get(id(func), set()):
                return  # ownership handed to the caller
            if func is not None and _returned_directly(ctx, func, target_name):
                return  # ownership handed to the caller
            escape = _escape_level(ctx, node, target_name, func)
            if escape == "returned":
                return  # caller owns it now
            if escape == "strong":
                self.add(ctx, node, rule,
                         f"`{name}` assigned to `{target_name}` and stored where it outlives this scope "
                         f"— no close() anywhere, so it is retained (the classic fd/handle leak)",
                         severity="high")
                return
            if escape == "weak":
                sev = "high" if is_db else "medium"
                self.add(ctx, node, rule,
                         f"`{name}` assigned to `{target_name}`, handed to another call, never closed",
                         severity=sev)
                return
            # purely local: CPython drops the last reference at scope exit and closes it —
            # unless the frame itself outlives the call (generator / coroutine).
            persists = _frame_may_persist(ctx, node)
            if persists:
                sev = "high" if is_db else "medium"
            else:
                sev = "medium" if is_db else "low"
            self.add(ctx, node, rule,
                     ("UNCLOSED_DB_SESSION: " if is_db else "") + f"`{name}` assigned to local `{target_name}` with no close() — CPython "
                     f"refcounting releases it at scope exit, so this is hygiene rather than a leak "
                     f"(run with -W error::ResourceWarning to surface it; a real leak appears if this "
                     f"frame stays alive or under PyPy)",
                     severity=sev)
            return

        # open(...) handed straight into a container: handles.append(open(p))
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
            if parent.func.attr in {"append", "extend", "insert", "add", "setdefault",
                                    "update", "put", "push", "appendleft"}:
                holder = dotted(parent.func.value)
                self.add(ctx, node, rule,
                         f"`{name}` result passed straight into `{holder}.{parent.func.attr}()` — "
                         f"the handle is retained in that container and never closed",
                         severity="high")
                return

        # bare call: open(...) / socket.socket() as a statement
        if isinstance(parent, ast.Expr):
            self.add(ctx, node, "DISCARDED_RESOURCE",
                     f"`{name}` called as a statement; the handle is unreferenced immediately "
                     f"(refcount frees it right away in CPython, but the intent is unclear)",
                     severity="low")
            return
        if isinstance(parent, (ast.Return, ast.arg, ast.keyword, ast.Starred)):
            return
        if isinstance(parent, ast.Attribute):
            return  # open(...).read() - temporary, refcount-freed
        if isinstance(parent, (ast.Compare, ast.If, ast.BoolOp)):
            return

    # ---- pools / executors -------------------------------------------------
    def rule_pools(self, ctx: Context, node: ast.Call) -> None:
        name = dotted(node.func)
        t = tail(name)
        if t not in {tail(p) for p in POOL_CTORS}:
            return
        if ctx.in_with_item(node) or ctx.in_with_block(node):
            return
        # `loop.set_default_executor(ThreadPoolExecutor(...))` -> the event loop owns it and
        # asyncio.run()/shutdown_default_executor() tears it down.
        for anc in ctx.ancestors(node):
            if isinstance(anc, ast.Call) and tail(dotted(anc.func)) == "set_default_executor":
                return
            if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
        func = ctx.enclosing_func(node)
        closed = ctx.cleaned.get(id(func), set()) if func else set()
        if closed & {"shutdown", "close", "terminate", "join", "kill", "__exit__", "stop"}:
            return
        var = _assigned_name(ctx, node)
        if var and (var in closed or ".shutdown" in closed):
            return                                   # var.shutdown() / with var: elsewhere in fn
        if var and func is not None and var in ctx.returned.get(id(func), set()):
            return                                   # ownership handed to caller (module singleton)
        if _returned(ctx, node):
            return
        self.add(ctx, node, "UNCLOSED_POOL", f"`{t}` created without shutdown/close in this scope")

    def rule_processes(self, ctx: Context, node: ast.Call) -> None:
        t = tail(dotted(node.func))
        if t != "Popen":
            return
        parent = ctx.parents.get(id(node))
        if isinstance(parent, ast.Expr):
            self.add(ctx, node, "SUBPROCESS_NOT_REAPED",
                     "Popen() result discarded; child is never reaped and pipes stay open",
                     severity="high")
            return
        func = ctx.enclosing_func(node)
        closed = ctx.cleaned.get(id(func), set()) if func else set()
        if closed & {"wait", "communicate", "terminate", "kill", "poll"} or ctx.in_with_block(node):
            return
        var = _assigned_name(ctx, node)
        if var and var in closed:
            return                                   # var.wait() / var.terminate() seen
        if _passed_to_call(ctx, node):
            return                                   # collected into a container / handed to a helper
        self.add(ctx, node, "SUBPROCESS_NOT_REAPED", "Popen() without wait/communicate/terminate in this scope")

    # ---- threads -----------------------------------------------------------
    def rule_threads(self, ctx: Context, node: ast.Call) -> None:
        t = tail(dotted(node.func))
        if t not in {tail(x) for x in THREAD_CTORS}:
            return
        if t == "Timer":
            func = ctx.enclosing_func(node)
            if func and "cancel" not in ctx.cleaned.get(id(func), set()) and ctx.in_loop(node):
                self.add(ctx, node, "THREAD_IN_LOOP", "Timer created inside a loop without cancel()")
            return
        daemon = any(kw.arg == "daemon" and is_true(kw.value) for kw in node.keywords)
        parent = ctx.parents.get(id(node))
        var = None
        if isinstance(parent, ast.Assign) and isinstance(parent.targets[0], ast.Name):
            var = parent.targets[0].id
        elif isinstance(parent, (ast.List, ast.Set, ast.Tuple)):
            holder = ctx.parents.get(id(parent))
            if isinstance(holder, ast.Assign) and isinstance(holder.targets[0], ast.Name):
                var = holder.targets[0].id          # threads = [Thread(...), Thread(...)]
        func = ctx.enclosing_func(node)
        closed = ctx.cleaned.get(id(func), set()) if func else set()
        joined = bool(closed & {"join", "cancel"}) or (var and var in closed)
        if not joined and var and func is not None and _joined_via_loop(ctx, func, var):
            joined = True                           # for t in threads: t.join()
        if ctx.in_loop(node):
            self.add(ctx, node, "THREAD_IN_LOOP",
                     f"`{t}` created per loop iteration (daemon={daemon}, joined={joined})")
            return
        if not daemon and not joined:
            self.add(ctx, node, "THREAD_NEVER_JOINED",
                     f"non-daemon `{t}` started without join() — it keeps its frames alive until it exits")

    # ---- queues / temp files / logging -------------------------------------
    def rule_queues(self, ctx: Context, node: ast.Call) -> None:
        t = tail(dotted(node.func))
        if t not in QUEUE_CTORS:
            return
        if len(node.args) >= 1 or any(kw.arg == "maxsize" for kw in node.keywords):
            return
        if not ctx.has_put:
            return
        self.add(ctx, node, "QUEUE_UNBOUNDED", f"`{t}()` with no maxsize; consumer must keep up forever")

    def rule_tempfiles(self, ctx: Context, node: ast.Call) -> None:
        t = tail(dotted(node.func))
        func = ctx.enclosing_func(node)
        cleaned = ctx.cleaned.get(id(func), set()) if func else set()
        if t == "NamedTemporaryFile":
            if any(kw.arg == "delete" and not is_true(kw.value) for kw in node.keywords):
                # the atomic-write idiom (write -> fsync -> os.replace, unlink on failure) is fine
                if func is not None and _replaces_or_unlinks(ctx, func):
                    return
                self.add(ctx, node, "TEMP_FILE_LEAK",
                         "NamedTemporaryFile(delete=False) with no unlink/remove/os.replace in this "
                         "function — temp files can accumulate on disk")
        elif t == "mkstemp" and not ctx.in_with_item(node):
            if ctx.has_os_close or any(name in cleaned for name in _assigned_names(ctx, node)):
                return  # fd handed to os.fdopen(...) or os.close(...)
            if func is None and _replaces_or_unlinks(ctx, ctx.tree):
                return
            self.add(ctx, node, "TEMP_FILE_LEAK",
                     "mkstemp() returns an open fd; no os.close()/os.fdopen() in this scope")

    def rule_logging(self, ctx: Context, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "addHandler":
            return
        func = ctx.enclosing_func(node)
        if func is None:
            return  # module scope = registered once, fine
        if _guarded_by_handlers(ctx, func, ctx.line_of(node)):
            return
        self.add(ctx, node, "LOG_HANDLER_LEAK", "addHandler() inside a function with no guard against duplicate handlers")

    def rule_listeners(self, ctx: Context, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr not in {"connect", "subscribe", "add_listener", "addEventListener", "on", "attach"}:
            return
        if ctx.disconnect_present:
            return
        recv = dotted(node.func.value).lower()
        has_callable = any(isinstance(a, (ast.Lambda, ast.Name, ast.Attribute)) for a in node.args)
        if not has_callable:
            return
        if not any(hint in recv for hint in ("signal", "event", "listener", "bus", "emitter", "observer", "subject", "hook")):
            return
        self.add(ctx, node, "LISTENER_NEVER_REMOVED",
                 f"`{node.func.attr}` on `{dotted(node.func.value)}` with no matching removal in this file")

    def rule_tasks(self, ctx: Context, node: ast.Call) -> None:
        t = tail(dotted(node.func))
        if t not in {"create_task", "ensure_future"}:
            return
        parent = ctx.parents.get(id(node))
        if isinstance(parent, ast.Expr):
            self.add(ctx, node, "TASK_REF_DROPPED",
                     f"`{t}()` result discarded; the task holds no strong reference")

    def rule_datastruct_loop(self, ctx: Context, node: ast.Call) -> None:
        t = tail(dotted(node.func))
        if t in {"concat", "concatenate", "vstack", "hstack", "append"} and ctx.in_loop(node):
            recv = dotted(node.func.value)
            if t == "append":
                # word-boundary match so `metadata`/`thread_data` do not read as "data"
                if not re.search(r"\b(df|frame|frames|data|series|arr|array)\b", recv.lower()):
                    return
            self.add(ctx, node, "DATAFRAME_REBUILD_IN_LOOP",
                     f"`{recv}.{t}()` inside a loop rebuilds the whole structure each iteration")

    def rule_gc(self, ctx: Context, node: ast.Call) -> None:
        if dotted(node.func) in {"gc.disable", "gc.freeze"}:
            self.add(ctx, node, "GC_DISABLED", f"`{dotted(node.func)}()` disables/tunes automatic collection")

    def rule_atexit(self, ctx: Context, node: ast.Call) -> None:
        if dotted(node.func) not in {"atexit.register", "signal.signal"}:
            return
        func = ctx.enclosing_func(node)
        if func is None:
            return
        if _guarded_once(ctx, node):
            return                                   # `if not getattr(fn, "_done", ...)` guard
        # one-time setup functions (install/configure/register/start at boot) register once
        if re.search(r"install|configure|setup|set_up|register|bootstrap|init|start|main|enable|prepare",
                     func.name, re.IGNORECASE):
            return
        self.add(ctx, node, "ATEXIT_IN_FUNCTION",
                 f"`{dotted(node.func)}()` registered from inside `{func.name}()` — verify it is not "
                 f"called per request, or handlers accumulate")

    def rule_growth_loop(self, ctx: Context, node: ast.Call) -> None:
        """self.attr / global container mutated inside `while True` with no eviction."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in {"append", "extend", "add", "update", "setdefault", "insert", "put"}:
                return
            recv = dotted(node.func.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            recv = dotted(node.value)
        elif isinstance(node, ast.AugAssign):
            recv = dotted(node.target)
        else:
            return
        if not recv:
            return
        if not ctx.in_unbounded_loop(node):
            return
        if recv.startswith(("self.", "cls.")) or recv in ctx.module_containers:
            if not ctx.bounded(recv):
                where = recv if recv.startswith(("self.", "cls.")) else f"global `{recv}`"
                self.add(ctx, node, "UNBOUNDED_GROWTH_LOOP",
                         f"{where} grows inside an unbounded loop with no eviction (pop/clear/TTL)")

    # ---- function level ----------------------------------------------------
    def rule_mutable_default(self, ctx: Context, func) -> None:
        defaults = list(func.args.defaults) + [d for d in func.args.kw_defaults if d is not None]
        params = [a.arg for a in func.args.args + func.args.kwonlyargs]
        for default in defaults:
            if not isinstance(default, (ast.List, ast.Dict, ast.Set)):
                continue
            if isinstance(default, (ast.List, ast.Dict)) and not _mutated_in_body(ctx, func, params):
                continue
            self.add(ctx, default, "MUTABLE_DEFAULT_ARG",
                     f"mutable default in `{func.name}()`; mutation persists across calls")
            return

    def rule_cache_decorator(self, ctx: Context, func) -> None:
        for dec in func.decorator_list:
            name = dotted(dec.func) if isinstance(dec, ast.Call) else dotted(dec)
            t = tail(name)
            if t not in CACHE_DECORATORS:
                continue
            unbounded = False
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "maxsize" and (
                        kw.value is None or (isinstance(kw.value, ast.Constant) and kw.value.value is None)
                    ):
                        unbounded = True
                if dec.args and isinstance(dec.args[0], ast.Constant) and dec.args[0].value is None:
                    unbounded = True
                if not dec.args and not any(kw.arg == "maxsize" for kw in dec.keywords) and t in {"cache", "lru_cache"}:
                    unbounded = t == "cache"
            else:
                unbounded = t == "cache"
            is_method = ctx.enclosing_class(func) is not None and func.args.args and func.args.args[0].arg in {"self", "cls"}
            if is_method and t in {"lru_cache", "cache", "cached", "memoize"}:
                self.add(ctx, dec, "LRU_CACHE_ON_METHOD",
                         f"`@{t}` on method `{func.name}` caches on self — every instance is retained forever")
            elif unbounded:
                self.add(ctx, dec, "UNBOUNDED_CACHE", f"`@{t}` never evicts (`maxsize=None`)")

    def rule_del_method(self, ctx: Context, cls: ast.ClassDef) -> None:
        for item in cls.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__del__":
                self.add(ctx, item, "FINALIZER_DEL", f"`{cls.name}.__del__` defined")

    def rule_module_container(self, ctx: Context, node: ast.Assign) -> None:
        if ctx.enclosing_func(node) is not None:
            return
        for tgt in node.targets:
            if not isinstance(tgt, ast.Name) or tgt.id not in ctx.module_containers:
                continue
            if tgt.id not in ctx.mutated and not any(m.startswith(tgt.id + ".") for m in ctx.mutated):
                continue
            if ctx.bounded(tgt.id):
                continue
            # honour `# leakscan: ignore` on any line that mutates this container
            if any(_line_ignored(ctx, ln) for ln in _mutation_lines(ctx, tgt.id)):
                continue
            self.add(ctx, node, "MUTABLE_MODULE_STATE",
                     f"module-level `{tgt.id}` is mutated at runtime and has no eviction",
                     severity="high" if _mutated_in_unbounded_loop(ctx, tgt.id) else "medium")
            return

    def rule_traceback(self, ctx: Context, handler: ast.ExceptHandler) -> None:
        for node in ast.walk(handler):
            recv = ""
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"append", "extend", "add", "update", "setdefault", "put"}:
                    recv = dotted(node.func.value)
                    if recv in {"self", "cls"} or not recv:
                        continue
                    args = [dotted(a) for a in node.args]
                    if handler.name in args:
                        self.add(ctx, node, "TRACEBACK_RETAINED",
                                 f"exception `{handler.name}` stored into `{recv}` — keeps the traceback and all its frames alive")


# ----------------------------------------------------------------------------
# small analysis helpers
# ----------------------------------------------------------------------------
def _looks_like_db(name: str) -> bool:
    return any(h in name.lower() for h in ("sqlite", "mysql", "mongo", "redis", "psycopg",
                                           "postgres", "sqlalchemy", "db", "session", "conn",
                                           "engine", "odbc", "asyncpg", "aiomysql", "boto"))


OPEN_QUALIFIERS = {"", "io", "codecs", "os", "gzip", "bz2", "lzma", "tarfile",
                   "zipfile", "aiofiles", "webbrowser", "builtins"}


def _qualifier_ok(name: str, t: str) -> bool:
    """Loose tail matches ('open', 'connect', ...) need an unambiguous qualifier."""
    if t == "open":
        qual = name[: -len(".open")] if name != "open" else ""
        return qual in OPEN_QUALIFIERS
    return True


def _escape_level(ctx: Context, node: ast.AST, name: str, func: Optional[ast.AST]) -> str:
    """
    How far does this local reference travel?

    strong   -> stored on an attribute / in a container / global index: retained,
                a genuine leak while the holder lives.
    weak     -> passed into another call or aliased: the callee may retain it.
    returned -> ownership handed back to the caller (not this scope's problem).
    none     -> strictly local: CPython refcounting frees it at scope exit.
    """
    if func is None:
        return "strong"  # module scope == process lifetime
    containers = {"append", "extend", "insert", "add", "setdefault", "update", "put",
                  "push", "appendleft", "add_reader", "register", "link", "emit", "send"}
    weak = False
    for sub in ast.walk(func):
        if isinstance(sub, ast.Call):
            args = list(sub.args) + [kw.value for kw in sub.keywords]
            names = [a.id for a in args if isinstance(a, ast.Name)]
            if name not in names:
                continue
            if isinstance(sub.func, ast.Attribute) and sub.func.attr in containers:
                return "strong"
            # `return consume(handle)` -> ownership passes out of this scope
            if isinstance(ctx.parents.get(id(sub)), ast.Return):
                return "returned"
            weak = True
        elif isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Name) and sub.value.id == name:
            for t in sub.targets:
                if isinstance(t, ast.Attribute):
                    return "strong"
                if isinstance(t, ast.Subscript):
                    return "strong"
                if isinstance(t, ast.Name) and t.id != name:
                    weak = True
        elif isinstance(sub, ast.Subscript) and isinstance(sub.ctx, ast.Store):
            if isinstance(sub.slice, ast.Name) and sub.slice.id == name:
                return "strong"
        elif isinstance(sub, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
            elts = list(getattr(sub, "values", [])) + [e for e in getattr(sub, "elts", [])]
            if any(isinstance(e, ast.Name) and e.id == name for e in elts):
                weak = True
        elif isinstance(sub, ast.Return) and isinstance(sub.value, ast.Name) and sub.value.id == name:
            return "returned"
    return "weak" if weak else "none"


def _frame_may_persist(ctx: Context, node: ast.AST) -> bool:
    """Generator / coroutine frames (and closures) outlive the call."""
    for anc in ctx.ancestors(node):
        if isinstance(anc, ast.AsyncFunctionDef):
            return True
        if isinstance(anc, (ast.FunctionDef, ast.Lambda)):
            return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(anc))
    return False


def _returned_directly(ctx: Context, func: ast.AST, name: str) -> bool:
    for sub in ast.walk(func):
        if isinstance(sub, ast.Return):
            v = sub.value
            if isinstance(v, ast.Name) and v.id == name:
                return True
            if isinstance(v, ast.Tuple) and any(isinstance(e, ast.Name) and e.id == name for e in v.elts):
                return True
    return False


def _assigned_names(ctx: Context, node: ast.AST) -> list[str]:
    """Variable names bound from an expression, including tuple unpacking."""
    parent = ctx.parents.get(id(node))
    names: list[str] = []
    if isinstance(parent, ast.Assign):
        for target in parent.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Tuple):
                names += [e.id for e in target.elts if isinstance(e, ast.Name)]
    elif isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
        names.append(parent.target.id)
    return names


def _joined_via_loop(ctx: Context, scope: ast.AST, collection: str) -> bool:
    """Detect `for t in threads: t.start()` ... `for t in threads: t.join()`."""
    targets = set()
    for sub in ast.walk(scope):
        if isinstance(sub, ast.For) and isinstance(sub.iter, ast.Name) and sub.iter.id == collection:
            if isinstance(sub.target, ast.Name):
                targets.add(sub.target.id)
    if not targets:
        return False
    for sub in ast.walk(scope):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in {"join", "cancel"} and isinstance(sub.func.value, ast.Name):
                if sub.func.value.id in targets:
                    return True
    return False


def _replaces_or_unlinks(ctx: Context, scope: ast.AST) -> bool:
    """Does this scope clean up a temporary file (os.replace / unlink / remove)?"""
    for sub in ast.walk(scope):
        if isinstance(sub, ast.Call):
            name = dotted(sub.func)
            if name in {"os.replace", "os.unlink", "os.remove"}:
                return True
            if isinstance(sub.func, ast.Attribute) and sub.func.attr in {"unlink", "remove", "replace"}:
                return True
    return False


def _assigned_name(ctx: Context, node: ast.AST) -> str:
    """Variable name this expression is assigned to, if any (x = ... / x: T = ...)."""
    parent = ctx.parents.get(id(node))
    if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
        return parent.targets[0].id
    if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
        return parent.target.id
    return ""


def _returned(ctx: Context, node: ast.AST) -> bool:
    for anc in ctx.ancestors(node):
        if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        if isinstance(anc, ast.Return):
            return True
    return False


def _passed_to_call(ctx: Context, node: ast.AST) -> bool:
    """True if the expression is an argument inside a larger call (ownership moved)."""
    for anc in ctx.ancestors(node):
        if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        if isinstance(anc, (ast.Tuple, ast.List, ast.Dict, ast.Set)):
            continue
        if isinstance(anc, ast.Call):
            return True
    return False


def _guarded_once(ctx: Context, node: ast.AST) -> bool:
    """`if not getattr(fn, "_flag", False):` style one-time-registration guard."""
    for anc in ctx.ancestors(node):
        if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        if isinstance(anc, ast.If):
            src = ast.dump(anc.test)
            if "getattr" in src or "hasattr" in src or "_done" in src or "_initialized" in src:
                return True
    return False


def _passed_as_arg(ctx: Context, func: ast.AST, name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            for a in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(a, ast.Name) and a.id == name:
                    if dotted(node.func).endswith(".close"):
                        continue
                    return True
    return False


def _stored_on_attribute(assign_node: ast.AST) -> bool:
    targets = getattr(assign_node, "targets", None)
    if targets:
        return any(isinstance(t, ast.Attribute) for t in targets)
    target = getattr(assign_node, "target", None)
    return isinstance(target, ast.Attribute)


def _mutated_in_body(ctx: Context, func: ast.AST, params: list[str]) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"append", "extend", "add", "update", "setdefault", "insert"}:
                recv = dotted(node.func.value)
                if recv in params:
                    return True
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and dotted(node.value) in params:
            return True
    return False


def _guarded_by_handlers(ctx: Context, func: ast.AST, call_line: int = 0) -> bool:
    """Does this function inspect the existing handler list before adding one?"""
    start = getattr(func, "lineno", 1) - 1
    end = getattr(func, "end_lineno", start + 1)
    window = "\n".join(ctx.lines[max(0, start - 2): end])
    if re.search(r"if\s+not\s+[\w\.]*\.?handlers\b", window):
        return True
    # any scan of `.handlers` (e.g. `any(isinstance(h, X) for h in root.handlers)`) earlier
    # in the same function counts as a guard.
    for line_no in range(start, min(end, (call_line or end + 1))):
        if ".handlers" in ctx.lines[line_no]:
            return True
    return False


def _line_ignored(ctx: Context, lineno: int) -> bool:
    for candidate in (lineno, lineno - 1):
        if 1 <= candidate <= len(ctx.lines) and "leakscan: ignore" in ctx.lines[candidate - 1]:
            return True
    return False


def _mutation_lines(ctx: Context, name: str) -> list[int]:
    """Lines where `name` (or name.attr) is mutated."""
    out = []
    for node in ast.walk(ctx.tree):
        recv = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"append", "extend", "add", "update", "setdefault", "insert", "put"}:
                recv = dotted(node.func.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            recv = dotted(node.value)
        elif isinstance(node, ast.AugAssign):
            recv = dotted(node.target)
        if recv and recv.split(".")[0] == name:
            out.append(getattr(node, "lineno", 0))
    return out


def _mutated_in_unbounded_loop(ctx: Context, name: str) -> bool:
    for node in ast.walk(ctx.tree):
        recv = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"append", "extend", "add", "update", "setdefault", "insert", "put"}:
                recv = dotted(node.func.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            recv = dotted(node.value)
        elif isinstance(node, ast.AugAssign):
            recv = dotted(node.target)
        if recv and recv.split(".")[0] == name and ctx.in_unbounded_loop(node):
            return True
    return False


# ----------------------------------------------------------------------------
# file discovery + reporting
# ----------------------------------------------------------------------------
def iter_python_files(paths: list[str], excludes: list[str]) -> Iterable[str]:
    for path in paths:
        if os.path.isfile(path):
            if path.endswith(".py"):
                yield path
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_EXCLUDES and not d.startswith(".")]
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, pat) for pat in excludes)]
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                if any(fnmatch.fnmatch(f, pat) for pat in excludes):
                    continue
                yield os.path.join(root, f)


def render_text(findings: list[Finding], scanned: int, files: list[str]) -> str:
    out = []
    for f in sorted(findings, key=lambda x: (SEV_RANK[x.severity], x.path, x.line)):
        out.append(f"{f.severity.upper():8} {f.path}:{f.line}:{f.col}  [{f.rule}]  {f.message}")
        if f.snippet:
            out.append(f"         | {f.snippet}")
        out.append("")
    out.append(render_summary_line(findings, scanned, files))
    return "\n".join(out)


def render_summary_line(findings: list[Finding], scanned: int, files: list[str]) -> str:
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_ORDER}
    total = len(findings)
    parts = "  ".join(f"{s}={counts[s]}" for s in SEVERITY_ORDER if counts[s])
    return f"scanned {scanned} file(s), {total} finding(s): {parts or 'none'}"


def render_markdown(findings: list[Finding], scanned: int, files: list[str]) -> str:
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_ORDER}
    lines = ["# leakscan report", ""]
    lines.append(f"**Files scanned:** {scanned}  ")
    lines.append(f"**Findings:** {len(findings)} "
                 + " · ".join(f"{s}: {counts[s]}" for s in SEVERITY_ORDER if counts[s]))
    lines.append("")
    if not findings:
        lines.append("No leak-shaped code found by the static rules. "
                     "Static analysis is necessary-but-not-sufficient — see the runtime checklist.")
        return "\n".join(lines)

    by_rule: dict[str, list[Finding]] = {}
    for f in findings:
        by_rule.setdefault(f.rule, []).append(f)
    lines += ["## Summary by rule", "", "| Rule | Severity | Count | What it means |", "|---|---|---|---|"]
    for rule, items in sorted(by_rule.items(), key=lambda kv: (SEV_RANK[kv[1][0].severity], kv[0])):
        title = RULES.get(rule, ("", rule, ""))[1]
        lines.append(f"| `{rule}` | {items[0].severity} | {len(items)} | {title} |")
    lines.append("")

    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.path, []).append(f)
    for path, items in sorted(by_file.items()):
        lines += [f"## `{path}`", ""]
        for f in sorted(items, key=lambda x: x.line):
            lines.append(f"### L{f.line} · {f.rule} · _{f.severity}_")
            lines.append("")
            lines.append(f.message)
            lines.append("")
            if f.snippet:
                lines += ["```python", f.snippet, "```", ""]
            lines += [f"**Fix:** {f.suggestion}", ""]
    lines += ["---", "", "Rules where the static heuristic is weakest are marked low/info — "
                        "confirm with the runtime workflow in `python-memory-leak-checklist.md` "
                        "(tracemalloc / objgraph / memray) before spending time on them."]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Static memory/resource-leak scanner for Python.")
    ap.add_argument("paths", nargs="*", default=["."], help="files or directories to scan")
    ap.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    ap.add_argument("--min-severity", choices=SEVERITY_ORDER, default="info")
    ap.add_argument("--fail-on", choices=SEVERITY_ORDER + ["never"], default="never",
                    help="exit 1 if any finding is at/above this severity")
    ap.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable)")
    ap.add_argument("-o", "--output", help="write report to a file instead of stdout")
    ap.add_argument("--list-rules", action="store_true")
    args = ap.parse_args(argv)

    if args.list_rules:
        for rule, (sev, title, fix) in RULES.items():
            print(f"{rule:26} {sev:8} {title}")
        return 0

    files = list(iter_python_files(args.paths or ["."], args.exclude))
    scanner = Scanner(min_severity=args.min_severity)
    for path in files:
        scanner.scan_path(path)
    findings = scanner.findings

    if args.format == "json":
        report = json.dumps({
            "files_scanned": len(files),
            "findings": [f.as_dict() for f in findings],
        }, indent=2)
    elif args.format == "markdown":
        report = render_markdown(findings, len(files), files)
    else:
        report = render_text(findings, len(files), files)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print(render_summary_line(findings, len(files), files))
    else:
        print(report)

    if args.fail_on != "never":
        worst = min((SEV_RANK[f.severity] for f in findings), default=99)
        if worst <= SEV_RANK[args.fail_on]:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
