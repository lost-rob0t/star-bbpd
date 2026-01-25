{
  description = "BBP Actor Service - Python project with pika and pykka";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    starintel-doc = {
      url = "github:lost-rob0t/starintel-doc";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, starintel-doc }:
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

        # Rebuild starintel-doc with our Python version
        starintel-doc-pkg = pythonPackages.buildPythonPackage rec {
          pname = "starintel_doc";
          version = "0.8.2";
          format = "setuptools";

          src = starintel-doc;

          propagatedBuildInputs = with pythonPackages; [
            python-ulid
            dataclasses-json
          ];

          doCheck = false;

          meta = with pkgs.lib; {
            description = "Document Spec for Star intel";
            homepage = "https://github.com/lost-rob0t/starintel_doc";
            license = licenses.mit;
          };
        };

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
            dnspython
            starintel-doc-pkg
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
            dnspython
            starintel-doc-pkg
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
