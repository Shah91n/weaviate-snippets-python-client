# Vector Index Optimization Guide

Optimize your vector search performance by choosing the right index strategy for your dataset size and requirements.

---

## Vector Index Strategy

Your choice of index determines the needs of RAM, operational cost and the retrieval latency of your cluster.

| **Index Type** | **Production Use Case** | Size | **Retrieval Speed** |
| --- | --- | --- | --- |
| **HNSW** | **High Performance (Production Recommended)** | Optimized for Large datasets | **Fastest** |
| **Flat** | **Memory Saver** | Best for small datasets only. Performs brute-force linear scans; uses zero graph memory. | **Good** |
| **Dynamic** | Balanced | Starts as **Flat** for efficiency, auto-upgrades to **HNSW** at a threshold (Default: 10,000). | **Adaptive** |

> **Dynamic Indexing** requires `ASYNC_INDEXING=true` in your environment. This is a **one-way switch**; once a shard converts to HNSW, it will not revert to Flat even if the object count drops.

---

## Critical Environment Configuration

| Category | Variable | Optimization |
| --- | --- | --- |
| Persistence | `PERSISTENCE_HNSW_MAX_LOG_SIZE` | Default 500MiB. Set it close to your HNSW graph size (e.g. 1 GiB for a ~1 GiB graph) to speed up compaction. Note: This increases memory usage. |
| Deletions | `TOMBSTONE_DELETION_CONCURRENCY` | Default is already half your CPU cores. In large-core clusters, consider setting lower to prevent cleanup from consuming too many resources. For small-core clusters with heavy deletions, increase to speed up cleanup. |
| Deletions | `TOMBSTONE_DELETION_MIN_PER_CYCLE` | Default 0 (cleanup runs on any tombstone). For very large single-tenant shards, set to 100000 (100k) to skip small cycles. For multi-tenant, keep the default: the value applies to every shard, and tenants below it never get cleaned up. |
| Deletions | `TOMBSTONE_DELETION_MAX_PER_CYCLE` | Default: unlimited. For very large indexes, set to 10000000 (10 million) to cap the number of tombstones deleted per cycle and prevent resource overconsumption. |
| Global Defaults | `DEFAULT_QUANTIZATION` | Set to `rq-8` so all new collections use 8-bit RQ compression automatically. Valid values: `none`, `pq`, `sq`, `rq-1`, `rq-4`, `rq-8`, `bq`. |

Tombstones are markers for deleted objects in the HNSW index. They get cleaned up periodically (controlled by `cleanupIntervalSeconds`, default 300).

---

## The HNSW Tuning

Tuning HNSW is a balance between graph density (Recall) and traversal speed (Latency). The following are **starting points** and should be validated per dataset.

### ✅ The Production DOs

- `Set ef: -1`: Enables Dynamic ef (the default). Weaviate sets search depth to `limit × 8`, clamped between 100 and 500 (`dynamicEfFactor`, `dynamicEfMin`, `dynamicEfMax`).
    - For predictable recall requirements where you need static performance, set `ef` between `300–500` (test your specific dataset to find optimal value).
- **`maxConnections`**: Keep the default (32). Increase only if you need higher recall and can afford the extra memory. Lower it to save RAM, with a small recall cost.
- Bulk Import Performance: Use Async Indexing (`ASYNC_INDEXING=true`) to prevent graph construction from blocking data ingestion.

### ❌ The Production DON'Ts

- DON'T exceed `ef: 512`. It causes large latency penalties for negligible recall gains.

---

## High-Efficiency Compression

For production, **Rotational Quantization (RQ-8)** is the standard for compression.

- **RQ (Recommended):** Provides **98-99% recall** with a 4x reduction in vector RAM. It requires **no training phase**: compression starts from the first object.
- **PQ (Large Scale):** Use only for massive datasets (>1M) where custom segment tuning is needed. Compression starts only when a shard reaches `trainingLimit` objects (default 100,000). SQ works the same way.

---

## Availability & Restart Optimization

In production, rolling updates can cause search latency spikes if the new Pod hasn't finished loading its cache. Use these settings to ensure a Pod is only "Ready" once it's fully performant.

| **Environment Variable** | **Recommended Value** | **Why it's Critical** |
| --- | --- | --- |
| `HNSW_STARTUP_WAIT_FOR_VECTOR_CACHE` | Leave unset | Deprecated since v1.36.6. When unset, Weaviate waits for the vector cache before marking the Pod Ready, except for lazy-loaded shards, which skip the wait. |
| `LAZY_LOAD_SHARD_COUNT_THRESHOLD` / `LAZY_LOAD_SHARD_SIZE_THRESHOLD_GB` | Default (`1000` / `100`) | Multi-tenant collections only: shards lazy-load when a node has more than 1000 active tenant shards or more than 100GB of them, to reduce startup time. Single-tenant shards always load fully before the node is Ready. `DISABLE_LAZY_LOAD_SHARDS` is deprecated since v1.36.6. |
| `PERSISTENCE_HNSW_DISABLE_SNAPSHOTS` | Don't set (no-op since v1.39) | Snapshots capture a point-in-time state of the HNSW index to drastically reduce startup times. Instead of replaying the full commit log, Weaviate loads the snapshot and only replays the **delta** (changes since the last snapshot). Since v1.39, snapshots are always created and managed automatically. |

> Why this matters: A pod that is "up" but hasn't loaded its HNSW graph causes large latency spikes during restarts. There are two complementary mechanisms for restart optimization: cache warming and HNSW Snapshots.

- Weaviate manages snapshot creation automatically. No tuning is needed.
- A 10M object index drops from **70+ seconds → ~5 seconds** startup (~10–15x faster).
- If a snapshot fails to load, Weaviate **safely falls back** to full commit log replay.

---

## Developer Checklist

**Environment & Infrastructure:**

- [ ] **Persistence**: `PERSISTENCE_HNSW_MAX_LOG_SIZE=1024MiB` (or match HNSW graph size; adjust based on dataset).
- [ ] **Global Defaults:** `DEFAULT_QUANTIZATION=rq-8` (applies RQ compression to all new collections).
- [ ] **Snapshots**: Always on since v1.39. Don't set `PERSISTENCE_HNSW_DISABLE_SNAPSHOTS`.

**HNSW Configuration:**

- [ ] ef is set to -1 for dynamic optimization (or 300-500 for static predictable recall).
- [ ] maxConnections=32 (the default). Only raise it for recall.
- [ ] Never set ef > 512 (causes large latency penalties).

**Compression & Memory:**

- [ ] **Compression**: RQ is enabled for RAM efficiency (98-99% recall, 4x reduction).
- [ ] **Memory**: Vector cache is sized to fit the "hot" portion of the dataset.

**Deletions & Cleanup:**

- [ ] **Deletions**: `TOMBSTONE_DELETION_CONCURRENCY` at default (already half CPU cores) or lower for large clusters.
- [ ] For large single-tenant datasets, configure `TOMBSTONE_DELETION_MIN_PER_CYCLE` and `TOMBSTONE_DELETION_MAX_PER_CYCLE`. For multi-tenant, keep `MIN_PER_CYCLE` at the default.
