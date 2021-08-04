{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.31";
  src = fetchPypi {
    inherit pname version;
    sha256 = "04s7d6s2gasc3d904wi67w7swq3d4sxdw6g2nfblqlgnd2ckm99j";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
