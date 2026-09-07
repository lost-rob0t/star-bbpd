# Gated promotion of the verified staging bbpd actor to the Proxmox ingest
# host (CT114 `starintel`, 10.60.60.248, network starintel_backend).
#
# Promoted means: the prod image (real reconnaissance tools) is built from
# this checkout, shipped, loaded, and run on the ingest host joining the
# existing starintel compose network. Broker credentials never leave the
# ingest host: the prod config is generated there from that host's own
# secrets by deploy/generate-prod-config.sh.
#
# Usage:
#   nix run .#promote-bbpd -- --arm-real-recon
#
# Overrides:
#   BBPD_PROMOTE_TARGET   ssh target (default: starintel)
#   BBPD_PROMOTE_DIR      remote dir  (default: /home/starintel/src/star-bbpd)
{ pkgs }:
pkgs.writeShellApplication {
  name = "promote-bbpd";
  runtimeInputs = with pkgs; [
    coreutils
    docker
    gawk
    gnugrep
    nix
    openssh
  ];
  text = ''
    set -euo pipefail

    TARGET="''${BBPD_PROMOTE_TARGET:-starintel}"
    REMOTE_DIR="''${BBPD_PROMOTE_DIR:-/home/starintel/src/star-bbpd}"

    if [ ! -f flake.nix ] || [ ! -f deploy/compose.yml ]; then
      echo "REFUSED: run from a star-bbpd checkout (flake.nix + deploy/compose.yml required)" >&2
      exit 2
    fi

    ARM=0
    for arg in "$@"; do
      case "$arg" in
        --arm-real-recon) ARM=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
      esac
    done

    if [ "$ARM" -ne 1 ]; then
      echo "REFUSED: promotion arms REAL reconnaissance tools (subfinder) on the" >&2
      echo "Proxmox ingest host. Pass --arm-real-recon after staging verification." >&2
      exit 2
    fi

    echo "== Gate 1: staging eval verified"
    state="$(docker inspect starintel-bbpd-staging-1 --format '{{.State.Status}}' 2>/dev/null || true)"
    if [ "$state" != "running" ]; then
      echo "REFUSED: staging bbpd not running (state=''${state:-absent})" >&2
      exit 2
    fi
    simg="$(docker inspect starintel-bbpd-staging-1 --format '{{.Config.Image}}')"
    if [ "$simg" != "starintel/bbpd:0.1.0-staging" ]; then
      echo "REFUSED: unexpected staging image: $simg" >&2
      exit 2
    fi

    echo "== Gate 2: ingest host healthy"
    if ! ssh "$TARGET" 'docker ps --format "{{.Names}}"' | grep -q starintel-rabbitmq-1; then
      echo "REFUSED: starintel stack not running on $TARGET" >&2
      exit 2
    fi

    echo "== Building prod image"
    img="$(nix build .#star-bbpd-prod-image --print-out-paths)"

    echo "== Shipping image and compose to $TARGET"
    remote_cmd="mkdir -p '$REMOTE_DIR'"
    # shellcheck disable=SC2029
    ssh "$TARGET" "$remote_cmd"
    scp -q "$img" "$TARGET:/tmp/bbpd-prod-image.tar.gz"
    scp -q deploy/compose.yml "$TARGET:$REMOTE_DIR/compose.yml"
    scp -q deploy/generate-prod-config.sh "$TARGET:$REMOTE_DIR/generate-prod-config.sh"

    echo "== Loading image and generating prod config on $TARGET"
    remote_cmd="set -e
      docker load -i /tmp/bbpd-prod-image.tar.gz
      rm -f /tmp/bbpd-prod-image.tar.gz
      sh '$REMOTE_DIR/generate-prod-config.sh' '$REMOTE_DIR/config.prod.toml'"
    # shellcheck disable=SC2029
    ssh "$TARGET" "$remote_cmd"

    echo "== Deploying prod bbpd on $TARGET"
    remote_cmd="cd '$REMOTE_DIR' && \
      STARINTEL_ACTOR_PROJECT=starintel-bbpd \
      ACTOR_IMAGE=starintel/bbpd:0.1.0 \
      ACTOR_CONTAINER=starintel-bbpd-1 \
      ACTOR_HOSTNAME=bbpd \
      ACTOR_CONFIG='$REMOTE_DIR/config.prod.toml' \
      docker compose -f compose.yml up -d"
    # shellcheck disable=SC2029
    ssh "$TARGET" "$remote_cmd"

    echo "== Verifying"
    sleep 5
    rstate="$(ssh "$TARGET" 'docker inspect starintel-bbpd-1 --format "{{.State.Status}}"')"
    if [ "$rstate" != "running" ]; then
      echo "PROMOTION FAILED: prod container state: $rstate" >&2
      exit 1
    fi
    consumers="$(ssh "$TARGET" 'docker exec starintel-rabbitmq-1 rabbitmqctl list_queues name consumers --quiet' \
      | awk '$1=="bbp.subfinder.targets"{print $2}')"
    echo "prod bbpd on $TARGET: state=$rstate consumers=''${consumers:-0}"
    echo "PROMOTED: prod bbpd running on the Proxmox ingest host"
  '';
}
