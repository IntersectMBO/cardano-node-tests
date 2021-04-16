{ config ? {}
, sourcesOverride ? {}
, pkgs ? import ./nix {
    inherit config sourcesOverride;
  }
}: pkgs.cardanoNodeTestsShell
