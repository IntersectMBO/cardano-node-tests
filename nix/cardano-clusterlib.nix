{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.4.6";
  src = fetchPypi {
    inherit pname version;
    sha256 = "x0gHsQtdU6uQofaZixwcXt0ADLMMIMs19Q9iw8PLl0s=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
