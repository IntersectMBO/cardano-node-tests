{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc1";
  src = fetchPypi {
    inherit pname version;
    sha256 = "60d8mabQDQGnr4jkAYT0WySEJExfysaSTBbt4rr5VYw=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
