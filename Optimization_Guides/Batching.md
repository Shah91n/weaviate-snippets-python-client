# 🚀 Batching Optimization Guide

This guide provides a production optimization for batching objects from 5 million to 100 million objects, using **server-side batching**.

---

## 1. Optimization Checklist

- [ ] Connectivity: Ensure gRPC (Port 50051) is enabled. Server-side batching runs over a gRPC stream; REST is a bottleneck for massive datasets.
- [ ] Batch Mode: Use **server-side batching** (`.stream()`). The server controls the batch size and slows the client down when it is under memory pressure.
- [ ] GOMEMLIMIT: Make sure it is set (or `LIMIT_RESOURCES=true`). Server-side batching uses it to apply backpressure.
- [ ] Error Handling: Always retrieve `collection.batch.failed_objects` after the batch context closes to capture individual object failures.

---

## 2. How Server-Side Batching Works

- The client opens a gRPC stream and sends objects; the **server decides the batch size** (100–1000, adjusted so each batch takes about 1 second to process) and tells the client to adjust it.
- When the server's heap reaches 50% of `GOMEMLIMIT`, it starts delaying acknowledgements (slowing the client down).
- At 90% it stops accepting new objects. The client re-queues them and waits up to 10 minutes for memory to free up; if it doesn't, the batch fails with an error.

---

## 3. Decision Tree: "Is Import Taking Too Long?"

Use this logic to isolate and solve ingestion bottlenecks. Determine if the delay is in Embedding or Indexing.

- Scenario A: Weaviate CPU is Saturated
    - *Cause:* The cluster is maxed out on indexing/compression tasks.
    - *Solution:* **Scale up**
- Scenario B: Embedding Time is the Bottleneck
    - *Cause:* Waiting on external API providers (e.g., OpenAI/Cohere).
    - *Solution:* Switch to a **pre-computed vector pipeline**, or switch to `.rate_limit()` to stay under the provider's limits.
- Scenario C: Import Is Being Throttled by the Server
    - *Cause:* Heap is above 50% of `GOMEMLIMIT`, so the server delays acknowledgements and slows the stream down.
    - *Solution:* Add memory, enable compression, or scale out.

---

## 4. Recommended Batch Snippet Template

This ensures that errors are captured correctly after the batch is fully flushed.

```python
try:
    with collection.batch.stream() as batch:
        for row in data_generator:
            batch.add_object(
                properties=row["props"],
                vector=row["vector"]
            )

    # Failure Retrieval
    failed_objs = collection.batch.failed_objects
    if failed_objs:
        print(f"Failed count: {len(failed_objs)}")
        for i, failed in enumerate(failed_objs[:5], 1):
            print(f"Error {i}: {failed.message}")

except Exception as e:
    print(f"Critical System Error: {e}")
```

---

## 5. Troubleshooting "X" vs "Y" Situations

| **Situation** | **Diagnosis** | **Fix** |
| --- | --- | --- |
| **Import slows down over time** | Server is applying backpressure (heap above 50% of `GOMEMLIMIT`). | Add memory, enable **Compression**, or scale out. |
| **Memory Errors (OOM)** | `GOMEMLIMIT` is not set, so the server cannot apply backpressure. | Set `GOMEMLIMIT` (or `LIMIT_RESOURCES=true`) and ensure **Compression** is enabled. |
| **Integration Model Errors** | Hitting OpenAI/API Rate Limits. | Switch to `collection.batch.rate_limit(requests_per_minute=X)`, where X is objects per minute. |
