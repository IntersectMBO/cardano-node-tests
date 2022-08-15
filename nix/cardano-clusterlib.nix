{ stdenv, buildPythonPackage, fetchPypi, setuptools_scm }:

buildPythonPackage rec {
  pname = "cardano-clusterlib";
  version = "0.3.0rc16";
  src = fetchPypi {
    inherit pname version;
    sha256 = "z0ka720Rm8HheWeOCGZAqYR2c+CBQMLlrGj9TKEevrw=";
  };
  doCheck = false;
  nativeBuildInputs = [ setuptools_scm ];
}
