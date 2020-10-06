{ stdenv, buildPythonPackage, fetchPypi
, allure, pytest, six, setuptools_scm }:

buildPythonPackage rec {
  pname = "allure-pytest";
  version = "2.8.18";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1sxk97fwfam0m0xm48vpqlvbfb9w0n7mkhrv3hyc47wjfv98551j";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
  propagatedBuildInputs = [ allure pytest six ];
}
