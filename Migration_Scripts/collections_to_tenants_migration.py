#!/usr/bin/env python3
"""
Merge several single collections that share the same schema into one multi-tenant collection:
each source collection becomes a tenant named after it.

The schema of the first source collection is used as the template for the new multi-tenant
collection. Objects keep their UUIDs and vectors, so nothing is re-vectorized. Source and target
can be the same instance (omit the --target-* options) or two different ones.

Install:
    pip install -U weaviate-client

Usage (same instance, all collections -> "Shops"):
    python collections_to_tenants_migration.py --source-host localhost --target-collection Shops

Only some collections:
    python collections_to_tenants_migration.py ... --collections ShopA ShopB

To another instance with an API key:
    export TARGET_WEAVIATE_API_KEY=...
    python collections_to_tenants_migration.py \
        --source-host localhost --target-collection Shops \
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
  - Cross-references are not migrated.
  - The source collections are not deleted.
  - Shard count and replication factor come from the target's defaults.
  - An existing target collection and existing tenants are reused and objects with the same UUID are
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
from weaviate.classes.tenants import Tenant
from weaviate.client import WeaviateClient
from weaviate.collections import Collection

logger = logging.getLogger("collections_to_tenants")
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


def multi_tenant_config(template: dict, name: str) -> dict:
    """The template's config, renamed and with multi-tenancy enabled."""
    config = dict(template, **{"class": name})
    config["multiTenancyConfig"] = {"enabled": True}
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
    parser.add_argument("--target-collection", required=True, help="name of the new multi-tenant collection")
    parser.add_argument("--collections", nargs="+",
                        help="collections to merge (default: all non-multi-tenant collections on the source)")
    parser.add_argument("--failed-file", default="failed_objects.json",
                        help="where to write failed objects (UUID and error), only if any fail")
    parser.add_argument("--header", action="append", default=[], metavar="NAME=VALUE",
                        help="extra request header, e.g. X-OpenAI-Api-Key=sk-...; repeatable")
    args = parser.parse_args()
    args.headers = dict(h.split("=", 1) for h in args.header)

    schema = fetch_schema(args, "source")
    names = args.collections or [
        n for n, c in schema.items()
        if n != args.target_collection and not (c.get("multiTenancyConfig") or {}).get("enabled")
    ]
    missing = [n for n in names if n not in schema]
    if missing:
        logger.error("not found on the source: %s", ", ".join(missing))
        return 1
    if not names:
        logger.error("no collections to merge")
        return 1

    template = schema[names[0]]
    template_props = {p["name"] for p in template.get("properties") or []}
    legacy_vector = not template.get("vectorConfig")
    logger.info("using '%s' as the schema template for '%s'", names[0], args.target_collection)

    source = connect(args, "source")
    target = connect(args, "target") if args.target_host else source
    try:
        server_side_batching = server_version(target) >= (1, 36)
        if target.collections.exists(args.target_collection):
            logger.info("%s: already exists on the target, reusing it", args.target_collection)
        else:
            target.collections.create_from_dict(multi_tenant_config(template, args.target_collection))
            logger.info("%s: created as a multi-tenant collection", args.target_collection)

        target_col = target.collections.use(args.target_collection)
        existing = target_col.tenants.get()
        results = {}
        for name in names:
            props = {p["name"] for p in schema[name].get("properties") or []}
            if props != template_props:
                logger.warning("%s: properties differ from the template '%s'", name, names[0])
            if name not in existing:
                target_col.tenants.create([Tenant(name=name)])
            results[name] = copy_objects(source.collections.use(name), target_col.with_tenant(name),
                                         f"{name} -> {args.target_collection}/{name}",
                                         legacy_vector, server_side_batching)
    finally:
        source.close()
        if target is not source:
            target.close()

    logger.info("summary:")
    for name, (copied, failures) in results.items():
        logger.info("  %s -> tenant %s: %d copied, %d failed", name, name, copied, len(failures))

    failures = [f for _, fs in results.values() for f in fs]
    if failures:
        write_failures(args.failed_file, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
