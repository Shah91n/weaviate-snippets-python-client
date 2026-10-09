#!/usr/bin/env python3
"""
Split multi-tenant collections into single collections: every HOT tenant becomes its own
collection, named after the tenant.

The new collections copy the multi-tenant collection's schema with multi-tenancy turned off.
Objects keep their UUIDs and vectors, so nothing is re-vectorized. Source and target can be the
same instance (omit the --target-* options) or two different ones.

Naming: a tenant gets a collection with its own name (made a valid collection name if needed:
first letter uppercase, only letters, digits and _). If that name is taken, the new collection
is prefixed with the source collection (Acme -> Projects_Acme), then numbered (Projects_Acme_2).
Existing collections are never merged into, so every run creates new collections.

Install:
    pip install -U weaviate-client

Usage (same instance, every multi-tenant collection):
    python tenants_to_collections_migration.py --source-host localhost

Only some multi-tenant collections:
    python tenants_to_collections_migration.py ... --collections Projects

To another instance with an API key:
    export TARGET_WEAVIATE_API_KEY=...
    python tenants_to_collections_migration.py \
        --source-host localhost \
        --target-host weaviate.example.com --target-secure \
        --target-http-port 443 --target-grpc-port 443

Weaviate Cloud: pass the cluster URL without https:// as the host, port 443 for both HTTP and gRPC,
--*-secure, and grpc-<host> as the gRPC host:
    --source-host abc.c0.europe-west3.gcp.weaviate.cloud \
    --source-grpc-host grpc-abc.c0.europe-west3.gcp.weaviate.cloud \
    --source-http-port 443 --source-grpc-port 443 --source-secure
(or replace connect() with weaviate.connect_to_weaviate_cloud(cluster_url=..., auth_credentials=Auth.api_key(...)))

Notes:
  - Both instances must expose gRPC (default port 50051) as well as HTTP.
  - Vectorizer modules used by the source collections must be enabled on the target.
  - Only HOT (active) tenants can be read. COLD or offloaded tenants are skipped; activate them first.
  - Cross-references are not migrated.
  - The source collections and tenants are not deleted.
  - Shard count and replication factor come from the target's defaults.
"""

import argparse
import json
import logging
import os
import re
import sys

import httpx
import weaviate
from weaviate.classes.init import AdditionalConfig, Auth, Timeout
from weaviate.classes.tenants import TenantActivityStatus
from weaviate.client import WeaviateClient
from weaviate.collections import Collection

HOT_STATUSES = (TenantActivityStatus.ACTIVE, TenantActivityStatus.HOT)

logger = logging.getLogger("tenants_to_collections")
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


def to_collection_name(raw_name: str) -> str:
    """Sanitize a string into a valid collection name (^[A-Z][_0-9A-Za-z]*$)."""
    safe = re.sub(r"[^0-9A-Za-z_]", "_", raw_name)
    if not safe or not safe[0].isalpha():
        safe = "T_" + safe
    return safe[0].upper() + safe[1:]


def resolve_target_name(target: WeaviateClient, tenant: str, mt_name: str, used: set) -> str:
    """Return an unused collection name: the tenant name, else <collection>_<tenant>, else numbered."""
    def is_free(name):
        return name not in used and not target.collections.exists(name)

    base = to_collection_name(tenant)
    if base != tenant:
        logger.warning("tenant '%s' is not a valid collection name, using '%s'", tenant, base)
    candidate = base
    i = 1
    while not is_free(candidate):
        candidate = to_collection_name(f"{mt_name}_{tenant}" if i == 1 else f"{mt_name}_{tenant}_{i}")
        i += 1
    if candidate != base:
        logger.warning("name '%s' is already in use, creating '%s' instead", base, candidate)
    used.add(candidate)
    return candidate


def single_collection_config(mt_config: dict, name: str) -> dict:
    """The multi-tenant collection's config, renamed and with multi-tenancy disabled."""
    config = dict(mt_config, **{"class": name})
    config["multiTenancyConfig"] = {"enabled": False}
    # Shard layout and replication factor depend on the cluster size, so the target uses its own
    # defaults (some deployments enforce a minimum replication factor).
    config.pop("shardingConfig", None)
    replication = dict(config.get("replicationConfig") or {})
    replication.pop("factor", None)
    config["replicationConfig"] = replication
    return config


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser, "source")
    add_connection_args(parser, "target", required=False)
    parser.add_argument("--collections", nargs="+",
                        help="multi-tenant collections to split (default: all multi-tenant collections)")
    parser.add_argument("--failed-file", default="failed_objects.json",
                        help="where to write failed objects (UUID and error), only if any fail")
    parser.add_argument("--header", action="append", default=[], metavar="NAME=VALUE",
                        help="extra request header, e.g. X-OpenAI-Api-Key=sk-...; repeatable")
    args = parser.parse_args()
    args.headers = dict(h.split("=", 1) for h in args.header)

    schema = fetch_schema(args, "source")
    mt_names = [n for n, c in schema.items() if (c.get("multiTenancyConfig") or {}).get("enabled")]
    names = args.collections or mt_names
    not_mt = [n for n in names if n not in mt_names]
    if not_mt:
        logger.error("not multi-tenant collections on the source: %s", ", ".join(not_mt))
        return 1
    if not names:
        logger.error("no multi-tenant collections found on the source")
        return 1

    source = connect(args, "source")
    target = connect(args, "target") if args.target_host else source
    results = []
    try:
        server_side_batching = server_version(target) >= (1, 36)
        used = set()
        for mt_name in names:
            legacy_vector = not schema[mt_name].get("vectorConfig")
            mt_col = source.collections.use(mt_name)
            tenants = mt_col.tenants.get()
            hot = [t for t, info in tenants.items() if info.activity_status in HOT_STATUSES]
            logger.info("%s: %d tenants, %d HOT", mt_name, len(tenants), len(hot))
            for tenant, info in tenants.items():
                if tenant not in hot:
                    logger.warning("%s/%s: tenant is %s, skipped (only HOT tenants can be migrated)",
                                   mt_name, tenant, info.activity_status.value)
                    results.append((f"{mt_name}/{tenant}", None, 0, []))

            for tenant in hot:
                new_name = resolve_target_name(target, tenant, mt_name, used)
                target.collections.create_from_dict(single_collection_config(schema[mt_name], new_name))
                copied, failures = copy_objects(mt_col.with_tenant(tenant), target.collections.use(new_name),
                                                f"{mt_name}/{tenant} -> {new_name}", legacy_vector,
                                                server_side_batching)
                results.append((f"{mt_name}/{tenant}", new_name, copied, failures))
    finally:
        source.close()
        if target is not source:
            target.close()

    logger.info("summary:")
    for label, new_name, copied, failures in results:
        if new_name is None:
            logger.info("  %s: skipped (not HOT)", label)
        else:
            logger.info("  %s -> %s: %d copied, %d failed", label, new_name, copied, len(failures))

    failures = [f for *_, fs in results for f in fs]
    if failures:
        write_failures(args.failed_file, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
