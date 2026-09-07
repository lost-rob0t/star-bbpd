#!/usr/bin/env bash
# Generates the prod bbpd config ON the ingest host from that host's own
# secrets. Credentials never leave the Proxmox CT114. Runs as root on the
# ingest host: generate-prod-config.sh <output-path>
set -euo pipefail
out="${1:?usage: generate-prod-config.sh <output-path>}"
secrets="${BBPD_SECRETS_DIR:-/home/starintel/src/starintel-server/secrets}"
umask 177
RPW="$(cat "$secrets/rabbitmq_password")"
cat > "$out" <<EOF
[service]
name = "bbpd-live"
star_authority = "bbpd.starintel.actor"
log_level = "INFO"
json_pretty = false
publisher_pool_size = 3
tool_pool_size = 2
publisher_max_retries = 3
publisher_retry_delays_sec = [1, 2, 4]
publish_settlement_timeout_sec = 30
shutdown_timeout_sec = 60

[documents]
host = "rabbitmq"
port = 5672
vhost = "/"
username = "starintel"
password = "$RPW"
exchange = "documents"
exchange_type = "topic"
heartbeat_sec = 30

[events]
exchange = "events"
exchange_type = "topic"

[routing]
target_key = "actors.{actor}.new.target"
target_dispatch_key = "documents.target.dispatch.{actor}"
document_ingest_key = "documents.ingest.{dtype}"
document_update_key = "documents.updated.{dtype}"
document_new_key = "documents.new.{dtype}"
event_key = "event.{eventType}"

[actors.subfinder]
enabled = true
queue = "bbp.subfinder.targets"
prefetch = 3
emit_relations = true

[tools.subfinder]
bin = "subfinder"
timeout_sec = 300
publish_timeout_sec = 30
args = ["-silent"]
EOF
chmod 600 "$out"
echo "prod config written to $out (0600, host-local secrets only)"
