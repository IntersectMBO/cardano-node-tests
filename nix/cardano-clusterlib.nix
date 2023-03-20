{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.1";
  src = fetchPypi {
    inherit pname version;
    sha256 = "evJkBHcSmm+Q1SHw2CxqZNwdvHlVopDfGpRUgxXDdbI=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
