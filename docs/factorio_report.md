# Overwatch — Factorio closed-loop (research)

**Audience:** researchers prototyping game perception–action loops  
**Scope:** Factorio-style **closed loop** work — separate from the core MP4 video job pipeline and from the general eval methodology ([`eval_report.md`](eval_report.md))

---

## Intent

Perception–action loop: periodic screenshots → multimodal **state** JSON (HUD/menus) → planner (future) → low-level input. Implementation lives in package [`overwatch.factorio`](../src/overwatch/factorio/).

---

## Risk / governance

Client automation may violate game or platform terms; treat as **lab-only**. Use a kill switch, cap actions per minute, and do not run unattended on machines with sensitive applications.

---

## Phases (backlog)

| Phase | Deliverable |
|-------|-------------|
| P1 | Session store + screenshot capture loop (filesystem + SQLite metadata) |
| P2 | VLM **state parser** → `FactorioState` (validated JSON) |
| P3 | **Skill library** + executor (`dry_run` default); optional real input backend |
| P4 | Frozen screenshot **parser** fixtures under [`evals/factorio_parser/`](../evals/factorio_parser/) |

**Optional oracle:** For calibration and regression, a non-vision channel (mod/Lua/log) can supply ground truth even if the demo stays vision-first.

---

## Parser regression fixtures

Frozen screenshots and expected JSON for the Factorio HUD state parser live under [`evals/factorio_parser/`](../evals/factorio_parser/). See that directory’s `manifest.json` for case ids and paths. Load cases in Python with `overwatch.factorio.eval_manifest.load_parser_eval_cases`. Tests under `tests/test_factorio_eval_manifest.py` validate manifest shape and end-to-end parsing with a mocked vLLM response.

---

## Configuration

See [`README.md`](../README.md) for `FACTORIO_*` and `VLLM_FACTORIO_*` environment variables.

---

*End of Factorio report.*
