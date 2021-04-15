{
  sourcesNix ? import ./nix/sources.nix {}
, cardanoNodePkgs ? import (sourcesNix.cardano-node + "/nix") {}
, cardanoNodeShell ? import (sourcesNix.cardano-node + "/shell.nix") {}
}:

let

  pkgs = cardanoNodePkgs.extend (_: super: {
      python3 = super.python3.override {
        packageOverrides = self: _: {
          allure = self.callPackage ./nix/allure.nix {};
          pytest-allure = self.callPackage ./nix/pytest-allure.nix {};
          cardano-clusterlib = self.callPackage ./nix/cardano-clusterlib.nix {};
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
    });

  shell = cardanoNodeShell.devops.overrideAttrs (oldAttrs: rec {
    buildInputs = oldAttrs.buildInputs ++ [ pkgs.git (pkgs.python3.withPackages (ps: with ps; [
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
    ])) ];
  });

in shell
