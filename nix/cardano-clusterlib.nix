{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc6";
  src = fetchPypi {
    inherit pname version;
    sha256 = "r3kWeQn7LZ2adaI7/a8Lc0txjMi9KhEINqh0t0qmCWs=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
