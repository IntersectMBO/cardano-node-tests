{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc20";
  src = fetchPypi {
    inherit pname version;
    sha256 = "K3YmrNXjgRpWe5HLJdvXmotL2tPg6z+saYrlyzKqMw0=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
