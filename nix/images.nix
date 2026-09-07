{
  # Nix-managed OCI images for the BBP Actor Service.
  #
  # The staging image is safe by construction: it contains the star-bbpd
  # runtime and deterministic fake tool stubs only. Real reconnaissance
  # tools (subfinder, nmap, httpx, katana) are deliberately absent — the
  # staging variant of the package drops the tools PATH wrapper so no real
  # tool can enter the image closure. A production image with real tools
  # is a separate, explicitly reviewed follow-up.
  pkgs,
  python,
  starintelSchemaSrc,
  star-bbpd,
}:

let
  # Staging variant of the package: keep only the conformance-root
  # wrapper. overrideAttrs replaces the fully-resolved postFixup (which
  # in this nixpkgs is `wrapPythonPrograms` + the flake's own wrapper), so
  # the PYTHONPATH wrapping must be re-included here. Dropping the
  # `--prefix PATH : <real tools>` line keeps subfinder/nmap/httpx/katana
  # out of the image closure.
  star-bbpd-staging = star-bbpd.overrideAttrs (old: {
    postFixup = ''
      wrapPythonPrograms
      wrapProgram $out/bin/star-bbpd \
        --set STARINTEL_CONFORMANCE_ROOT ${starintelSchemaSrc}
    '';
  });

  # Deterministic fake tools for staging. Output is derived from the target
  # so repeated runs can never collide with earlier results. Implemented in
  # Python (shell is not present in the image); never touches the network.
  stagingTools = pkgs.runCommand "bbpd-staging-tools"
    {
      inherit python;
    }
    ''
      mkdir -p $out/opt/staging-tools
      cat > $out/opt/staging-tools/subfinder <<SH
      #!${python}/bin/python3
      import sys

      args = sys.argv[1:]
      domain = None
      i = 0
      while i < len(args):
          if args[i] == "-d" and i + 1 < len(args):
              domain = args[i + 1]
              i += 2
          else:
              i += 1
      if not domain:
          sys.exit(1)
      print("a." + domain)
      print("www." + domain)
      print("b." + domain)
      SH
      chmod 0755 $out/opt/staging-tools/subfinder
    '';

  stagingImageRoot = pkgs.buildEnv {
    name = "starintel-bbpd-staging-root";
    paths = [
      star-bbpd-staging
      stagingTools
      pkgs.cacert
      pkgs.tzdata
    ];
    pathsToLink = [ "/bin" "/opt" "/etc" "/share" ];
    ignoreCollisions = true;
  };

  bbpdStagingImage = pkgs.dockerTools.buildLayeredImage {
    name = "starintel/bbpd";
    tag = "0.1.0-staging";
    contents = [ stagingImageRoot ];
    extraCommands = ''
      mkdir -p tmp
      chmod 1777 tmp
    '';
    config = {
      Entrypoint = [ "/bin/star-bbpd" "/etc/starintel-bbpd/config.toml" ];
      Env = [
        "HOME=/tmp"
        "XDG_CACHE_HOME=/tmp/.cache"
      ];
      WorkingDir = "/tmp";
    };
  };

  # Production image: the default star-bbpd package with the real
  # reconnaissance tools on PATH. It is NOT built by default and must only
  # be promoted after staging verification; arming real scanning is an
  # explicit operator decision.
  bbpdProdImage = pkgs.dockerTools.buildLayeredImage {
    name = "starintel/bbpd";
    tag = "0.1.0";
    contents = [
      (pkgs.buildEnv {
        name = "starintel-bbpd-prod-root";
        paths = [
          star-bbpd
          pkgs.cacert
          pkgs.tzdata
        ];
        pathsToLink = [ "/bin" "/etc" "/share" ];
        ignoreCollisions = true;
      })
    ];
    extraCommands = ''
      mkdir -p tmp
      chmod 1777 tmp
    '';
    config = {
      Entrypoint = [ "/bin/star-bbpd" "/etc/starintel-bbpd/config.toml" ];
      Env = [
        "HOME=/tmp"
        "XDG_CACHE_HOME=/tmp/.cache"
      ];
      WorkingDir = "/tmp";
    };
  };

in
{
  inherit bbpdStagingImage;
  bbpdStagingTools = stagingTools;
  inherit bbpdProdImage;
}