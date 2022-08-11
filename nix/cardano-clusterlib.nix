{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc15";
  src = fetchPypi {
    inherit pname version;
    sha256 = "2+EhXGnNn7HiYPiTxj5AwtYZVBV8iti72Wp9T5FM2+c=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
