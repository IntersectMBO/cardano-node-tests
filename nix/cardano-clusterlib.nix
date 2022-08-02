{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc14";
  src = fetchPypi {
    inherit pname version;
    sha256 = "RG186mU6rpPc0zq07Ci2mZUwn2VGZJBol0uKazUAJ0E=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
