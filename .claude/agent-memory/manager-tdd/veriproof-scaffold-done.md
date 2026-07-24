---
name: veriproof-scaffold-done
description: VeriProof SPEC-000 scaffold complete — venv location, how to run Django/pytest, and where SSOT decisions diverged
metadata:
  type: project
---

VeriProof AI SPEC-000 scaffold is COMPLETE (as of 2026-07-23). The Django 5 project lives in `veriproof/` inside the repo root `GoogleSolana/`.

**How to run commands (IMPORTANT — non-obvious):**
The system Python is PEP-668 externally-managed, so a project-local venv was created at the REPO ROOT: `GoogleSolana/.venv`. All Django/pytest commands run from `veriproof/` using `../.venv/bin/python`:
- `../.venv/bin/python manage.py <cmd>` (run from `veriproof/`)
- `../.venv/bin/python -m pytest` (run from `veriproof/`)
Installed there: Django 5.2.16, Pillow 12.3, pytest-django 4.12, factory_boy 3.3, dj_database_url, freezegun, pytest-cov, pytest-mock.

**Why:** no venv existed at task start and Homebrew Python refused `pip install` (PEP 668). Created `.venv` rather than `--break-system-packages` — cleaner and reusable for all later SPECs.

**How to apply:** always activate or reference `../.venv/bin/python` for any veriproof work. Do NOT reinstall into system Python.

**Default DB:** SQLite (`veriproof/db.sqlite3`) for local TDD; `DATABASE_URL` switches to PostgreSQL. Local-fallback flags are the app default (FIRESTORE off, local storage, AP2 off, mock sandbox).

**SSOT divergence log (decisions where docs were ambiguous):**
1. `django.contrib.admin` was ADDED to INSTALLED_APPS (task enumerated only {auth,contenttypes,sessions,messages,staticfiles}) because the URLs deliverable requires an "admin placeholder" route → admin app needed. Minor, non-breaking.
2. `on_delete` was unspecified in SSOT for most FKs. Chosen: IpAsset.parent_asset=PROTECT, License.asset=PROTECT, License.session=SET_NULL, BatchItem.asset=PROTECT, BatchItem.license=SET_NULL, AgentEvent.asset/session=SET_NULL, RoyaltyDistribution.license=CASCADE, BatchItem.order=CASCADE, NegotiationSession.asset=CASCADE.
3. `created_at` added to BatchOrder/BatchItem (not in SSOT table) for audit consistency — non-breaking.
4. Creator placed in `apps/ip` (task allowed ip-or-common); AgentEvent in `apps/common`.

Related: [[veriproof-project]] in the auto-memory system.
