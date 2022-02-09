{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.52";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1kwt5lJBvik+UB0YHdKv53LQA40iHNE55vtbjk45oLg=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
