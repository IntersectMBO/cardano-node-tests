{ stdenv, buildPythonPackage, fetchPypi
, attrs, six, pluggy, setuptools_scm }:

buildPythonPackage rec {
  pname = "allure-python-commons";
  version = "2.9.45";
  src = fetchPypi {
    inherit pname version;
    sha256 = "wjjSiurDXox8UX2KIyfiWuW78sMLXiMT0g7xHXX1VJ0=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
  propagatedBuildInputs = [ attrs six pluggy ];
}
