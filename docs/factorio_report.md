# Overwatch — Factorio closed-loop (research)

**Audience:** researchers prototyping game perception–action loops  
**Scope:** Factorio-style **closed loop** work — separate from the core MP4 video job pipeline and from the general eval methodology ([`eval_report.md`](eval_report.md))

---

## Intent

Perception–action loop: screenshots → multimodal **state** JSON (`FactorioState`) → **text planner** → structured **`GameAction`** → **executor** (dry-run by default). Implementation lives in package [`overwatch.factorio`](../src/overwatch/factorio/).

---

## Agent loop

[`run_factorio_agent`](../src/overwatch/factorio/agent.py) runs up to **`max_steps`** iterations:

1. Capture PNG (full monitor via **mss**, or inject `capture_fn` in tests).
2. Save frame via [`FactorioSessionStore.append_frame`](../src/overwatch/factorio/session.py).
3. [`parse_factorio_state_from_png`](../src/overwatch/factorio/state_parser.py) (multimodal vLLM).
4. If confidence is below **`FACTORIO_CONFIDENCE_THRESHOLD`**, skip planner and use **`noop`**.
5. Else [`plan_next_action`](../src/overwatch/factorio/planner.py) (text-only vLLM; uses **`VLLM_AGENT_MAX_TOKENS`** / **`VLLM_AGENT_TIMEOUT_SEC`**).
6. Execute action (respecting **`FACTORIO_MAX_ACTIONS_PER_MINUTE`** when not dry-run).
7. Persist one row per step in **`factorio_agent_steps`** (state, action, planner raw text, errors).
8. Sleep **`FACTORIO_SETTLE_SEC`** before the next iteration.

---

## CLI (lab driver)

```bash
PYTHONPATH=src python -m overwatch.factorio --goal "Your objective" --max-steps 5
```

- **Dry-run default:** no `pyautogui` key events unless **`--execute`**.
- **Kill switch:** **Ctrl+C** / **SIGTERM** (where supported) sets a stop flag between steps.
- **`--tech-tree path.json`:** overrides **`FACTORIO_TECH_TREE_PATH`** for planner grounding.
- **`--session-id`:** continue under an existing session (must already exist in the local store).
- **`--monitor`:** mss monitor index (default `1` = primary).
- **`--no-click`:** with **`--execute`**, disable mouse clicks (keys/skills only).

---

## Running against a live Factorio client

1. **vLLM:** Set **`VLLM_BASE_URL`**, **`VLLM_MODEL`**, and optional **`VLLM_API_KEY`**. The state parser sends **PNG screenshots**; the model must support **image** inputs.
2. **Display:** Factorio must be visible on the monitor you capture (default **primary**). Put the game **in focus**; borderless fullscreen on that monitor avoids the planner clicking on desktop chrome.
3. **Dry-run first:** Run without **`--execute`** and inspect logs + session under **`FACTORIO_ROOT`** / **`DATA_DIR/factorio`** (PNGs + SQLite steps).
4. **Execute:** `pip install -r requirements.txt` includes **pyautogui**. Then add **`--execute`**. Clicks use **PNG coordinates** plus the monitor’s **global origin** so multi-monitor setups stay consistent with **mss**.
5. **Safety:** **`FACTORIO_MAX_ACTIONS_PER_MINUTE`** caps bursts. **`--no-click`** is useful for a first real run. PyAutoGUI **failsafe** (mouse to a corner) can abort automation.

---

## Risk / governance

Client automation may violate game or platform terms; treat as **lab-only**. Do not run **`--execute`** on machines with sensitive applications.

---

## Phases (backlog)

| Phase | Deliverable |
|-------|-------------|
| P1 | Session store + screenshot capture loop (filesystem + SQLite metadata) |
| P2 | VLM **state parser** → `FactorioState` (validated JSON) |
| P3 | **Skill library** + executor (`dry_run` default); optional real input backend |
| P4 | Frozen screenshot **parser** fixtures under [`evals/factorio_parser/`](../evals/factorio_parser/) |
| P5 | **Agent loop** + **`factorio_agent_steps`** persistence + **CLI** |

**Optional oracle:** For calibration and regression, a non-vision channel (mod/Lua/log) can supply ground truth even if the demo stays vision-first.

---

## Tech tree fixture (minimal)

Example grounding file for the planner: [`evals/factorio_data/tech_tree_minimal.json`](../evals/factorio_data/tech_tree_minimal.json). Point **`FACTORIO_TECH_TREE_PATH`** at it or pass **`--tech-tree`**. Loaded as text via [`load_tech_tree_text`](../src/overwatch/factorio/tech_tree.py).

---

## Parser regression fixtures

Frozen screenshots and expected JSON for the Factorio HUD state parser live under [`evals/factorio_parser/`](../evals/factorio_parser/). See that directory’s `manifest.json` for case ids and paths. Load cases in Python with `overwatch.factorio.eval_manifest.load_parser_eval_cases`. Tests under `tests/test_factorio_eval_manifest.py` validate manifest shape and end-to-end parsing with a mocked vLLM response.

---

## Configuration

See [`README.md`](../README.md) for `FACTORIO_*`, `VLLM_FACTORIO_*`, and (for the planner) `VLLM_AGENT_MAX_TOKENS` / `VLLM_AGENT_TIMEOUT_SEC`.

---

*End of Factorio report.*
