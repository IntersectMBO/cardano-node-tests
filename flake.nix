{
  description = "Functional tests for cardano-node";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
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
          py3Pkgs = pkgs.python311Packages;
          py3Full = pkgs.python311Full;
        in
        {
          devShells = rec {
            base = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ bash coreutils curl git gnugrep gnumake gnutar jq xz ];
            };
            postgres = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ glibcLocales postgresql lsof procps ];
            };
            venv = pkgs.mkShell {
              nativeBuildInputs = base.nativeBuildInputs ++ postgres.nativeBuildInputs ++ [
                nodePkgs.cardano-cli
                nodePkgs.cardano-node
                nodePkgs.cardano-submit-api
                nodePkgs.bech32
                pkgs.poetry
                py3Full
                py3Pkgs.virtualenv
              ];
            };
            # Use 'venv' directly as 'default' and 'dev'
            default = venv;
            dev = venv;
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
