{
  description = "BBP Actor Service - Python project with pika and pykka";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;
        pythonPackages = python.pkgs;

        star-bbpd = pythonPackages.buildPythonPackage {
          pname = "star-bbpd";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = with pythonPackages; [
            setuptools
            setuptools-scm
          ];

          propagatedBuildInputs = with pythonPackages; [
            pika
            pykka
            ulid
            tomli
          ];

          meta = with pkgs.lib; {
            description = "BBP Actor Service";
            license = licenses.mit;
          };
        };

      in
      {
        packages = {
          default = star-bbpd;
          star-bbpd = star-bbpd;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = with pythonPackages; [
            python
            pika
            pykka
            ulid
            tomli
            # Development tools
            pytest
            black
            flake8
            mypy
          ];

          shellHook = ''
            echo "BBP Development Environment"
            echo "Python: ${python.version}"
            echo ""
            echo "Available commands:"
            echo "  python bbp/main.py  - Run the stub application"
            echo "  pytest              - Run tests"
            echo "  black .             - Format code"
          '';
        };

        apps.default = {
          type = "app";
          program = "${star-bbpd}/bin/star-bbpd";
        };
      }
    );
}
