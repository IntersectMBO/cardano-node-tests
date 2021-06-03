{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.23";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0bm9mhhlcg6xiyxhlhlg65dbp8nqrij7wjcdjza6m4lr2g2jdrrx";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
