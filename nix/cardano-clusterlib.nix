{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.0rc5";
  src = fetchPypi {
    inherit pname version;
    sha256 = "peM/9OYB/AYdteIMoThhz2pihKYoJ4+T0U5BH+GBgbg=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
