{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.11";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0zhj62q894z00xchxhnq9p1hp5rc21nkq78myp7ajra08j3grcgn";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
