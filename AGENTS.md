# Agent instructions for this repository

This repo *is* a set of Apigee X agent skills (see `README.md`). When working here:

- Skills live in `skills/<name>/SKILL.md`; frontmatter `name` must equal the folder name.
- Scripts are Python 3.9+ **stdlib only** (PyYAML optional for YAML specs) or bash; keep them dependency-free.
- Every scaffold output must pass `skills/apigee-proxy-builder/scripts/validate_bundle.py --strict`.
- Run `make test` before finishing any change; add tests in `tests/test_skills.py` for new options/checks.
- Do not add org-, project- or demo-specific values (hosts, org names, keys) to skills, scripts or references.
- Apigee X docs only: `cloud.google.com/apigee` (never `docs.apigee.com`).
- Follow `rules/apigee_proxy_standards.md` and `rules/security_and_governance.md` for any XML you write.
