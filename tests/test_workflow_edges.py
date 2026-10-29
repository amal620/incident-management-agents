import json
import unittest
from pathlib import Path

WORKFLOW_FILE = Path(__file__).resolve().parents[1] / "incident_management_v1.0.0.json.json"

class TestWorkflowEdges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with WORKFLOW_FILE.open(encoding="utf-8-sig") as f:
            cls.workflow = json.load(f)["data"]

    def test_every_edge_references_existing_nodes(self):
        nodes = self.workflow["nodes"]
        edges = self.workflow["edges"]

        node_ids = {
            node.get("id") or node.get("data", {}).get("id")
            for node in nodes
        }

        missing = []
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")

            if source not in node_ids:
                missing.append(f"Missing source: {source}")

            if target not in node_ids:
                missing.append(f"Missing target: {target}")

        self.assertEqual(missing, [], "\n".join(missing))

if __name__ == "__main__":
    unittest.main()
