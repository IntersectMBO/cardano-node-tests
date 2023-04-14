{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.4";
  src = fetchPypi {
    inherit pname version;
    sha256 = "+7mjv402ER27r+p82BXZCxTAVRudZUGCEXaVHzhWMl8=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
