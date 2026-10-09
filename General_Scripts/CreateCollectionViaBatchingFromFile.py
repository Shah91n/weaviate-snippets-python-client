#!/usr/bin/env python3
"""
Create a collection vectorized with OpenAI and import a CSV file into it with batching.

Expected CSV columns (headers are case-insensitive):
    company_id, last_name, first_name, job_title, email_address, country, interaction_notes
Adjust PROPERTIES below for your own data.

Install:
    pip install -U weaviate-client

Usage (local open-source Weaviate):
    export OPENAI_API_KEY=sk-...
    python CreateCollectionViaBatchingFromFile.py --host localhost --collection Contacts --csv contacts.csv

Weaviate Cloud: pass the cluster URL without https:// as the host, port 443 for both HTTP and gRPC,
--secure, and grpc-<host> as the gRPC host:
    --host abc.c0.europe-west3.gcp.weaviate.cloud --grpc-host grpc-abc.c0.europe-west3.gcp.weaviate.cloud \
    --http-port 443 --grpc-port 443 --secure
(or replace connect() with weaviate.connect_to_weaviate_cloud(cluster_url=..., auth_credentials=Auth.api_key(...)))

Notes:
  - The OpenAI key is sent to Weaviate as a header and used by the text2vec-openai module, which
    must be enabled on the instance.
  - If the collection already exists it is reused, so the script can be run again to import more files.
    Importing the same file twice creates duplicates.
  - Failed rows are printed with their CSV line number and error. The exit code is 1 if any failed.
"""

import argparse
import csv
import os
import sys

import weaviate
from weaviate.classes.config import Configure, DataType, Property, Tokenization
from weaviate.classes.init import AdditionalConfig, Auth, Timeout
from weaviate.client import WeaviateClient

PROPERTIES = [
    "company_id",
    "last_name",
    "first_name",
    "job_title",
    "email_address",
    "country",
    "interaction_notes",
]


def connect(args: argparse.Namespace) -> WeaviateClient:
    return weaviate.connect_to_custom(
        http_host=args.host,
        http_port=args.http_port,
        http_secure=args.secure,
        grpc_host=args.grpc_host or args.host,
        grpc_port=args.grpc_port,
        grpc_secure=args.secure,
        headers={"X-OpenAI-Api-Key": args.openai_key} if args.openai_key else None,
        auth_credentials=Auth.api_key(args.api_key) if args.api_key else None,
        additional_config=AdditionalConfig(timeout=Timeout(init=60, query=120, insert=240)),
    )


def server_version(client: WeaviateClient) -> tuple:
    version = client.get_meta()["version"].split("-")[0]
    return tuple(int(part) for part in version.split(".")[:2])


def create_collection(client: WeaviateClient, name: str) -> None:
    if client.collections.exists(name):
        print(f"Collection '{name}' already exists, importing into it.")
        return
    client.collections.create(
        name,
        vector_config=Configure.Vectors.text2vec_openai(),
        properties=[Property(name=p, data_type=DataType.TEXT, tokenization=Tokenization.WORD) for p in PROPERTIES],
    )
    print(f"Collection '{name}' created.")


def import_csv(client: WeaviateClient, name: str, csv_path: str) -> int:
    """Import every CSV row; return the number of failed rows."""
    collection = client.collections.use(name)
    count = 0
    line_of = {}  # object UUID -> CSV line number, to report failures
    # Server-side batching (Weaviate >= 1.36) lets the server control the pace; older versions use dynamic batching.
    batching = collection.batch.stream() if server_version(client) >= (1, 36) else collection.batch.dynamic()

    with open(csv_path, newline="", encoding="utf-8") as file, batching as batch:
        reader = csv.DictReader(file)
        reader.fieldnames = [header.strip().lower() for header in reader.fieldnames or []]
        for line, row in enumerate(reader, start=2):  # line 1 is the header
            uuid = batch.add_object(properties={p: row.get(p) or "" for p in PROPERTIES})
            line_of[str(uuid)] = line
            count += 1

    failed = collection.batch.failed_objects
    for f in failed:
        print(f"  line {line_of.get(str(f.object_.uuid), '?')}: {f.message}")
    print(f"Imported {count - len(failed)} of {count} rows into '{name}', {len(failed)} failed.")
    return len(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True, help="HTTP host, without http:// or https://")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--grpc-host", help="defaults to the HTTP host")
    parser.add_argument("--grpc-port", type=int, default=50051)
    parser.add_argument("--secure", action="store_true", help="use HTTPS and TLS for gRPC")
    parser.add_argument("--api-key", default=os.environ.get("WEAVIATE_API_KEY"),
                        help="defaults to $WEAVIATE_API_KEY; omit for anonymous access")
    parser.add_argument("--openai-key", default=os.environ.get("OPENAI_API_KEY"), help="defaults to $OPENAI_API_KEY")
    parser.add_argument("--collection", required=True, help="collection to create or import into")
    parser.add_argument("--csv", required=True, help="path to the CSV file")
    args = parser.parse_args()

    with connect(args) as client:
        print(f"Connected to Weaviate {client.get_meta()['version']} (client {weaviate.__version__})")
        create_collection(client, args.collection)
        failed = import_csv(client, args.collection, args.csv)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
