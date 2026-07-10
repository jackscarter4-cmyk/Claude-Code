PY := python3

.PHONY: demo eval audit responder triage watcher approve-list clean

## make demo — full seam-layer flow end-to-end on fixtures (< 2 min, offline-safe)
demo: clean
	@echo "==================== 1/5  STALE-SURFACE AUDIT ===================="
	$(PY) -m audit.pipeline
	@echo
	@echo "==================== 2/5  EVENTS INQUIRY RESPONDER ==============="
	$(PY) -m responder.pipeline
	@echo
	@echo "==================== 3/5  CLUB EXCEPTION TRIAGE =================="
	$(PY) -m triage.pipeline
	@echo
	@echo "==================== 4/5  ESCALATION LADDER (simulated clock) ===="
	$(PY) -m triage.escalate --advance-hours 4
	$(PY) -m triage.escalate --advance-hours 8
	@echo
	@echo "==================== 5/5  REPUTATION WATCHER ====================="
	$(PY) -m watcher.pipeline --reset-state
	@echo
	@echo "==================== APPROVAL GATE ==============================="
	$(PY) -m approval.cli list | tail -3
	@echo
	@echo "Demo artifacts: out/surface_audit.md, out/digest_*.html, out/queue/,"
	@echo "out/triage_log.jsonl, out/responder_metrics.json"

## make eval — the five gates; exit code fails the build on any gate
eval:
	$(PY) -m evals.run_evals

audit:
	$(PY) -m audit.pipeline

audit-live:
	$(PY) -m audit.pipeline --live

responder:
	$(PY) -m responder.pipeline

triage:
	$(PY) -m triage.pipeline

watcher:
	$(PY) -m watcher.pipeline

watcher-live:
	$(PY) -m watcher.pipeline --live

approve-list:
	$(PY) -m approval.cli list

clean:
	rm -rf out watcher/state.json
	@mkdir -p out
