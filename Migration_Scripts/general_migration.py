#!/usr/bin/env python3
"""
Copy every collection (schema, objects, vectors, tenants) from one Weaviate instance to another.

Works between any two Weaviate deployments the machine can reach: open source (Docker, Kubernetes),
Weaviate on DigitalOcean, Weaviate Cloud, or anything else. Objects are read through the API and
re-inserted with their original UUIDs and vectors, so nothing is re-vectorized.

Install:
    pip install -U weaviate-client

Source and target can be any Weaviate (local, DigitalOcean, Weaviate Cloud, ...).

Usage, e.g. local open-source Weaviate -> remote Weaviate with an API key:
    export TARGET_WEAVIATE_API_KEY=...
    python general_migration.py \
        --source-host localhost \
        --target-host weaviate.example.com --target-secure \
        --target-http-port 443 --target-grpc-port 443

Only some collections:
    python general_migration.py ... --collections Articles Products

Vectorizer API keys (needed only if the target has to vectorize something):
    python general_migration.py ... --header X-OpenAI-Api-Key=sk-...

Weaviate Cloud: pass the cluster URL without https:// as the host, port 443 for both HTTP and gRPC,
--*-secure, and grpc-<host> as the gRPC host:
    --target-host abc.c0.europe-west3.gcp.weaviate.cloud \
    --target-grpc-host grpc-abc.c0.europe-west3.gcp.weaviate.cloud \
    --target-http-port 443 --target-grpc-port 443 --target-secure
(or replace connect() with weaviate.connect_to_weaviate_cloud(cluster_url=..., auth_credentials=Auth.api_key(...)))

Notes:
  - Both instances must expose gRPC (default port 50051) as well as HTTP.
  - Vectorizer modules used by the source collections must be enabled on the target.
  - Only HOT (active) tenants can be read. COLD or offloaded tenants are skipped; activate them first.
  - Cross-references are not migrated.
  - Shard count and replication factor come from the target's defaults. Other settings the source
    leaves unset also take the target's defaults, e.g. Weaviate Cloud compresses uncompressed
    vectors with RQ.
  - Existing collections and tenants on the target are reused and objects with the same UUID are
    overwritten, so the script is safe to re-run.
"""

import argparse
import json
import logging
import os
import sys

import httpx
import weaviate
from weaviate.classes.init import AdditionalConfig, Auth, Timeout
from weaviate.classes.tenants import Tenant, TenantActivityStatus
from weaviate.client import WeaviateClient
from weaviate.collections import Collection

HOT_STATUSES = (TenantActivityStatus.ACTIVE, TenantActivityStatus.HOT)

logger = logging.getLogger("migration")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-5s | %(message)s", "%H:%M:%S"))
logger.addHandler(_handler)


def add_connection_args(parser: argparse.ArgumentParser, side: str, required: bool = True) -> None:
    group = parser.add_argument_group(f"{side} instance")
    group.add_argument(f"--{side}-host", required=required, help="HTTP host, without http:// or https://")
    group.add_argument(f"--{side}-http-port", type=int, default=8080)
    group.add_argument(f"--{side}-grpc-host", help="defaults to the HTTP host")
    group.add_argument(f"--{side}-grpc-port", type=int, default=50051)
    group.add_argument(f"--{side}-secure", action="store_true", help="use HTTPS and TLS for gRPC")
    group.add_argument(
        f"--{side}-api-key",
        default=os.environ.get(f"{side.upper()}_WEAVIATE_API_KEY"),
        help=f"defaults to ${side.upper()}_WEAVIATE_API_KEY; omit for anonymous access",
    )


def connect(args: argparse.Namespace, side: str) -> WeaviateClient:
    def arg(name):
        return getattr(args, f"{side}_{name}")

    return weaviate.connect_to_custom(
        http_host=arg("host"),
        http_port=arg("http_port"),
        http_secure=arg("secure"),
        grpc_host=arg("grpc_host") or arg("host"),
        grpc_port=arg("grpc_port"),
        grpc_secure=arg("secure"),
        headers=args.headers,
        auth_credentials=Auth.api_key(arg("api_key")) if arg("api_key") else None,
        additional_config=AdditionalConfig(timeout=Timeout(init=60, query=240, insert=480)),
    )


def fetch_schema(args: argparse.Namespace, side: str) -> dict:
    """Return {collection name: raw config} straight from the REST API.

    The client's export_config().to_dict() loses some settings (multi-vector indexes come back
    disabled), so the schema is copied exactly as the server reports it.
    """
    def arg(name):
        return getattr(args, f"{side}_{name}")

    scheme = "https" if arg("secure") else "http"
    headers = {"Authorization": f"Bearer {arg('api_key')}"} if arg("api_key") else {}
    response = httpx.get(f"{scheme}://{arg('host')}:{arg('http_port')}/v1/schema", headers=headers, timeout=60)
    response.raise_for_status()
    return {c["class"]: c for c in response.json().get("classes") or []}


def server_version(client: WeaviateClient) -> tuple:
    version = client.get_meta()["version"].split("-")[0]
    return tuple(int(part) for part in version.split(".")[:2])


def copy_schema(target: WeaviateClient, config: dict) -> None:
    name = config["class"]
    if target.collections.exists(name):
        logger.info("%s: already exists on the target, reusing it", name)
        return

    # Shard layout and replication factor depend on the cluster size, so the target uses its own
    # defaults (some deployments enforce a minimum replication factor).
    config = dict(config)
    config.pop("shardingConfig", None)
    replication = dict(config.get("replicationConfig") or {})
    replication.pop("factor", None)
    config["replicationConfig"] = replication

    target.collections.create_from_dict(config)
    logger.info("%s: created on the target", name)


def copy_objects(source_col: Collection, target_col: Collection, label: str,
                 legacy_vector: bool, server_side_batching: bool) -> tuple:
    """Copy all objects; return (copied, failures) with one {migration, uuid, error} dict per failed object."""
    count = 0
    # Server-side batching (Weaviate >= 1.36) lets the server control the pace; older targets use dynamic batching.
    batching = target_col.batch.stream() if server_side_batching else target_col.batch.dynamic()
    with batching as batch:
        for obj in source_col.iterator(include_vector=True):
            batch.add_object(
                properties=obj.properties,
                uuid=obj.uuid,
                # Collections without named vectors take a single vector, not a dict.
                vector=obj.vector.get("default") if legacy_vector else obj.vector,
            )
            count += 1
            if count % 10_000 == 0:
                logger.info("%s: %d objects sent", label, count)

    failures = [{"migration": label, "uuid": str(f.object_.uuid), "error": f.message}
                for f in target_col.batch.failed_objects]
    if failures:
        logger.error("%s: %d of %d objects failed, first error: %s", label, len(failures), count, failures[0]["error"])
    logger.info("%s: %d objects copied", label, count - len(failures))
    return count - len(failures), failures


def write_failures(path: str, failures: list) -> None:
    """Save failed objects so they can be inspected, fixed and re-inserted from the source by UUID."""
    with open(path, "w") as f:
        json.dump(failures, f, indent=2)
    logger.error("%d failed objects written to %s", len(failures), path)


def migrate_collection(source: WeaviateClient, target: WeaviateClient, config: dict,
                       server_side_batching: bool) -> dict:
    name = config["class"]
    copy_schema(target, config)
    legacy_vector = not config.get("vectorConfig")
    source_col = source.collections.use(name)
    target_col = target.collections.use(name)
    result = {"copied": 0, "failures": [], "skipped_tenants": []}

    if not (config.get("multiTenancyConfig") or {}).get("enabled"):
        result["copied"], result["failures"] = copy_objects(
            source_col, target_col, name, legacy_vector, server_side_batching)
        return result

    tenants = source_col.tenants.get()
    hot = [t for t, info in tenants.items() if info.activity_status in HOT_STATUSES]
    result["skipped_tenants"] = [t for t in tenants if t not in hot]
    for tenant in result["skipped_tenants"]:
        logger.warning("%s/%s: tenant is %s, skipped (only HOT tenants can be migrated)",
                       name, tenant, tenants[tenant].activity_status.value)

    existing = target_col.tenants.get()
    new_tenants = [Tenant(name=t) for t in hot if t not in existing]
    if new_tenants:
        target_col.tenants.create(new_tenants)
    logger.info("%s: %d tenants to copy", name, len(hot))

    for tenant in hot:
        copied, failures = copy_objects(source_col.with_tenant(tenant), target_col.with_tenant(tenant),
                                        f"{name}/{tenant}", legacy_vector, server_side_batching)
        result["copied"] += copied
        result["failures"] += failures
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser, "source")
    add_connection_args(parser, "target")
    parser.add_argument("--collections", nargs="+", help="collections to copy (default: all)")
    parser.add_argument("--failed-file", default="failed_objects.json",
                        help="where to write failed objects (UUID and error), only if any fail")
    parser.add_argument("--header", action="append", default=[], metavar="NAME=VALUE",
                        help="extra request header, e.g. X-OpenAI-Api-Key=sk-...; repeatable")
    args = parser.parse_args()
    args.headers = dict(h.split("=", 1) for h in args.header)

    schema = fetch_schema(args, "source")
    names = args.collections or list(schema)
    missing = [n for n in names if n not in schema]
    if missing:
        logger.error("not found on the source: %s", ", ".join(missing))
        return 1

    with connect(args, "source") as source, connect(args, "target") as target:
        server_side_batching = server_version(target) >= (1, 36)
        logger.info("source %s, target %s, %d collections to copy",
                    source.get_meta()["version"], target.get_meta()["version"], len(names))

        results = {}
        for name in names:
            try:
                results[name] = migrate_collection(source, target, schema[name], server_side_batching)
            except Exception as e:
                logger.error("%s: migration failed: %s", name, e)
                results[name] = {"copied": 0, "failures": None, "skipped_tenants": []}

    logger.info("summary:")
    for name, r in results.items():
        if r["failures"] is None:
            logger.info("  %s: FAILED, see the error above", name)
            continue
        skipped = f", skipped tenants: {', '.join(r['skipped_tenants'])}" if r["skipped_tenants"] else ""
        logger.info("  %s: %d copied, %d failed%s", name, r["copied"], len(r["failures"]), skipped)

    failures = [f for r in results.values() for f in r["failures"] or []]
    if failures:
        write_failures(args.failed_file, failures)
    return 1 if failures or any(r["failures"] is None for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
