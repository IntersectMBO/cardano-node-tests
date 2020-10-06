{ stdenv, buildPythonPackage, fetchPypi
, attrs, six, pluggy, setuptools_scm }:

buildPythonPackage rec {
  pname = "allure-python-commons";
  version = "2.8.18";
  src = fetchPypi {
    inherit pname version;
    sha256 = "1n4yp6kzf5awp731p0mz6kvlcyljvxabqmhggml5zvnabm8yl6ls";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
  propagatedBuildInputs = [ attrs six pluggy ];
}
