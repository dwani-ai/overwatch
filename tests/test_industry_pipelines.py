from __future__ import annotations

import unittest

from overwatch.industry_pipelines import INDUSTRY_PIPELINES, pipeline_for
from overwatch.models import AgentKind, IndustryPack


class TestIndustryPipelines(unittest.TestCase):
    def test_all_packs_defined(self) -> None:
        for pack in IndustryPack:
            self.assertIn(pack, INDUSTRY_PIPELINES)
            steps = INDUSTRY_PIPELINES[pack]
            self.assertGreater(len(steps), 0)
            for a in steps:
                self.assertIsInstance(a, AgentKind)

    def test_healthcare_starts_with_privacy_second(self) -> None:
        steps = pipeline_for(IndustryPack.healthcare_facilities)
        self.assertEqual(steps[0], AgentKind.synthesis)
        self.assertEqual(steps[1], AgentKind.privacy_review)

    def test_retail_prioritizes_loss_prevention_early(self) -> None:
        steps = pipeline_for(IndustryPack.retail_qsr)
        self.assertEqual(steps[0], AgentKind.synthesis)
        self.assertEqual(steps[1], AgentKind.loss_prevention)

    def test_pipeline_for_returns_copy(self) -> None:
        a = pipeline_for(IndustryPack.general)
        b = pipeline_for(IndustryPack.general)
        self.assertEqual(a, b)
        a.pop()
        self.assertNotEqual(a, b)
