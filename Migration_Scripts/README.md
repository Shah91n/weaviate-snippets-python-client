# Migration Scripts

Copy data between Weaviate instances (open source, DigitalOcean, Weaviate Cloud, ...). Objects keep their UUIDs and vectors, so nothing is re-vectorized. Each script has a matching notebook (`.ipynb`) with the same logic and a config cell instead of command-line options.

| Script | What it does |
|---|---|
| `general_migration.py` | Copy all (or selected) collections, with schema, objects, vectors and tenants, to another instance |
| `collections_to_tenants_migration.py` | Merge same-schema collections into one multi-tenant collection, one tenant per collection |
| `tenants_to_collections_migration.py` | Split multi-tenant collections into one collection per tenant |

## Usage

```bash
pip install -U weaviate-client
export SOURCE_WEAVIATE_API_KEY=...   # omit for anonymous access
export TARGET_WEAVIATE_API_KEY=...

# Local open-source Weaviate -> remote Weaviate
python general_migration.py \
  --source-host localhost \
  --target-host weaviate.example.com --target-secure --target-http-port 443 --target-grpc-port 443

python general_migration.py ... --collections Articles Products     # only some collections
python collections_to_tenants_migration.py --source-host localhost --target-collection Shops
python tenants_to_collections_migration.py --source-host localhost --collections Projects
```

Run any script with `--help` for all options. The two tenant scripts work within one instance unless `--target-*` options are given.

**Weaviate Cloud:** use the cluster URL without `https://` as the host, `grpc-<host>` as the gRPC host, port 443 for both, and `--*-secure`:

```bash
--source-host abc.c0.europe-west3.gcp.weaviate.cloud \
--source-grpc-host grpc-abc.c0.europe-west3.gcp.weaviate.cloud \
--source-http-port 443 --source-grpc-port 443 --source-secure
```

## Good to know

- Both instances must expose gRPC (default port 50051) as well as HTTP.
- Vectorizer modules used by the source collections must be enabled on the target.
- Only HOT tenants are migrated; COLD or offloaded tenants are skipped with a warning, so activate them first.
- Cross-references are not migrated.
- Shard count, replication factor and anything else the source leaves unset follow the target's defaults.
- Re-running `general_migration.py` is safe: existing collections are reused and objects with the same UUID are overwritten.
- Objects that fail are written to `failed_objects.json` (`--failed-file` to change the path) with their UUID and error, so you can fix the cause and re-insert them from the source by UUID. Notebooks print them instead. The exit code is 1 if anything failed.
