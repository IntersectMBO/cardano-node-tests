{
  cardanoNodePkgs ? import ((import ./nix/sources.nix {}).cardano-node + "/nix") {}
}:

let

  pkgs = cardanoNodePkgs.extend (_: super: {
      python3 = super.python3.override {
        packageOverrides = self: _: {
          allure = self.callPackage ./nix/allure.nix {};
          pytest-allure = self.callPackage ./nix/pytest-allure.nix {};
        };
      };
    });

  shell = cardanoNodePkgs.cardanoNodeShell.devops.overrideAttrs (oldAttrs: rec {
    buildInputs = oldAttrs.buildInputs ++ [ pkgs.git (pkgs.python3.withPackages (ps: with ps; [
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
      requests
    ])) ];
  });

in shell
