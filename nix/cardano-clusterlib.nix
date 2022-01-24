{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.1.50";
  src = fetchPypi {
    inherit pname version;
    sha256 = "oB5f44mkKf0heOHxSl6wNXBp8bTOv3oXQ47kDHnPxHE=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
