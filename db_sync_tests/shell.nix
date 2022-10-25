# shell.nix
{ pkgs ? import <nixpkgs> {} }:
let
  my-python = pkgs.python3;
  python-with-my-packages = my-python.withPackages (p: with p; [
    matplotlib
    pandas
    requests
    xmltodict
    psutil
    GitPython
    pymysql
    # other python packages if needed
  ]);
in
pkgs.mkShell {
  buildInputs = [
    python-with-my-packages
    pkgs.postgresql
    pkgs.wget
    pkgs.buildkite-agent
  ];
  shellHook = ''
    PYTHONPATH=${python-with-my-packages}/${python-with-my-packages.sitePackages}
    # set more env-vars
  '';
}
