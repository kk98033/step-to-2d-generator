# CAD Web System Concurrency Stress Report

Generated: 2026-10-20T02:27:47+0800

## 中文結論

- 本次使用 10 個獨立登入帳號；一般已驗證 API 共送出 200 次請求，成功 200 次、錯誤 0 次，吞吐量約 239.11 requests/s，p95 延遲 0.058 秒。
- 10 人在同一瞬間要求智慧標註時，目前容量控制只放行 1 人；其餘 9 人快速收到受控的 HTTP 429，沒有造成伺服器掛掉。
- 10 人依 0.5 秒間隔重試後全部完成，總耗時 5.943 秒，約 1.683 個標註工作/秒；過程共產生 45 次忙碌回應。這證明保護機制有效，但也顯示應將用戶端重試改為伺服器端標註佇列。
- 2 人同時上傳 489,713 bytes STEP 時，兩個分析工作全部完成；第一個約 10.818 秒完成，第二個經過 `queued -> processing -> completed`，兩件總耗時 20.172 秒。
- 重型負載期間執行 70 次健康探測，成功 70 次、失敗 0 次，可用率 100%；健康端點 p95 延遲 0.507 秒。
- 本次結果證明這台工作站至少可承受 10 名已登入使用者的一般操作，但沒有測出登入人數上限。現行重型容量仍是分析 1 worker、標註 1 slot，不能把它解讀成只能有一或兩名使用者登入。

## 建議

1. 智慧標註改成伺服器端 FIFO 佇列，回傳 job ID、排隊順位與進度，避免前端反覆收到 429。
2. 分析與標註改由獨立 worker process 執行，再測試 2、4 個 worker；不要只在同一 Python process 內提高執行緒數。
3. 正式部署硬體上以更大型模型執行 30–60 分鐘持續測試，加入 CPU、RAM、磁碟與 worker crash 指標後，才能定義正式 SLA 與最大使用人數。

## Executive summary

- Authenticated test users: 10
- Light API: 200/200 successful; 239.1083 requests/s; p95 0.057641 s.
- Annotation burst: {'capacity_busy': 9, 'success': 1}.
- Annotation retry-to-completion: {'success': 10}; 5.942563 s wall time; 1.6828 completed/s.
- Health during heavy load: 70/70 successful (100.0%); p95 0.506803 s.
- Analysis queue: {'completed': 2} for 2 uploads; 20.172074 s wall time.

## Interpretation

- The user count is not capped by the heavy-worker count. Light authenticated requests are measured separately.
- Annotation generation currently uses immediate backpressure: one request runs and excess requests receive HTTP 429, then clients must retry.
- Analysis uploads use a server queue and remain observable through queued, processing, finalizing, completed, or error states.
- This workstation result is a point-in-time safety/throughput measurement, not a production SLA.

## Raw phase metrics

```json
{
  "light_api": {
    "clients": 10,
    "requests_per_client": 20,
    "request_count": 200,
    "success": 200,
    "errors": 0,
    "wall_seconds": 0.836441,
    "requests_per_second": 239.1083,
    "latency_seconds": {
      "minimum": 0.008246,
      "mean": 0.039674,
      "p50": 0.037357,
      "p95": 0.057641,
      "maximum": 0.075521
    },
    "status_counts": {
      "200": 200
    }
  },
  "annotation_burst": {
    "clients": 10,
    "wall_seconds": 0.516644,
    "outcomes": {
      "capacity_busy": 9,
      "success": 1
    },
    "latency_seconds": {
      "minimum": 0.084295,
      "mean": 0.157417,
      "p50": 0.132297,
      "p95": 0.51447,
      "maximum": 0.51447
    },
    "results": [
      {
        "client": "stress-20261020022742-1",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.087795
      },
      {
        "client": "stress-20261020022742-10",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.134283
      },
      {
        "client": "stress-20261020022742-2",
        "status": 200,
        "outcome": "success",
        "elapsed": 0.51447
      },
      {
        "client": "stress-20261020022742-3",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.136132
      },
      {
        "client": "stress-20261020022742-4",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.084295
      },
      {
        "client": "stress-20261020022742-5",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.132667
      },
      {
        "client": "stress-20261020022742-6",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.135835
      },
      {
        "client": "stress-20261020022742-7",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.132297
      },
      {
        "client": "stress-20261020022742-8",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.084359
      },
      {
        "client": "stress-20261020022742-9",
        "status": 429,
        "outcome": "capacity_busy",
        "elapsed": 0.13204
      }
    ]
  },
  "annotation_retry": {
    "clients": 10,
    "retry_interval_seconds": 0.5,
    "wall_seconds": 5.942563,
    "completed_per_second": 1.6828,
    "outcomes": {
      "success": 10
    },
    "completion_seconds": {
      "minimum": 0.506469,
      "mean": 3.142862,
      "p50": 2.731545,
      "p95": 5.939899,
      "maximum": 5.939899
    },
    "total_attempts": 55,
    "total_busy_responses": 45,
    "results": [
      {
        "client": "stress-20261020022742-1",
        "outcome": "success",
        "attempts": 1,
        "busy_responses": 0,
        "completion_seconds": 0.506469
      },
      {
        "client": "stress-20261020022742-10",
        "outcome": "success",
        "attempts": 7,
        "busy_responses": 6,
        "completion_seconds": 4.132863
      },
      {
        "client": "stress-20261020022742-2",
        "outcome": "success",
        "attempts": 2,
        "busy_responses": 1,
        "completion_seconds": 1.111569
      },
      {
        "client": "stress-20261020022742-3",
        "outcome": "success",
        "attempts": 5,
        "busy_responses": 4,
        "completion_seconds": 2.731545
      },
      {
        "client": "stress-20261020022742-4",
        "outcome": "success",
        "attempts": 10,
        "busy_responses": 9,
        "completion_seconds": 5.939899
      },
      {
        "client": "stress-20261020022742-5",
        "outcome": "success",
        "attempts": 3,
        "busy_responses": 2,
        "completion_seconds": 1.637553
      },
      {
        "client": "stress-20261020022742-6",
        "outcome": "success",
        "attempts": 4,
        "busy_responses": 3,
        "completion_seconds": 2.146659
      },
      {
        "client": "stress-20261020022742-7",
        "outcome": "success",
        "attempts": 6,
        "busy_responses": 5,
        "completion_seconds": 3.262467
      },
      {
        "client": "stress-20261020022742-8",
        "outcome": "success",
        "attempts": 9,
        "busy_responses": 8,
        "completion_seconds": 5.296933
      },
      {
        "client": "stress-20261020022742-9",
        "outcome": "success",
        "attempts": 8,
        "busy_responses": 7,
        "completion_seconds": 4.662664
      }
    ]
  },
  "analysis_queue": {
    "clients": 2,
    "step_filename": "new_9b88a022_housing_assy_1fnkw6001h_a01_a_3_asm.stp",
    "step_bytes": 489713,
    "wall_seconds": 20.172074,
    "outcomes": {
      "completed": 2
    },
    "results": [
      {
        "client": "stress-20261020022742-1",
        "upload_status": 200,
        "outcome": "completed",
        "job_id": "260cc3e4-2388-468f-8388-114936642843",
        "initial_queue_position": 2,
        "worker_limit": 1,
        "state_history": [
          "processing",
          "completed"
        ],
        "completion_seconds": 10.818004,
        "final_message": "處理完成"
      },
      {
        "client": "stress-20261020022742-2",
        "upload_status": 200,
        "outcome": "completed",
        "job_id": "5ebc82a9-0d6b-49dd-8e22-f0d81cab5e8c",
        "initial_queue_position": 1,
        "worker_limit": 1,
        "state_history": [
          "queued",
          "processing",
          "completed"
        ],
        "completion_seconds": 20.171951,
        "final_message": "處理完成"
      }
    ]
  },
  "health_during_heavy_load": {
    "sample_count": 70,
    "success": 70,
    "failures": 0,
    "availability_percent": 100.0,
    "latency_seconds": {
      "minimum": 0.002597,
      "mean": 0.123358,
      "p50": 0.049493,
      "p95": 0.506803,
      "maximum": 0.67149
    }
  }
}
```

Passwords and session cookies are intentionally excluded from this report.
