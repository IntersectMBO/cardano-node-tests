{ stdenv, buildPythonPackage, fetchPypi, pytest }:

buildPythonPackage rec {
  pname = "pytest-select";
  version = "0.1.2";
  src = fetchPypi {
    inherit pname version;
    sha256 = "sha256-FGqq8pQDT2TMAyZEjQTAP6U98ZckjJh0cS8hqRnOQU4=";
  };
  doCheck = false;
  propagatedBuildInputs = [ pytest ];
}
