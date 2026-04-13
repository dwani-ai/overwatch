# Overwatch — Research todo report

**Audience:** researchers and engineers running lab evals and game-control experiments  
**Scope:** evaluation methodology, backlog, and Factorio closed-loop work (sibling to the core video pipeline)

Complements [`technical_report.md`](technical_report.md) (engineering reference). The technical report §13 notes there is no golden eval for agent quality yet; this document is the plan to add one.

---

## Evaluation goals

- Measure **structured correctness** (JSON/schema), **temporal grounding** (claims vs timestamps/chunks), and **actionability** (useful next steps without unsafe leakage).
- Keep runs **reproducible**: model id, prompt/version hash, clip-set version, and environment knobs recorded on every run.

---

## Layered evaluation methodology

### A — Structured / JSON checks

- Validate chunk outputs (`scene_summary`, merged chunk payloads) and job `summary_json` against Pydantic models.
- Golden fixtures for JSON repair and extraction ([`analysis/json_extract.py`](../src/overwatch/analysis/json_extract.py)).
- Regression tests with **stubbed** vLLM responses (no network).

### B — Clip-level human rubrics

- Small **frozen** clip set (10–30 short segments).
- Short rubrics: 1–3 Likert scales + one optional free-text note per clip.
- Two annotators where feasible; report simple agreement (e.g. Cohen’s kappa or % within 1 point).

### C — Job-level agent rubrics

- Roll the same clips into synthetic or real `JobSummaryPayload` fixtures.
- Judge synthesis, risk, privacy, and related agent outputs against rubrics (human preferred; model-as-judge only with documented bias caveats).

### D — Orchestration / industry pipelines

- Assert step ordering, **409** when a second orchestration is started, and queue-to-terminal status transitions with mocked persistence where needed.
- Extend toward HTTP integration tests when the stack stabilizes.

---

## Operational hygiene (run cards)

Each eval run should record at minimum:

| Field | Example |
|-------|---------|
| Date (UTC) | 2026-04-12 |
| Git commit | `abc123f` |
| Model id | served id on vLLM |
| `VLLM_*` knobs | timeouts, max tokens |
| Clip / fixture set version | tag or manifest hash |

Optional layout under repo root: `evals/` — manifest of clips, expected JSON snippets, rubric CSVs (add when you start human annotation).

---

## Explicit non-goals (research lab)

- Production **authentication**, **retention**, or **backup** work for this track unless separately prioritized.

---

## Factorio closed-loop (research prototype)

**Intent:** Perception–action loop separate from MP4 chunk jobs: periodic screenshots → multimodal **state** JSON (HUD/menus) → planner → low-level input. See package [`overwatch.factorio`](../src/overwatch/factorio/).

**Risk / governance:** Client automation may violate game or platform terms; **lab-only**. Use a kill switch, cap actions per minute, and do not run unattended on machines with sensitive applications.

**Phases (backlog):**

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

*End of todo report.*
