{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.0rc2";
  src = fetchPypi {
    inherit pname version;
    sha256 = "rpuGA8TCdlzwIWfynJ3dJ9PDhkLKKmzy+tvV14O+rbs=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
