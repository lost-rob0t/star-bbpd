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

        tools = with pkgs; [
          subfinder
          httpx
          katana
          nmap
        ];

        star-bbpd = pythonPackages.buildPythonPackage {
          pname = "star-bbpd";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = with pythonPackages; [
            setuptools
            setuptools-scm
          ] ++ [
            pkgs.makeWrapper
          ];

          propagatedBuildInputs = with pythonPackages; [
            pika
            pykka
            python-ulid
            tomli
            dataclasses-json
          ];

          dontCheckRuntimeDeps = true;

          postFixup = ''
            wrapProgram $out/bin/star-bbpd \
              --prefix PATH : ${pkgs.lib.makeBinPath tools}
          '';

          meta = with pkgs.lib; {
            description = "BBP Actor Service";
            license = licenses.mit;
          };
        };
      in
      {
        packages.default = star-bbpd;
        packages.star-bbpd = star-bbpd;

        devShells.default = pkgs.mkShell {
          buildInputs = with pythonPackages; [
            python
            pika
            pykka
            python-ulid
            tomli
            dataclasses-json
            pytest
            black
            flake8
            mypy
          ] ++ tools;
        };

        apps.default = {
          type = "app";
          program = "${star-bbpd}/bin/star-bbpd";
        };
      }
    );
}
