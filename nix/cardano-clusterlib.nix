{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.32";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0ll16969vrscjr5fzks5lfvy96g9b1j5dfhkax6dfx4v5gbhaib3";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
