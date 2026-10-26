import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.generate_system_api_graph import build_graph, outputs


class ApiGraphGeneratorTests(unittest.TestCase):
    def test_api_graph_covers_routes_frontend_and_worker_boundary(self):
        graph = build_graph()

        self.assertGreaterEqual(graph["summary"]["backend_routes"], 90)
        self.assertGreaterEqual(graph["summary"]["frontend_http_calls"], 60)
        self.assertEqual(graph["summary"]["unresolved_frontend_calls"], 0)

        render_route = next(
            route
            for route in graph["routes"]
            if route["method"] == "POST" and route["path"] == "/api/annotation/render"
        )
        self.assertTrue(
            any(
                edge["type"] == "calls_api"
                and edge["to"] == render_route["id"]
                and "handleRenderCustomDrawing" in edge["from"]
                for edge in graph["edges"]
            )
        )

        actions = {item["action"] for item in graph["worker_actions"]}
        self.assertTrue(
            {"render", "candidates", "experimental_render", "experimental_candidates"} <= actions
        )
        worker_links = {
            edge["to"]
            for edge in graph["edges"]
            if edge["type"] in {"dispatches", "configures_worker_action"}
        }
        self.assertIn("worker:experimental_render", worker_links)
        self.assertIn("worker:experimental_candidates", worker_links)

    def test_api_graph_output_is_deterministic(self):
        self.assertEqual(outputs(build_graph()), outputs(build_graph()))


if __name__ == "__main__":
    unittest.main()
