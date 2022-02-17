{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.0";
  src = fetchPypi {
    inherit pname version;
    sha256 = "fL9z8iDWv/oEo0oJvm4FUd359IuRr0MKXhACHmY77iI=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
