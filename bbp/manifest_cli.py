from bbp.manifest_catalog import register_extended_manifests
from bbp.manifests import main as manifests_main


def main() -> None:
    register_extended_manifests()
    manifests_main()


if __name__ == "__main__":
    main()
