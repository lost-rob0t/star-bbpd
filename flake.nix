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

        starintelDocSrc = builtins.fetchGit {
          url = "https://github.com/lost-rob0t/starintel-doc.git";
          rev = "980f7a217a0d28f22508c2f05831ceb2fefe8bd0";
        };

        starintelSchemaSrc = builtins.fetchGit {
          url = "https://github.com/lost-rob0t/starintel-gpt-auto-dig.git";
          rev = "ff814ff63868286d68e21502122832802cd5e361";
        };

        tools = with pkgs; [
          subfinder
          httpx
          katana
          nmap
        ];

        ulid-py = pythonPackages.buildPythonPackage {
          pname = "ulid-py";
          version = "1.1.0";
          format = "setuptools";

          src = pkgs.fetchPypi {
            pname = "ulid-py";
            version = "1.1.0";
            hash = "sha256-3GiEvpFVjfB3wwEbn7DIfRCXy4/GU0sR8xAWGv1XOPA=";
          };

          nativeBuildInputs = with pythonPackages; [
            setuptools
          ];

          doCheck = false;
          pythonImportsCheck = [ "ulid" ];
        };

        starintel-doc-pkg = pythonPackages.buildPythonPackage {
          pname = "starintel_doc";
          version = "0.9.0";
          format = "pyproject";
          src = starintelDocSrc;

          nativeBuildInputs = with pythonPackages; [
            setuptools
          ];

          propagatedBuildInputs = with pythonPackages; [
            ulid-py
            dataclasses-json
            jsonschema
          ];

          doCheck = true;
          checkPhase = ''
            export STARINTEL_CONFORMANCE_ROOT=${starintelSchemaSrc}
            ${python.interpreter} -m unittest discover -s tests
          '';

          pythonImportsCheck = [ "starintel_doc" ];
        };

        star-bbpd = pythonPackages.buildPythonPackage {
          pname = "star-bbpd";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";

          nativeBuildInputs = with pythonPackages; [
            setuptools
            wheel
          ] ++ [
            pkgs.makeWrapper
          ];

          nativeCheckInputs = with pythonPackages; [
            pytest
          ];

          propagatedBuildInputs = with pythonPackages; [
            pika
            pykka
            ulid-py
            tomli
            dnspython
            starintel-doc-pkg
          ];

          doCheck = true;
          checkPhase = ''
            export STARINTEL_CONFORMANCE_ROOT=${starintelSchemaSrc}
            pytest -q
          '';

          pythonImportsCheck = [ "bbp.document_boundary" ];

          postFixup = ''
            wrapProgram $out/bin/star-bbpd \
              --prefix PATH : ${pkgs.lib.makeBinPath tools} \
              --set STARINTEL_CONFORMANCE_ROOT ${starintelSchemaSrc}
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
        checks.default = star-bbpd;

        devShells.default = pkgs.mkShell {
          buildInputs = with pythonPackages; [
            python
            pika
            pykka
            ulid-py
            tomli
            dnspython
            starintel-doc-pkg
            pytest
            ruff
            build
          ] ++ tools;

          shellHook = ''
            export STARINTEL_CONFORMANCE_ROOT=${starintelSchemaSrc}
          '';
        };

        apps.default = {
          type = "app";
          program = "${star-bbpd}/bin/star-bbpd";
        };
      }
    );
}
