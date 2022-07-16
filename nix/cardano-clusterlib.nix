{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc9";
  src = fetchPypi {
    inherit pname version;
    sha256 = "C4BfVASODWkHCyHimsFxKNUvN24E46arrE54OihgHLw=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
