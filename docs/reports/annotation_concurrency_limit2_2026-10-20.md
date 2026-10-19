# Smart Annotation Concurrency Benchmark

- Clients: 3
- Rounds: 1
- Requests: 3
- Wall time: 1.649034 s
- Successful requests/s: 1.2128
- Maximum overlapping successful requests: 2
- Success: 2
- Same-user conflicts: 0
- Capacity busy: 1
- Errors: 0
- Latency p50 / p95 / max: 1.538832 / 1.64771 / 1.64771 s

## Request details

| Client | Round | HTTP | Outcome | Latency (s) | Cached |
|---|---:|---:|---|---:|---|
| limit2-20261020023319-3 | 1 | 429 | capacity_busy | 0.116182 | False |
| limit2-20261020023319-1 | 1 | 200 | success | 1.64771 | False |
| limit2-20261020023319-2 | 1 | 200 | success | 1.538832 | False |
