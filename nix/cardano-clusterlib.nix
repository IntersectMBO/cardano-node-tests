{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.37";
  src = fetchPypi {
    inherit pname version;
    sha256 = "08shsp4l31sqqpjpf5lx4hvvrh81nsscgl0c5njyqh54qx64phbh";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
