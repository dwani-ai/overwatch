from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from overwatch.factorio.models import FactorioState


@dataclass(frozen=True)
class ParserEvalCase:
    id: str
    image_path: Path
    expected_state: FactorioState


def load_parser_eval_cases(eval_dir: Path) -> list[ParserEvalCase]:
    """
    Load ``manifest.json`` under ``eval_dir`` (repo: ``evals/factorio_parser``).

    Paths in the manifest are relative to ``eval_dir``.
    """
    manifest_path = eval_dir / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[ParserEvalCase] = []
    for item in raw.get("cases", []):
        cid = str(item["id"])
        img = eval_dir / str(item["image"])
        exp = eval_dir / str(item["expected"])
        state = FactorioState.model_validate_json(exp.read_text(encoding="utf-8"))
        cases.append(ParserEvalCase(id=cid, image_path=img, expected_state=state))
    return cases
