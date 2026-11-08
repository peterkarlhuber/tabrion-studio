"""Access protection of the local server (STU-003, STU-ADR-0002, threats T-01, T-02, T-03, T-07, T-15).

* :mod:`.launch`: one-time launch tokens and the 0600 redirect files that carry them to the browser (review F-02);
* :mod:`.sessions`: the session cookies a launch token is exchanged for, each with its own CSRF value (F-09);
* :mod:`.access`: the ASGI middleware that applies the Host allowlist, the Origin and ``Sec-Fetch-*`` rules, JSON-only
  bodies, the session check and the security headers to every request (T-01, T-02, F-05, F-08, T-07, T-15).

This package depends on nothing above it (no app, server or CLI; import-linter contract ``security-below-app``) and
the launch and session modules use the standard library only, so the CLI can use them without loading the server.
"""
