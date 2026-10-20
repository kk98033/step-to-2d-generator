# CAD Annotation Process-Isolation Benchmark

- Generated: `2026-10-20T08:45:30.584137+00:00`
- STEP fixture: `D:\School\力致\app\step-to-2d-generator\auto_2d_drawing\output\BLADE_ASSY-1AC085000H-R01_37f6697b_batch\_parts\SHAFT-2AC085010H_1.stp`
- Candidate rules: `14`
- Python: `D:\School\力致\app\step-to-2d-generator\pyoccenv\python.exe`
- Overall: `PASS`

This benchmark runs each OpenCASCADE projection and smart-annotation render in its own OS process. It does not change the production API or the legacy dimension engine.

| Concurrent workers | Round | Successful jobs | Wall time (s) | Throughput (jobs/s) | API availability | Max API latency (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 2 | 1 | 2/2 | 1.85 | 1.08 | 100.0% | 0.110 |
| 2 | 2 | 2/2 | 1.78 | 1.12 | 100.0% | 0.066 |
| 2 | 3 | 2/2 | 1.74 | 1.15 | 100.0% | 0.018 |
| 3 | 1 | 3/3 | 1.98 | 1.51 | 100.0% | 0.019 |
| 3 | 2 | 3/3 | 1.94 | 1.55 | 100.0% | 0.027 |
| 3 | 3 | 3/3 | 1.97 | 1.52 | 100.0% | 0.019 |
| 4 | 1 | 4/4 | 1.97 | 2.03 | 100.0% | 0.020 |
| 4 | 2 | 4/4 | 1.98 | 2.02 | 100.0% | 0.021 |
| 4 | 3 | 4/4 | 1.93 | 2.07 | 100.0% | 0.018 |

## Conclusion

All requested rounds passed through 4 isolated workers while the API remained available.

Passing this controlled fixture proves crash containment and basic parallel execution, not the final user-capacity limit. Production rollout should begin below the measured ceiling and add a persistent queue, timeouts, cancellation, and larger-model soak tests.
