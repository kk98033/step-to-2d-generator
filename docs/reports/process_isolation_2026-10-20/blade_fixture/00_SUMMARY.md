# CAD Annotation Process-Isolation Benchmark

- Generated: `2026-10-20T08:46:25.443256+00:00`
- STEP fixture: `D:\School\力致\app\step-to-2d-generator\auto_2d_drawing\output\BLADE_ASSY-1AC085000H-R01_37f6697b_batch\_parts\BLADE-AC08_1.stp`
- Candidate rules: `147`
- Python: `D:\School\力致\app\step-to-2d-generator\pyoccenv\python.exe`
- Overall: `PASS`

This benchmark runs each OpenCASCADE projection and smart-annotation render in its own OS process. It does not change the production API or the legacy dimension engine.

| Concurrent workers | Round | Successful jobs | Wall time (s) | Throughput (jobs/s) | API availability | Max API latency (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 2 | 1 | 2/2 | 5.52 | 0.36 | 100.0% | 0.018 |
| 2 | 2 | 2/2 | 5.37 | 0.37 | 100.0% | 0.026 |
| 3 | 1 | 3/3 | 5.43 | 0.55 | 100.0% | 0.020 |
| 3 | 2 | 3/3 | 5.46 | 0.55 | 100.0% | 0.029 |
| 4 | 1 | 4/4 | 5.53 | 0.72 | 100.0% | 0.020 |
| 4 | 2 | 4/4 | 5.63 | 0.71 | 100.0% | 0.019 |

## Conclusion

All requested rounds passed through 4 isolated workers while the API remained available.

Passing this controlled fixture proves crash containment and basic parallel execution, not the final user-capacity limit. Production rollout should begin below the measured ceiling and add a persistent queue, timeouts, cancellation, and larger-model soak tests.
