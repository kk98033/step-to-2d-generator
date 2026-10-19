# Smart Annotation Concurrency Benchmark

- Clients: 3
- Rounds: 1
- Requests: 3
- Wall time: 1.162453 s
- Successful requests/s: 0.8603
- Maximum overlapping successful requests: 1
- Success: 1
- Same-user conflicts: 0
- Capacity busy: 2
- Errors: 0
- Latency p50 / p95 / max: 0.06747 / 1.160945 / 1.160945 s

## Request details

| Client | Round | HTTP | Outcome | Latency (s) | Cached |
|---|---:|---:|---|---:|---|
| loadtest-20261020013742-3 | 1 | 429 | capacity_busy | 0.058439 | False |
| loadtest-20261020013742-1 | 1 | 429 | capacity_busy | 0.06747 | False |
| loadtest-20261020013742-2 | 1 | 200 | success | 1.160945 | False |
