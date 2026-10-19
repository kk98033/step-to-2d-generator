# CAD Web System Concurrency Stress Report

Generated: 2026-10-20T02:37:08+0800

## Executive summary

- Authenticated test users: 5
- Light API: 25/25 successful; 241.1496 requests/s; p95 0.031731 s.
- Annotation burst: {'capacity_busy': 4, 'success': 1}.
- Annotation retry-to-completion: {'success': 5}; 2.881701 s wall time; 1.7351 completed/s.
- Health during heavy load: 62/62 successful (100.0%); p95 0.405996 s.
- Analysis queue: {'completed': 2} for 2 uploads; 20.39622 s wall time.

## Interpretation

- The user count is not capped by the heavy-worker count. Light authenticated requests are measured separately.
- Annotation generation currently uses immediate backpressure: one request runs and excess requests receive HTTP 429, then clients must retry.
- Analysis uploads use a server queue and remain observable through queued, processing, finalizing, completed, or error states.
- This workstation result is a point-in-time safety/throughput measurement, not a production SLA.

## Raw phase metrics

```json
{
  "light_api": {
    "clients": 5,
    "requests_per_client": 5,
    "request_count": 25,
    "success": 25,
    "errors": 0,
    "wall_seconds": 0.10367,
    "requests_per_second": 241.1496,
    "latency_seconds": {
      "minimum": 0.004104,
      "mean": 0.017932,
      "p50": 0.015851,
      "p95": 0.031731,
      "maximum": 0.03597
    },
    "status_counts": {
      "200": 25
    }
  },
  "annotation_burst": {
    "clients": 5,
    "wall_seconds": 0.822954,
    "outcomes": {
      "capacity_busy": 4,
      "success": 1
    },
    "latency_seconds": {
      "minimum": 0.07891,
      "mean": 0.233866,
      "p50": 0.089341,
      "p95": 0.821998,
      "maximum": 0.821998
    },
    "results": [
      {
        "client": "sweep-20261020023705-1",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.089341
      },
      {
        "client": "sweep-20261020023705-2",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.091365
      },
      {
        "client": "sweep-20261020023705-3",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.087715
      },
      {
        "client": "sweep-20261020023705-4",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.07891
      },
      {
        "client": "sweep-20261020023705-5",
        "status": 200,
        "outcome": "success",
        "elapsed": 0.821998
      }
    ]
  },
  "annotation_retry": {
    "clients": 5,
    "retry_interval_seconds": 0.5,
    "wall_seconds": 2.881701,
    "completed_per_second": 1.7351,
    "outcomes": {
      "success": 5
    },
    "completion_seconds": {
      "minimum": 0.469187,
      "mean": 1.694835,
      "p50": 1.692641,
      "p95": 2.879656,
      "maximum": 2.879656
    },
    "total_attempts": 15,
    "total_busy_responses": 10,
    "results": [
      {
        "client": "sweep-20261020023705-1",
        "outcome": "success",
        "attempts": 4,
        "busy_responses": 3,
        "completion_seconds": 2.292214
      },
      {
        "client": "sweep-20261020023705-2",
        "outcome": "success",
        "attempts": 2,
        "busy_responses": 1,
        "completion_seconds": 1.140476
      },
      {
        "client": "sweep-20261020023705-3",
        "outcome": "success",
        "attempts": 5,
        "busy_responses": 4,
        "completion_seconds": 2.879656
      },
      {
        "client": "sweep-20261020023705-4",
        "outcome": "success",
        "attempts": 1,
        "busy_responses": 0,
        "completion_seconds": 0.469187
      },
      {
        "client": "sweep-20261020023705-5",
        "outcome": "success",
        "attempts": 3,
        "busy_responses": 2,
        "completion_seconds": 1.692641
      }
    ]
  },
  "analysis_queue": {
    "clients": 2,
    "step_filename": "new_9b88a022_housing_assy_1fnkw6001h_a01_a_3_asm.stp",
    "step_bytes": 489713,
    "wall_seconds": 20.39622,
    "outcomes": {
      "completed": 2
    },
    "results": [
      {
        "client": "sweep-20261020023705-1",
        "upload_status": 200,
        "outcome": "completed",
        "job_id": "dec66124-af55-4ac7-bf99-abddb9b9cd50",
        "initial_queue_position": 1,
        "worker_limit": 1,
        "state_history": [
          "queued",
          "processing",
          "completed"
        ],
        "completion_seconds": 20.396098,
        "final_message": "處理完成"
      },
      {
        "client": "sweep-20261020023705-2",
        "upload_status": 200,
        "outcome": "completed",
        "job_id": "f0cc5c25-ae08-41c8-8abe-a8f0a85142d2",
        "initial_queue_position": 1,
        "worker_limit": 1,
        "state_history": [
          "processing",
          "completed"
        ],
        "completion_seconds": 11.19236,
        "final_message": "處理完成"
      }
    ]
  },
  "health_during_heavy_load": {
    "sample_count": 62,
    "success": 62,
    "failures": 0,
    "availability_percent": 100.0,
    "latency_seconds": {
      "minimum": 0.002638,
      "mean": 0.133136,
      "p50": 0.064613,
      "p95": 0.405996,
      "maximum": 1.101944
    }
  }
}
```

Passwords and session cookies are intentionally excluded from this report.
