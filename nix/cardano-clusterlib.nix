{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.2.7";
  src = fetchPypi {
    inherit pname version;
    sha256 = "xpe8PCKitEO6z2Y/CBlUBbt/5d/8j1dj5aNjLV2q0FQ=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
