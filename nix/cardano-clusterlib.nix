{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.47";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0i7028hj9lykk937bbaxnfid2f5d9prcm8r701d39j4idz1jgjh5";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
