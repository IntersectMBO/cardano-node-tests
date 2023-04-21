{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.5";
  src = fetchPypi {
    inherit pname version;
    sha256 = "H8e/m5VVe3lgLTkbA7XsAhKXa07UXSQpa2wHQZs30WU=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
