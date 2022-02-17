{ system ? builtins.currentSystem
, crossSystem ? null
, config ? {}
, sourcesOverride ? {}
, ...
}:

let

  sources = import ./sources.nix { inherit pkgs; } // sourcesOverride;

  cardanoNodePkgs = import (sources.cardano-node + "/nix") { inherit system crossSystem config; };

  pkgs = cardanoNodePkgs.extend (self: super: {

      python3 = super.python3.override {
        packageOverrides = self: _: {
          allure = self.callPackage ./allure.nix {};
          pytest-allure = self.callPackage ./pytest-allure.nix {};
          cardano-clusterlib = self.callPackage ./cardano-clusterlib.nix {};
          _hypothesis = self.hypothesis.overridePythonAttrs(old: rec {
            version = "6.23.2";
            doCheck = false;
            src =  super.fetchFromGitHub {
              owner = "HypothesisWorks";
              repo = "hypothesis-python";
              rev = "hypothesis-python-${version}";
              sha256 = "1mdygyq6ra4kysi0y2g3a4bgpqrcb8ci2061117zyms419qwwh4l";
            };
          });
        };
      };

      cardanoNodeShell = import (sources.cardano-node + "/shell.nix") { inherit pkgs; };

      cardanoNodeTestsShell = self.cardanoNodeShell.devops.overrideAttrs (oldAttrs: rec {
        nativeBuildInputs = oldAttrs.nativeBuildInputs ++ [ pkgs.git (pkgs.python3.withPackages (ps: with ps; [
          pytest
          allure
          cardano-clusterlib
          pytest-allure
          pytest-html
          pytest-order
          pytest_xdist
          pyyaml
          setuptools
          filelock
          pydantic
          pylint
          mypy
          _hypothesis
          cbor2
          requests
          psycopg2
          pandas
          psutil
          pymysql
        ])) ];
      });
    });


in pkgs
