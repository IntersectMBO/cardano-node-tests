let
  sources = import ./nix/sources.nix {};
  pkgs = import sources.nixpkgs {
    overlays = [(_: super: {
      python3 = super.python3.override {
        packageOverrides = self: _: {
          allure = self.callPackage ./nix/allure.nix {};
          pytest-allure = self.callPackage ./nix/pytest-allure.nix {};
        };
      };
    })];
  };

  shell = (import (sources.cardano-node + "/shell.nix") {}).devops.overrideAttrs (oldAttrs: {
    buildInputs = oldAttrs.buildInputs ++ [ (pkgs.python3.withPackages (ps: with ps; [
      supervisor
      ipython
      pytest
      allure
      pytest-allure
      pytest-html
      pytest-ordering
      pytest_xdist
      setuptools
      filelock
      pylint
      mypy
      hypothesis
      cbor2
    ])) ];
  });

in shell
