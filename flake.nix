{
  description = "Functional tests for cardano-node";

  inputs = {
    cardano-node = {
      url = "github:input-output-hk/cardano-node";
      inputs = {
        node-measured.follows = "cardano-node";
        membench.follows = "/";
      };

    };
    nixpkgs.follows = "cardano-node/nixpkgs";
    flake-utils = {
      url = "github:numtide/flake-utils";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, cardano-node }:
    flake-utils.lib.eachDefaultSystem
      (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python3 = pkgs.python3.override {
            packageOverrides = self: _: {
              allure = self.callPackage ./nix/allure.nix { };
              pytest-allure = self.callPackage ./nix/pytest-allure.nix { };
              pytest-select = self.callPackage ./nix/pytest-select.nix {};
              cardano-clusterlib = self.callPackage ./nix/cardano-clusterlib.nix { };
            };
          };
        in
        {
          devShells = rec {
            base = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ bash nix gnugrep gnumake gnutar coreutils git xz ];
            };
            python = pkgs.mkShell {
              nativeBuildInputs = with pkgs; with python39Packages; [ python39Full virtualenv pip matplotlib pandas requests xmltodict psutil GitPython pymysql postgresql wget curl psycopg2 assertpy ];
            };
            postgres = pkgs.mkShell {
              nativeBuildInputs = with pkgs; [ glibcLocales postgresql lsof procps wget ];
            };
            default = (
              cardano-node.devShells.${system}.devops or (
                # Compat with 1.34.1:
                (import (cardano-node + "/shell.nix") {
                  pkgs = cardano-node.legacyPackages.${system}.extend (self: prev: {
                    workbench-supervisord =
                      { useCabalRun, profileName, haskellPackages }:
                      self.callPackage (cardano-node + "/nix/supervisord-cluster")
                        {
                          inherit profileName useCabalRun haskellPackages;
                          workbench = self.callPackage (cardano-node + "/nix/workbench") { inherit useCabalRun; };
                        };
                  });
                }).devops
              )
            ).overrideAttrs (oldAttrs: rec {
              nativeBuildInputs = base.nativeBuildInputs ++ oldAttrs.nativeBuildInputs ++ [
                (python3.withPackages (ps: with ps; [
                  pytest
                  allure
                  cardano-clusterlib
                  pytest-allure
                  pytest-html
                  pytest-order
                  pytest-select
                  pytest_xdist
                  pyyaml
                  setuptools
                  filelock
                  pydantic
                  pylint
                  mypy
                  hypothesis
                  cbor2
                  requests
                  psycopg2
                ]))
              ];
            });
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
