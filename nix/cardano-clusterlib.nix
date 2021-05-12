{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.18";
  src = fetchPypi {
    inherit pname version;
    sha256 = "0w2hy5zp1gkq2mgl87hglpayibkhkfnixmq6r1rvzdqjnmrq29sq";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
