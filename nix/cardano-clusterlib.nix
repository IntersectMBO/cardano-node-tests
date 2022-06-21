{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc4";
  src = fetchPypi {
    inherit pname version;
    sha256 = "cPcz/eyypbtoZLZ5FThTjdBD993tLdyFouGFb5put3k=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
