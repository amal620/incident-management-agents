import json
import unittest
from pathlib import Path

WORKFLOW_FILE = Path(__file__).resolve().parents[1] / "incident_management_v1.0.0.json.json"

class TestWorkflowStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with WORKFLOW_FILE.open(encoding="utf-8-sig") as f:
            cls.workflow = json.load(f)

    def test_nodes_is_a_list(self):
        self.assertIsInstance(self.workflow["data"]["nodes"], list)

    def test_edges_is_a_list(self):
        self.assertIsInstance(self.workflow["data"]["edges"], list)

    def test_viewport_is_an_object(self):
        self.assertIsInstance(self.workflow["data"]["viewport"], dict)

    def test_nodes_are_not_empty(self):
        self.assertGreater(len(self.workflow["data"]["nodes"]), 0)

if __name__ == "__main__":
    unittest.main()
