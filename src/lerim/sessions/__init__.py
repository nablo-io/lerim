"""Session catalog, queue, and shared session models.

Importing this package pulls in nothing: callers import the submodule they
need (``lerim.sessions.catalog``, ``lerim.sessions.types``). That keeps
``lerim.sessions.types`` a leaf module, so the trace sources in
``lerim.adapters`` can depend on it without an import cycle back through the
catalog.
"""
