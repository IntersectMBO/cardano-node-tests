{ stdenv, buildPythonPackage, fetchPypi
, allure, pytest, six, setuptools_scm }:

buildPythonPackage rec {
  pname = "allure-pytest";
  version = "2.9.45";
  src = fetchPypi {
    inherit pname version;
    sha256 = "IGIP3gill1eLFXpg/zi9zDAOMS0S6qOM8o5KYuIr2qM=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
  propagatedBuildInputs = [ allure pytest six ];
}
