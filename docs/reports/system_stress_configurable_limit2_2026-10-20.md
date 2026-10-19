# CAD Web System Concurrency Stress Report

Generated: 2026-10-20T02:33:31+0800

## 可調式容量 2/2 驗證結論

- 管理員設定：模型分析同時上限 2、智慧標註同時上限 2；部署硬上限 4。
- 3 個不同帳號同時智慧標註時，2 個請求成功且重疊執行，1 個收到受控 HTTP 429，證明新設定不需重啟即可生效。
- 3 個不同帳號同時上傳 489,713 bytes STEP 時，2 件先進入 processing，第 3 件經過 `queued -> processing -> completed`；3 件全部完成，總耗時 29.230 秒。
- 一般已驗證 API 15/15 成功；重型測試期間健康探測 68/68 成功，可用率 100%。
- 測試用帳號已全部停用，報告未保存密碼或 Cookie。
- 容量調高會增加 CPU、RAM 與 OpenCASCADE 同時工作量；目前 2/2 已通過這個小型 STEP 與 3 條標註規則的測試，但大型模型仍應另外驗證。

## Executive summary

- Authenticated test users: 3
- Light API: 15/15 successful; 133.4225 requests/s; p95 0.0509 s.
- Annotation burst: {'capacity_busy': 1, 'success': 2}.
- Annotation retry-to-completion: {'success': 3}; 1.660492 s wall time; 1.8067 completed/s.
- Health during heavy load: 68/68 successful (100.0%); p95 0.826645 s.
- Analysis queue: {'completed': 3} for 3 uploads; 29.229873 s wall time.

## Interpretation

- The user count is not capped by the heavy-worker count. Light authenticated requests are measured separately.
- Annotation generation currently uses immediate backpressure: one request runs and excess requests receive HTTP 429, then clients must retry.
- Analysis uploads use a server queue and remain observable through queued, processing, finalizing, completed, or error states.
- This workstation result is a point-in-time safety/throughput measurement, not a production SLA.

## Raw phase metrics

```json
{
  "light_api": {
    "clients": 3,
    "requests_per_client": 5,
    "request_count": 15,
    "success": 15,
    "errors": 0,
    "wall_seconds": 0.112425,
    "requests_per_second": 133.4225,
    "latency_seconds": {
      "minimum": 0.00375,
      "mean": 0.019182,
      "p50": 0.012004,
      "p95": 0.0509,
      "maximum": 0.0509
    },
    "status_counts": {
      "200": 15
    }
  },
  "annotation_burst": {
    "clients": 3,
    "wall_seconds": 0.837461,
    "outcomes": {
      "capacity_busy": 1,
      "success": 2
    },
    "latency_seconds": {
      "minimum": 0.072645,
      "mean": 0.564097,
      "p50": 0.782894,
      "p95": 0.836751,
      "maximum": 0.836751
    },
    "results": [
      {
        "client": "capacity-20261020023329-1",
        "status": 200,
        "outcome": "success",
        "elapsed": 0.782894
      },
      {
        "client": "capacity-20261020023329-2",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.072645
      },
      {
        "client": "capacity-20261020023329-3",
        "status": 200,
        "outcome": "success",
        "elapsed": 0.836751
      }
    ]
  },
  "annotation_retry": {
    "clients": 3,
    "retry_interval_seconds": 0.5,
    "wall_seconds": 1.660492,
    "completed_per_second": 1.8067,
    "outcomes": {
      "success": 3
    },
    "completion_seconds": {
      "minimum": 0.739063,
      "mean": 1.082318,
      "p50": 0.847981,
      "p95": 1.65991,
      "maximum": 1.65991
    },
    "total_attempts": 5,
    "total_busy_responses": 2,
    "results": [
      {
        "client": "capacity-20261020023329-1",
        "outcome": "success",
        "attempts": 1,
        "busy_responses": 0,
        "completion_seconds": 0.847981
      },
      {
        "client": "capacity-20261020023329-2",
        "outcome": "success",
        "attempts": 1,
        "busy_responses": 0,
        "completion_seconds": 0.739063
      },
      {
        "client": "capacity-20261020023329-3",
        "outcome": "success",
        "attempts": 3,
        "busy_responses": 2,
        "completion_seconds": 1.65991
      }
    ]
  },
  "analysis_queue": {
    "clients": 3,
    "step_filename": "new_9b88a022_housing_assy_1fnkw6001h_a01_a_3_asm.stp",
    "step_bytes": 489713,
    "wall_seconds": 29.229873,
    "outcomes": {
      "completed": 3
    },
    "results": [
      {
        "client": "capacity-20261020023329-1",
        "upload_status": 200,
        "outcome": "completed",
        "job_id": "12cfb6ce-bbeb-43c4-9e43-4b9a2a8e7161",
        "initial_queue_position": 1,
        "worker_limit": 2,
        "state_history": [
          "processing",
          "completed"
        ],
        "completion_seconds": 19.931955,
        "final_message": "處理完成"
      },
      {
        "client": "capacity-20261020023329-2",
        "upload_status": 200,
        "outcome": "completed",
        "job_id": "92dd73af-4681-4fb3-b6b4-4dcf6a05fa79",
        "initial_queue_position": 1,
        "worker_limit": 2,
        "state_history": [
          "queued",
          "processing",
          "completed"
        ],
        "completion_seconds": 29.229729,
        "final_message": "處理完成"
      },
      {
        "client": "capacity-20261020023329-3",
        "upload_status": 200,
        "outcome": "completed",
        "job_id": "613a6b1c-8738-4039-90f7-76322889ad61",
        "initial_queue_position": 1,
        "worker_limit": 2,
        "state_history": [
          "processing",
          "finalizing",
          "completed"
        ],
        "completion_seconds": 21.583578,
        "final_message": "處理完成"
      }
    ]
  },
  "health_during_heavy_load": {
    "sample_count": 68,
    "success": 68,
    "failures": 0,
    "availability_percent": 100.0,
    "latency_seconds": {
      "minimum": 0.002398,
      "mean": 0.210028,
      "p50": 0.096626,
      "p95": 0.826645,
      "maximum": 1.256858
    }
  }
}
```

Passwords and session cookies are intentionally excluded from this report.
