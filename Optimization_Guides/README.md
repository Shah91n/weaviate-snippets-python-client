# Optimization Guides

A collection of comprehensive optimization guides for Weaviate Vector Database.

## 📚 Available Guides

### [Inverted Index Optimization Guide](./Inverted_Index.md)
Learn how to optimize inverted indexes in Weaviate for maximum performance and efficiency. This guide covers:
- Index types and their use cases
- Tokenization strategies
- Best practices and common pitfalls
- Real-world implementation examples

### [Vector Index Optimization Guide](./Vector_Index.md)
Improve vector search performance by choosing the right index strategy, tuning HNSW, and configuring compression and environment variables for production readiness.

### [Batching Optimization Guide](./Batching.md)
Production checklist for ingesting 5M–100M objects with server-side batching. Covers how server-side batching and memory backpressure work, a decision tree for bottlenecks, a batch template, and troubleshooting.

### [Query Agent Optimization Guide](./Query_Agent.md)
Essential best practices for maximizing Query Agent performance. Covers schema design, data type precision, property management with `view_properties`, and performance optimization strategies for collections with many properties.

### [Multi-Tenancy Optimization Guide](./Multi_Tenancy.md)
Architect, configure, and operate multi-tenant collections efficiently. Learn when to use multi-tenancy, manage tenant states for cost optimization, handle backups and data safety, enforce access control, and follow production best practices for scaling to millions of tenants.

### [Sharding Optimization Guide](./Sharding.md)
Understand sharding architecture decisions, immutable shard-count rules, startup lazy-loading behavior for multi-tenant collections, read-only protection, manual rebalancing controls, and production best practices for balanced clusters.

### [Memory Optimization & Scaling Guide](./Memory.md)
Learn practical strategies to size memory, control heap growth, and scale Weaviate safely. Covers Weaviate memory architecture, HNSW and vector cache sizing, the 1.5x rule, scaling triggers, and production checklists.

## 🎯 Purpose

These guides are designed to help developers:
- Understand complex indexing concepts
- Optimize database performance
- Avoid common configuration mistakes
- Implement best practices in production environments

## 📝 Contributing

If you have suggestions or improvements, feel free to open an issue or submit a pull request. All Contributions are welcome!
