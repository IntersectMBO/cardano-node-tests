{ system ? builtins.currentSystem
, crossSystem ? null
, config ? {}
, sourcesOverride ? {}
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
            version = "6.8.8";
            doCheck = false;
            src =  super.fetchFromGitHub {
              owner = "HypothesisWorks";
              repo = "hypothesis-python";
              rev = "hypothesis-python-${version}";
              sha256 = "1y2aamla2qd4sz0alsbj740bpkq203v4malbvzci2k134g5dmvcq";
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
          pytest-ordering
          pytest_xdist
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
        ])) ];
      });
    });


in pkgs
