{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc3";
  src = fetchPypi {
    inherit pname version;
    sha256 = "rhZwqSeZFpchHSCseaDI7nWkDQG6XFNL6nB97Qxx5J8=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
