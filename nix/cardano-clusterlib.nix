{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.53";
  src = fetchPypi {
    inherit pname version;
    sha256 = "E9prTc8H++4StRDusPLEV8fQFLIjD2UxTAUaqblkohQ=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
