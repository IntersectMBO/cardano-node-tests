{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0";
  src = fetchPypi {
    inherit pname version;
    sha256 = "Ko3y7saWdyiq9ubZD+GbCUnD4N7zcEAVPj6rcPlXGVU=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
