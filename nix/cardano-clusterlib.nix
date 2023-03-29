{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.2";
  src = fetchPypi {
    inherit pname version;
    sha256 = "PJA62sYwIKtU5yTVI6dSucxAHcoqvHsNRkna2fjB2Vo=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
