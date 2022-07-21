{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc12";
  src = fetchPypi {
    inherit pname version;
    sha256 = "QSWtx8qjO9yjoPyNN1ZLaBMlR5i7zFxzTj1QloYnY1Y=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
