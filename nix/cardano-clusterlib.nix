{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.30";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1i3zj4f7gxpyidzfh1lb05hy9gsyninyvwv3n7ysicfshhwf67pp";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
