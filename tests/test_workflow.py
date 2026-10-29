import json
import unittest
from pathlib import Path

WORKFLOW_FILE = Path(__file__).resolve().parents[1] / "incident_management_v1.0.0.json.json"

class TestWorkflowJSON(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with WORKFLOW_FILE.open(encoding="utf-8-sig") as f:
            cls.workflow = json.load(f)

    def test_root_is_object(self):
        self.assertIsInstance(self.workflow, dict)

    def test_required_keys_exist(self):
        required = {
            "data", "description", "endpoint_name",
            "id", "is_component", "name", "tags"
        }
        self.assertTrue(required.issubset(self.workflow.keys()))

    def test_data_is_present(self):
        self.assertIn("data", self.workflow)
        self.assertIsNotNone(self.workflow["data"])

if __name__ == "__main__":
    unittest.main()
