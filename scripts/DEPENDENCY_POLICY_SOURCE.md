# Dependency policy K-33 (vendored copy)

`dependency-policy.json` (repository root) and `scripts/check_dependency_policy.py` are unchanged copies from
nodestro-studio, the integrator that holds the edge list K-33 (TABRION AC-2, QA-17). The policy copy holds only
tabrion-studio's outgoing edges. `python scripts/check_dependency_policy.py` checks this repository's
`pyproject.toml` (core, every extra and dependency group) against it; `tests/architecture/test_dependency_policy.py`
checks the copies against the SHA-256 below, runs the checker and probes forbidden requirements.

Origin: `https://github.com/peterkarlhuber/nodestro-studio`, files `scripts/check_dependency_policy.py` and the output
of `python scripts/check_dependency_policy.py --copy tabrion-studio`, at commit `1a941991bd13d6d1447fc6c15501ef8a62d66145` (nodestro-studio PR #225, branch `claude/w3-ns003`, NS-003: the
edge `tabrion-studio -> nodestro-session`; the merge commit once #225 is merged)

- `check_dependency_policy.py`: `9c790533d9bc7e34927b48f532dfcb311f6841984430bf501e05304816ff386e`
- `dependency-policy.json`: `9de19bd34d50b9576542d9bb453e0d962d7bc5ea3cd173562228411cfa6a46cc`

## Updating the copies

Never edit either file by hand. A new or changed edge goes into nodestro-studio `platform/dependency-policy.json`
first (it needs the plan owner's K-33 decision). Then, in a nodestro-studio checkout, write the two files here:
`python scripts/check_dependency_policy.py --copy tabrion-studio > <this repo>/dependency-policy.json` and copy
`scripts/check_dependency_policy.py` unchanged. Update the origin commit and both SHA-256 above
(`sha256sum dependency-policy.json scripts/check_dependency_policy.py`) and run the test.
