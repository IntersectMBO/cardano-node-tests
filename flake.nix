{
  description = "Functional tests for cardano-node";

  inputs = {
    cardano-node = {
      url = "github:IntersectMBO/cardano-node";
    };
    nixpkgs.follows = "cardano-node/nixpkgs";
    flake-utils = {
      url = "github:numtide/flake-utils";
    };
  };

  outputs = { self, nixpkgs, flake-utils, cardano-node }:
    flake-utils.lib.eachDefaultSystem
      (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          py3pkgs = pkgs.python311Packages;
          py3Full = pkgs.python311Full;
        in
        {
          devShells = rec {
            base = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ bash coreutils curl git gnugrep gnumake gnutar jq xz ];
            };
            # TODO: can be removed once sync tests are fully moved to separate repo
            python = pkgs.mkShell {
              nativeBuildInputs = with pkgs; with python39Packages; [ python39Full virtualenv pip matplotlib pandas requests xmltodict psutil GitPython pymysql ];
            };
            postgres = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ glibcLocales postgresql lsof procps ];
            };
            venv = pkgs.mkShell {
              nativeBuildInputs = base.nativeBuildInputs ++ postgres.nativeBuildInputs ++ [
                cardano-node.packages.${system}.cardano-cli
                cardano-node.packages.${system}.cardano-node
                cardano-node.packages.${system}.cardano-submit-api
                cardano-node.packages.${system}.bech32
                py3Full
                py3pkgs.pip
                py3pkgs.virtualenv
              ];
            };
            # Use 'venv' directly as 'default' and 'dev'
            default = venv;
            dev = venv;
          };
        });

  # --- Flake Local Nix Configuration ----------------------------
  nixConfig = {
    # This sets the flake to use the IOG nix cache.
    # Nix should ask for permission before using it,
    # but remove it here if you do not want it to.
    extra-substituters = [ "https://cache.iog.io" ];
    extra-trusted-public-keys = [ "hydra.iohk.io:f/Ea+s+dFdN+3Y/G+FDgSq+a5NEWhJGzdjvKNGv0/EQ=" ];
    allow-import-from-derivation = "true";
  };
}
