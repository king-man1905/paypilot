# PayPilot Release Operations & Rollback Runbook (Phase 23)

## 1. Pre-Release Checklist
Before tagging or deploying a new release candidate:
- [ ] Ensure local working directory is clean (`git status`).
- [ ] Run full test suite: `python -m pytest -v`.
- [ ] Run 32-case offline evaluation: `python evaluation/run_evaluation.py`.
- [ ] Run secret audit: `python -c "from evaluation.release_pipeline import stage_1_secret_audit; print(stage_1_secret_audit())"`.
- [ ] Run safe release pipeline simulation: `python evaluation/release_pipeline.py`.

---

## 2. Versioned Database Migration Procedures

### Inspect Migration Status
```bash
python -c "from backend.storage.versioned_migrator import get_versioned_migrator; print(get_versioned_migrator().get_status())"
```

### Apply Forward Migrations
```bash
python -m backend.storage.migrator
```

### Roll Back Last Applied Migration Step
```bash
python -c "from backend.storage.versioned_migrator import get_versioned_migrator; print(get_versioned_migrator().rollback(steps=1))"
```

### Verify Migration Checksum Integrity
```bash
python -c "from backend.storage.versioned_migrator import get_versioned_migrator; print(get_versioned_migrator().verify_checksums())"
```

---

## 3. Deployment & Promotion Flow

```bash
# 1. Build and test release candidate
python evaluation/release_pipeline.py

# 2. Run release microbenchmarks
python evaluation/release_benchmark.py

# 3. If release pipeline outputs 'PROMOTED', proceed to tag release:
git tag -a v1.23.0 -m "Release v1.23.0"
git push origin v1.23.0
```

---

## 4. Rollback & Incident Recovery Procedures

### Scenario A: Migration Gate Fails
1. Migration gate halts the release pipeline before candidate traffic is admitted.
2. Check logs for schema constraint errors or table lock timeouts.
3. Fix the migration script in `backend/storage/migrations/` and re-run checksum verification.

### Scenario B: Deployment Smoke Test Fails
1. Release pipeline automatically blocks candidate promotion.
2. Automated rollback engine restores the last verified stable version (`v1.22.0`).
3. Readiness probe `/ready` on the active instance verifies operational health.
4. Review test client failure logs in `evaluation/release_pipeline_report.json`.
