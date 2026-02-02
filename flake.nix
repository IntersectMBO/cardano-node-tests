{
  description = "Functional tests for cardano-node";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    cardano-node = {
      url = "github:IntersectMBO/cardano-node";
    };
    flake-utils = {
      url = "github:numtide/flake-utils";
    };
  };

  outputs = { self, nixpkgs, flake-utils, cardano-node }:
    flake-utils.lib.eachDefaultSystem
      (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          nodePkgs = cardano-node.packages.${system};
        in
        {
          devShells = rec {
            base = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ bash coreutils curl git gnugrep gnutar jq xz ];
            };
            postgres = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ glibcLocales postgresql lsof procps ];
            };
            testenv = pkgs.mkShell {
              nativeBuildInputs = base.nativeBuildInputs ++ postgres.nativeBuildInputs ++ [
                pkgs.uv
                pkgs.python313
              ];
            };
            dev = pkgs.mkShell {
              nativeBuildInputs = testenv.nativeBuildInputs ++ [
                nodePkgs.cardano-cli
                nodePkgs.cardano-node
                nodePkgs.cardano-submit-api
                nodePkgs.bech32
                pkgs.bashInteractive
              ];
            };
            default = dev;
          };
        });

  # --- Flake Local Nix Configuration ----------------------------
  nixConfig = {
    # Sets the flake to use the IOG nix cache.
    extra-substituters = [ "https://cache.iog.io" ];
    extra-trusted-public-keys = [ "hydra.iohk.io:f/Ea+s+dFdN+3Y/G+FDgSq+a5NEWhJGzdjvKNGv0/EQ=" ];
    allow-import-from-derivation = "true";
  };
}
