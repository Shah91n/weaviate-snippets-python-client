# Sharding Optimization Guide

This guide is designed for developers building Weaviate schema. It focuses on the architectural logic of sharding.

---

## 1. Core Concepts: Sharding vs. Replication

Understanding the difference is critical for a healthy cluster. While both distribute data, they serve different masters:

- **Sharding (Efficiency):** Splits your data into pieces across nodes. This allows you to use the combined **memory** and CPU of multiple nodes.
- **Replication (Availability):** Creates identical copies of those shards. If one node fails, another has the data. The default replication factor is **1**. Setting the **`REPLICATION_MINIMUM_FACTOR`** environment variable (e.g., 3 for high availability) makes it the default for new collections and rejects any collection created with a lower factor. The replication factor cannot exceed the number of nodes.

> **Memory note:** Sharding only reduces memory per node when **nodes > replication factor**. With RF=3 on 3 nodes, every node holds a full copy of all data.

---

## 2. The "Immutable" Rules

In Weaviate, some schema decisions are permanent. Once a collection is created, you **cannot** change the following:

- **The Shard Count (`desiredCount`):** Re-sharding is **not supported yet,** you cannot change the shard count after collection creation. If you start with 3 shards and realize you need more due to the number of nodes, you must create a new collection and re-import your data.

> **Developer Tip:** Always plan your shard count based on your expected peak node count, not just your starting size.

---

## 3. Default Behavior

By default, Weaviate tries to be helpful. When you create a collection without specific sharding instructions:

1. It counts the number of nodes currently in your cluster.
2. It sets the **Shard Count** equal to that node count.
3. It assigns 128 virtual shards per physical shard to ensure even data distribution.

> **Multi-tenancy:** Each tenant is its own shard, so `desiredCount` does not apply.

---

## 4. Startup

Weaviate auto-detects shard loading mode per collection at startup. This applies to **multi-tenant collections only**. By default, single-tenant collections are fully loaded before the node is ready.

- Shards are eagerly loaded by default until a collection crosses one threshold on that node.
- Thresholds:
    - **`LAZY_LOAD_SHARD_COUNT_THRESHOLD`** (default: more than `1000` active tenants on the node)
    - **`LAZY_LOAD_SHARD_SIZE_THRESHOLD_GB`** (default: more than `100` GB total size of those tenants)
- Once either threshold is crossed, that collection flips to lazy loading automatically.

---

## 5. Optimization & Resource Management

### The Disk vs. Memory Gap

There is a specific path in how Weaviate places data:

- Placement Logic: Weaviate uses a disk-aware round-robin approach. It sorts nodes by most free disk space first, then distributes shards one-by-one across that list.
- The Reality: It's a fair spread, not a "fill the emptiest node" policy. Placement happens only when a collection (or tenant) is created. Existing shards never move automatically, which is why they don't jump to a brand-new, empty node.

### Handling High Resource Pressure (Shards)

When a node runs low on resources, Weaviate takes protective measures:

- **Read-Only Mode:** Shards on that node are flipped to read-only to prevent crashes.
    - **Disk:** on by default at 90% disk usage (`DISK_USE_READONLY_PERCENTAGE`, warning at 80%).
    - **Memory:** off by default. Enable it with `MEMORY_READONLY_PERCENTAGE` (warning at 80% via `MEMORY_WARNING_PERCENTAGE`). The percentage is of `GOMEMLIMIT`, so `GOMEMLIMIT` must be set.
    - Shards return to normal automatically once usage drops below the thresholds.

---

## 6. Manual Rebalancing

If your cluster becomes unbalanced (e.g., some nodes are doing all the work while others are idle), you can manually move data.

**Execute Movement:** You can explicitly move a specific shard from a **`Source Node`** to a **`Target Node`** but only if the **`REPLICA_MOVEMENT_ENABLED=true`** environment variable is set. Without this flag, replica movement endpoints return HTTP 501 Not Implemented. Replica movement allows you to manually move or copy individual shard replicas between nodes in a Weaviate cluster.

---

## 7. Best Practices Checklist

- [ ] **Set Shard Count Early:** Match it to your intended cluster scale.
- [ ] **Monitor Nodes Verbose Output:** Use the nodes API regularly to check the `shardCount` and `objectCount` on each physical node.
- [ ] **Check Disk & RAM:** Don't just watch the disk; ensure your RAM is sufficient for the vectors stored on those shards.
- [ ] **Enable Memory Read-Only Protection:** Set `MEMORY_READONLY_PERCENTAGE` (and `GOMEMLIMIT`) if you want writes to stop before a node runs out of memory.
