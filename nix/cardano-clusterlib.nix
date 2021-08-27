{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.38";
  src = fetchPypi {
    inherit pname version;
    sha256 = "036f96ax5kjl6zhkaw2jlk6j9n6xlbpkp9rp17haj6b33cai5bsb";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
