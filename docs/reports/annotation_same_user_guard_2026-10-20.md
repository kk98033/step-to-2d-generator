# Smart Annotation Concurrency Benchmark

- Clients: 3
- Rounds: 1
- Requests: 3
- Wall time: 0.461193 s
- Successful requests/s: 2.1683
- Maximum overlapping successful requests: 1
- Success: 1
- Same-user conflicts: 2
- Capacity busy: 0
- Errors: 0
- Latency p50 / p95 / max: 0.062185 / 0.460226 / 0.460226 s

## Request details

| Client | Round | HTTP | Outcome | Latency (s) | Cached |
|---|---:|---:|---|---:|---|
| same-admin-session-3 | 1 | 200 | success | 0.460226 | False |
| same-admin-session-2 | 1 | 409 | same_user_conflict | 0.053469 | False |
| same-admin-session-1 | 1 | 409 | same_user_conflict | 0.062185 | False |
