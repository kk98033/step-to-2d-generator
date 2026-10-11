import time
import unittest

from experimental_lab.tolerance_extraction_audit.common.scripts.timed_process_runner import (
    iter_timed_process_results,
)


def _sleep(seconds: float) -> float:
    time.sleep(seconds)
    return seconds


class TimedProcessRunnerTests(unittest.TestCase):
    def test_completed_and_timed_out_jobs_are_distinguished(self):
        results = list(iter_timed_process_results(
            _sleep,
            [("fast", (0.01,)), ("timeout", (0.5,))],
            timeout_seconds=0.2,
            workers=2,
        ))
        by_id = {result.task_id: result for result in results}
        self.assertEqual("COMPLETED", by_id["fast"].status)
        self.assertEqual("TIMEOUT", by_id["timeout"].status)
        self.assertEqual(0.01, by_id["fast"].value)


if __name__ == "__main__":
    unittest.main()
